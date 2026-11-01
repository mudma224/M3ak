"""Orchestrateur du pipeline d'une question — le cœur métier de la plateforme.

Chaîne actée : langue par message → anonymisation (placeholders, sans
re-substitution) → recherche hybride → seuil d'appel (en dessous, aucun
appel LLM : passages trop lointains, refus score_insuffisant) → prompt
sous délimiteurs stricts → génération EN FLUX, vérifiée phrase à phrase
(aucun texte n'est diffusé sans avoir été vérifié contre les passages).
Deux issues normales : la réponse vérifiée, ou — quand aucun passage ne
répond — la description VÉRIFIÉE de ce que contiennent les passages
consultés (SANS_REPONSE : l'utilisateur voit ce qui a été trouvé et
pourquoi cela ne répond pas, jamais un message système à la place d'un
contenu). Un échec final après diffusion conserve le texte vérifié
affiché (réponse partielle) ; sans texte diffusé, refus classique. →
avertissements contextuels. Le service informe et oriente ; il ne
remplace ni l'avocat, ni le juge, ni l'administration.

Deux modes : `flux` (événements SSE) et `poser` (bloc, pour le harnais
du jeu doré — les deux découlent du même pipeline). Toute
indisponibilité est convertie en exception typée : le point d'entrée
décide du code HTTP propre.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from m3ak_contracts import (
    EvenementConversation,
    MessageGeneration,
    ReponseQuestion,
    ReponseRecherche,
    SourceCitee,
)

from .anonymisation import anonymiser
from .avertissements import detecter_avertissements
from .citations import verifier_citations
from .langue import detecter_langue
from .prompt import INSUFFISANT, RAPPEL_VERBATIM, SANS_REPONSE, assembler_messages

# Au-delà de cette taille sans frontière de phrase, le tampon est coupé au
# dernier espace : l'affichage ne peut pas rester bloqué sur un modèle qui
# écrirait un « paragraphe » sans aucune ponctuation finale.
_TAMPON_MAX = 1500


@dataclass
class MorceauGeneration:
    """Un morceau du flux du fournisseur de génération (`generer_flux`).

    `entete` : porté par l'événement `debut` du service llm (fournisseur,
    modèle) ; `texte` : un fragment de contenu filtré ; `fin` : porté par
    l'événement `fin` (usage, latence_ms, complet)."""

    texte: str = ""
    entete: dict[str, Any] | None = None
    fin: dict[str, Any] | None = None


GenerateurFlux = Callable[[list], AsyncIterator[MorceauGeneration]]


def _frontiere_phrase(tampon: str) -> int | None:
    """Index juste après la dernière phrase complète du tampon, None si le
    tampon ne contient aucune frontière (. ! ? … ؟ retour à la ligne,
    suivis d'un espace ou placés en fin)."""
    meilleure: int | None = None
    for index, caractere in enumerate(tampon):
        if caractere in ".!?…؟\n" and (
            index + 1 >= len(tampon) or tampon[index + 1].isspace()
        ):
            meilleure = index + 1
    if meilleure is None and len(tampon) > _TAMPON_MAX:
        coupe = tampon.rfind(" ")
        if coupe > 0:
            return coupe + 1
    return meilleure

REFUS_REPONSES = {
    "ar": (
        "لم أجد في المدونة الوثائقية للمنصة مصادر كافية للإجابة عن هذا السؤال "
        "بشكل موثوق. حاول إعادة صياغة السؤال، أو استشر مختصا (محام، إدارة مختصة). "
        "المنصة تطلع وترشد ولا تحل محل القانون أو المختصين."
    ),
    "fr": (
        "Je n'ai pas trouvé dans le corpus de la plateforme de sources "
        "suffisamment pertinentes pour répondre de façon fiable à cette question. "
        "Reformulez votre question ou consultez un professionnel (avocat, "
        "administration compétente). M3ak informe et oriente ; elle ne remplace "
        "ni le juge, ni l'avocat, ni l'administration."
    ),
}


class ErreurRecherche(RuntimeError):
    """Le service recherche est indisponible ou a répondu une erreur."""


class ErreurGeneration(RuntimeError):
    """Le service llm a échoué (indisponible, quota, charge invalide)."""

    def __init__(self, message: str, statut_http: int = 502) -> None:
        super().__init__(message)
        self.statut_http = statut_http


class Orchestrateur:
    """Dépendances injectées pour la testabilité : `chercher` interroge le
    service recherche, `generer_flux` transmet les morceaux du fournisseur
    au fil de leur arrivée, `enregistrer` persiste un message et retourne
    son identifiant."""

    def __init__(
        self,
        chercher,
        generer_flux: GenerateurFlux,
        enregistrer,
        seuil_appel: float = 0.375,
        nb_sources: int = 8,
        max_caracteres_passage: int = 2000,
        max_jetons: int = 4096,
    ) -> None:
        self._chercher = chercher
        self._generer_flux = generer_flux
        self._enregistrer = enregistrer
        self._seuil_appel = seuil_appel
        self._nb_sources = nb_sources
        self._max_caracteres_passage = max_caracteres_passage
        self._max_jetons = max_jetons

    async def poser(self, question: str, session_id: str) -> ReponseQuestion:
        """Mode bloc : le pipeline complet, réponse vérifiée à la fin."""
        async for evenement in self.flux(question, session_id):
            if evenement.type == "fin":
                assert evenement.reponse is not None
                return evenement.reponse
        raise RuntimeError("pipeline terminé sans réponse")

    async def flux(
        self, question: str, session_id: str
    ) -> AsyncIterator[EvenementConversation]:
        """Mode flux : les événements SSE du pipeline. Les exceptions
        (ErreurRecherche, ErreurGeneration) traversent — le point d'entrée
        les convertit (code HTTP avant ouverture, événement `erreur` après)."""
        debut = time.perf_counter()

        langue = detecter_langue(question)
        question_anonymisee, nb_noms = anonymiser(question)
        question_id = await self._enregistrer("user", question, langue, False, None)

        yield EvenementConversation(type="statut", statut="recherche")
        reponse_recherche = await self._chercher(question_anonymisee)
        resultats = reponse_recherche.resultats

        if not resultats:
            yield await self._cloturer_refus(
                session_id, question_id, langue, "aucune_source", None,
                nb_noms, reponse_recherche, debut,
            )
            return
        if reponse_recherche.rerank and resultats[0].score < self._seuil_appel:
            yield await self._cloturer_refus(
                session_id, question_id, langue, "score_insuffisant",
                resultats[0].score, nb_noms, reponse_recherche, debut,
            )
            return

        retenus = resultats[: self._nb_sources]
        passages: dict[int, str] = {}
        bloc_prompt: list[tuple[int, str, str, str]] = []
        for rang, resultat in enumerate(retenus, start=1):
            texte = resultat.texte[: self._max_caracteres_passage]
            passages[rang] = texte
            bloc_prompt.append((rang, resultat.titre, resultat.source, texte))
        sources = [self._source_citee(resultat) for resultat in retenus]
        repli = langue not in {str(r.langue) for r in retenus}

        yield EvenementConversation(type="statut", statut="generation")
        yield EvenementConversation(type="sources", sources=sources)

        messages = assembler_messages(question_anonymisee, bloc_prompt, langue)

        # Génération en flux : chaque phrase complète est vérifiée
        # cumulativement contre les passages AVANT diffusion ; la
        # vérification stricte finale porte toujours sur le texte complet
        # (≥ 1 renvoi, verbatim — et, pour une SANS_REPONSE, une citation
        # retrouvée dans CHAQUE passage décrit). Deux escalades possibles
        # sur une tentative de réponse, une fois chacune : citation
        # déformée (variance des modèles) → rappel strict de copie ;
        # troncature (les modèles « thinking » consomment le budget en
        # raisonnement) → budget doublé. PAS d'escalade sur une tentative
        # descriptive (SANS_REPONSE) : l'orientation n'a pas à consommer
        # plusieurs appels. Si un échec final survient alors que des
        # phrases vérifiées ONT ÉTÉ DIFFUSÉES, elles restent servies comme
        # « réponse partielle » — détruire un texte vérifié que
        # l'utilisateur a vu s'afficher est pire qu'une réponse incomplète
        # (demande du porteur, 2026-10-07) ; la garantie §7.1 reste
        # entière : seules des phrases vérifiées sont jamais servies.
        messages_actuels = messages
        budget = self._max_jetons
        rappel_fait = False
        budget_double = False
        resultat: dict[str, Any] = {}
        emis_diffuse = ""  # tout ce que l'utilisateur a vu s'afficher
        for tentative in (1, 2, 3):
            resultat = {
                "motif": None,
                "detail": None,
                "emis": "",
                "descriptif": False,
                "entete": {},
                "fin_flux": {},
                "citations_rejetees": 0,
                "detail_verification": None,
            }
            # Tentative 1 diffusée en continu ; les suivantes tamponnées —
            # si l'une d'elles réussit, le texte final (complet) remplace
            # celui qui est affiché.
            async for fragment in self._parcours_generation(
                messages_actuels,
                passages,
                resultat,
                diffuser=(tentative == 1),
                budget=budget,
            ):
                emis_diffuse += fragment
                yield EvenementConversation(type="fragment", texte=fragment)
            motif = resultat["motif"]
            if motif is None:
                break
            if resultat["descriptif"]:
                # Tentative descriptive : aucune escalade — si la
                # description échoue à la vérification, on clôt (réponse
                # partielle ou refus classique), sans relancer le modèle.
                break
            if motif == "non_verifiable" and not rappel_fait:
                rappel_fait = True
                messages_actuels = messages + [
                    MessageGeneration(role="system", contenu=RAPPEL_VERBATIM)
                ]
                continue
            if motif == "tronquee" and not budget_double:
                budget_double = True
                budget = budget * 2
                continue
            break

        motifs_refus = {
            "insuffisant": "insuffisant_modele",
            "tronquee": "generation_tronquee",
            "non_verifiable": "generation_non_verifiable",
        }
        if resultat["motif"] is not None:
            motif_refus = motifs_refus[resultat["motif"]]
            if emis_diffuse.strip():
                # Réponse partielle : le texte diffusé ne contient que des
                # phrases vérifiées mot à mot (la phrase fautive, elle,
                # n'a jamais été diffusée). Le refus reste tracé.
                avertissements = detecter_avertissements(question, langue)
                reponse = ReponseQuestion(
                    session_id=session_id,
                    message_id=question_id,  # provisoire ; remplacé après persistance
                    reponse=emis_diffuse.strip(),
                    langue_question=langue,
                    refus=True,
                    motif_refus=motif_refus,
                    sources=sources,
                    citations_verifiees=True,
                    citations_rejetees=resultat["citations_rejetees"],
                    avertissements=avertissements,
                    repli_interlingue=repli,
                    texte_partiel=True,
                    score_meilleur=retenus[0].score,
                    fournisseur=resultat["entete"].get("fournisseur"),
                    modele=resultat["entete"].get("modele"),
                    latence_generation_ms=int(resultat["fin_flux"].get("latence_ms") or 0),
                    duree_ms=int((time.perf_counter() - debut) * 1000),
                )
                metadonnees = self._metadonnees(
                    reponse, nb_noms, reponse_recherche, resultat["detail"]
                )
                reponse.message_id = await self._enregistrer(
                    "assistant", reponse.reponse, langue, True, metadonnees
                )
                yield EvenementConversation(type="fin", reponse=reponse)
                return
            # Aucun texte diffusé : refus classique (message digne, aucun
            # texte inventé, traçabilité complète).
            yield await self._cloturer_refus(
                session_id, question_id, langue, motif_refus,
                retenus[0].score, nb_noms, reponse_recherche, debut,
                detail=resultat["detail"],
            )
            return

        if resultat.get("descriptif"):
            # SANS_REPONSE vérifiée : la réponse est la DESCRIPTION des
            # passages consultés — chacun cité et vérifié en lui — suivie
            # de la conclusion du modèle (aucun ne répond). Une vraie
            # réponse d'orientation, jamais un message système. `refus`
            # reste vrai (aucune réponse fiable produite — les métriques
            # du jeu doré restent comparables) ; `sans_reponse` porte le
            # cas à l'affichage.
            avertissements = detecter_avertissements(question, langue)
            reponse = ReponseQuestion(
                session_id=session_id,
                message_id=question_id,  # provisoire ; remplacé après persistance
                reponse=resultat["emis"],
                langue_question=langue,
                refus=True,
                motif_refus="insuffisant_modele",
                sources=sources,
                citations_verifiees=True,
                citations_rejetees=resultat["citations_rejetees"],
                avertissements=avertissements,
                repli_interlingue=repli,
                sans_reponse=True,
                score_meilleur=retenus[0].score,
                fournisseur=resultat["entete"].get("fournisseur"),
                modele=resultat["entete"].get("modele"),
                latence_generation_ms=int(resultat["fin_flux"].get("latence_ms") or 0),
                duree_ms=int((time.perf_counter() - debut) * 1000),
            )
            metadonnees = self._metadonnees(
                reponse, nb_noms, reponse_recherche, resultat["detail_verification"]
            )
            reponse.message_id = await self._enregistrer(
                "assistant", reponse.reponse, langue, True, metadonnees
            )
            yield EvenementConversation(type="fin", reponse=reponse)
            return

        avertissements = detecter_avertissements(question, langue)
        reponse = ReponseQuestion(
            session_id=session_id,
            message_id=question_id,  # provisoire ; remplacé après persistance
            reponse=resultat["emis"],
            langue_question=langue,
            refus=False,
            sources=sources,
            citations_verifiees=True,
            citations_rejetees=resultat["citations_rejetees"],
            avertissements=avertissements,
            repli_interlingue=repli,
            score_meilleur=retenus[0].score,
            fournisseur=resultat["entete"].get("fournisseur"),
            modele=resultat["entete"].get("modele"),
            latence_generation_ms=int(resultat["fin_flux"].get("latence_ms") or 0),
            duree_ms=int((time.perf_counter() - debut) * 1000),
        )
        metadonnees = self._metadonnees(
            reponse, nb_noms, reponse_recherche, resultat["detail_verification"]
        )
        reponse.message_id = await self._enregistrer(
            "assistant",
            resultat["emis"],
            langue,
            False,
            metadonnees,
        )
        yield EvenementConversation(type="fin", reponse=reponse)

    async def _parcours_generation(
        self,
        messages: list[MessageGeneration],
        passages: dict[int, str],
        resultat: dict[str, Any],
        diffuser: bool,
        budget: int,
    ) -> AsyncIterator[str]:
        """Une tentative de génération vérifiée phrase à phrase. Diffuse
        (ou tamponne, selon `diffuser`) les fragments vérifiés et écrit dans
        `resultat` : motif (None si succès), detail, emis (texte final),
        descriptif (vrai si la réponse est une SANS_REPONSE — description
        des passages consultés), entete, fin_flux, citations_rejetees,
        detail_verification."""
        emis = ""
        tampon = ""
        entete: dict[str, Any] = {}
        fin_flux: dict[str, Any] = {}
        insuffisant = False
        descriptif = False

        async for morceau in self._generer_flux(messages, max_jetons=budget):
            if morceau.entete is not None:
                entete = morceau.entete
            if morceau.fin is not None:
                fin_flux = morceau.fin
            if not morceau.texte:
                continue
            tampon += morceau.texte
            # Réponse « sans réponse » : le marqueur ouvre la réponse ; il
            # est retiré, la suite (description des passages) suit le
            # pipeline de vérification normal.
            if (
                not emis
                and not descriptif
                and tampon.lstrip().startswith(SANS_REPONSE)
            ):
                descriptif = True
                tampon = tampon.lstrip()[len(SANS_REPONSE):].lstrip(":-–— \n")
                continue
            if tampon.strip().rstrip(".!…؟") == INSUFFISANT:
                insuffisant = True
                break
            while True:
                coupe = _frontiere_phrase(tampon)
                if coupe is None:
                    break
                phrase, tampon = tampon[:coupe], tampon[coupe:]
                if not phrase.strip():
                    continue
                controle = verifier_citations(
                    emis + phrase, passages, exiger_renvoi=False
                )
                if not controle.valide:
                    resultat.update(
                        motif="non_verifiable", detail=controle.motif, emis=emis
                    )
                    return
                if diffuser:
                    yield phrase
                emis += phrase

        resultat.update(entete=entete, fin_flux=fin_flux, descriptif=descriptif)
        if fin_flux.get("complet") is False:
            resultat.update(
                motif="tronquee",
                detail="budget de jetons de génération atteint — texte coupé",
                emis=emis,
            )
            return
        if insuffisant or tampon.strip().rstrip(".!…؟") == INSUFFISANT:
            resultat.update(motif="insuffisant", emis=emis)
            return
        if tampon.strip():
            # Dernier tronçon sans ponctuation finale.
            controle = verifier_citations(emis + tampon, passages, exiger_renvoi=False)
            if not controle.valide:
                resultat.update(
                    motif="non_verifiable", detail=controle.motif, emis=emis
                )
                return
            if diffuser:
                yield tampon
            emis += tampon

        verification = verifier_citations(
            emis, passages, exiger_couverture=descriptif
        )
        if not verification.valide:
            resultat.update(
                motif="non_verifiable", detail=verification.motif, emis=emis
            )
            return
        resultat.update(
            emis=verification.texte,
            citations_rejetees=len(verification.ids_rejetes),
            detail_verification=verification.motif,
        )

    # ── Internes ─────────────────────────────────────────────────────────

    def _source_citee(self, resultat) -> SourceCitee:
        return SourceCitee(
            unite_id=resultat.unite_id,
            famille=resultat.famille,
            document_id=resultat.document_id,
            titre=resultat.titre,
            source=resultat.source,
            collection=resultat.collection,
            langue=str(resultat.langue),
            score=round(resultat.score, 4),
        )

    async def _cloturer_refus(
        self,
        session_id: str,
        question_id: str,
        langue: str,
        motif: str,
        score_meilleur: float | None,
        nb_noms: int,
        reponse_recherche: ReponseRecherche,
        debut: float,
        detail: str | None = None,
    ) -> EvenementConversation:
        """Refus explicite — message digne, aucun texte inventé, traçabilité complète."""
        reponse = ReponseQuestion(
            session_id=session_id,
            message_id=question_id,
            reponse=REFUS_REPONSES[langue],
            langue_question=langue,
            refus=True,
            motif_refus=motif,
            score_meilleur=score_meilleur,
            duree_ms=int((time.perf_counter() - debut) * 1000),
        )
        metadonnees = self._metadonnees(reponse, nb_noms, reponse_recherche, detail)
        reponse.message_id = await self._enregistrer(
            "assistant",
            reponse.reponse,
            langue,
            True,
            metadonnees,
        )
        return EvenementConversation(type="fin", reponse=reponse)

    def _metadonnees(
        self,
        reponse: ReponseQuestion,
        nb_noms: int,
        reponse_recherche: ReponseRecherche,
        detail_verification: str | None,
    ) -> dict[str, Any]:
        return {
            "sources": [s.model_dump() for s in reponse.sources],
            "avertissements": reponse.avertissements,
            "refus": reponse.refus,
            "motif_refus": reponse.motif_refus,
            "detail_verification": detail_verification,
            "citations_verifiees": reponse.citations_verifiees,
            "citations_rejetees": reponse.citations_rejetees,
            "repli_interlingue": reponse.repli_interlingue,
            "texte_partiel": reponse.texte_partiel,
            "sans_reponse": reponse.sans_reponse,
            "score_meilleur": reponse.score_meilleur,
            "fournisseur": reponse.fournisseur,
            "modele": reponse.modele,
            "latence_generation_ms": reponse.latence_generation_ms,
            "nb_noms_anonymises": nb_noms,
            "mode_recherche": reponse_recherche.mode,
            "rerank": reponse_recherche.rerank,
            "cache_recherche": reponse_recherche.cache,
        }
