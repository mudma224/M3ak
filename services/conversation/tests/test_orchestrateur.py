"""Tests de l'orchestrateur — fakes injectés, sans infrastructure."""

from __future__ import annotations

import asyncio

import pytest
from m3ak_contracts import ReponseRecherche, ResultatRecherche

from app.orchestrateur import (
    ErreurRecherche,
    MorceauGeneration,
    Orchestrateur,
)

TEXTE_SOURCE = "L'article 3 prévoit un délai de trente jours."


def _resultat(score=0.9, langue="fr", texte=TEXTE_SOURCE, famille="segment"):
    return ResultatRecherche(
        unite_id="seg-1",
        famille=famille,
        document_id="doc-1",
        titre="Code des obligations",
        texte=texte,
        langue=langue,
        source="adala",
        collection="textes_normatifs",
        contexte={},
        score=score,
    )


def _recherche(resultats, rerank=True):
    return ReponseRecherche(resultats=resultats, mode="hybride", rerank=rerank, duree_ms=5)


class PersisterFactice:
    def __init__(self):
        self.messages = []

    async def __call__(self, role, contenu, langue, refus, metadonnees):
        self.messages.append(
            {"role": role, "contenu": contenu, "refus": refus, "metadonnees": metadonnees}
        )
        return f"msg-{len(self.messages)}"


def _generation_flux(morceaux: list[str], *, complet: bool | None = True, latence_ms: int = 10):
    """Faux générateur en flux : entête, morceaux au fil, puis fin."""

    async def generer_flux(messages, max_jetons=None):
        yield MorceauGeneration(entete={"fournisseur": "fictif", "modele": "m"})
        for morceau in morceaux:
            yield MorceauGeneration(texte=morceau)
        yield MorceauGeneration(fin={"complet": complet, "latence_ms": latence_ms})

    return generer_flux


def _jamais_appele():
    async def generer_flux(messages, max_jetons=None):
        raise AssertionError("le llm ne doit pas être appelé")
        yield MorceauGeneration()  # pragma: no cover

    return generer_flux


def _generation_flux_variable(entrees: list[tuple[str, bool]]):
    """Faux générateur dont chaque appel consomme l'entrée suivante
    (texte, complet) — simule la variance d'un modèle réel. Expose
    `nb_appels` : appels et budgets reçus."""

    async def generer_flux(messages, max_jetons=None):
        indice = min(etat["appels"], len(entrees) - 1)
        etat["appels"] += 1
        etat["budgets"].append(max_jetons)
        texte, complet = entrees[indice]
        yield MorceauGeneration(entete={"fournisseur": "fictif", "modele": "m"})
        yield MorceauGeneration(texte=texte)
        yield MorceauGeneration(fin={"complet": complet, "latence_ms": 10})

    etat: dict = {"appels": 0, "budgets": []}
    generer_flux.nb_appels = etat  # type: ignore[attr-defined]
    return generer_flux


def _orchestrateur(recherche, generation_flux, seuil=0.375):
    persister = PersisterFactice()
    orchestrateur = Orchestrateur(
        recherche, generation_flux, persister, seuil_appel=seuil, nb_sources=8
    )
    return orchestrateur, persister


def lance(coroutine):
    return asyncio.run(coroutine)


def _flux_en_evenements(orchestrateur, question="Question ?", session="sess-1"):
    async def parcourir():
        return [e async for e in orchestrateur.flux(question, session)]

    return lance(parcourir())


def test_generation_tronquee_est_un_refus():
    async def chercher(question):
        return _recherche([_resultat()])

    orchestrateur, persister = _orchestrateur(
        chercher,
        _generation_flux(["Réponse coupée en plein [1]."], complet=False),
    )
    reponse = lance(orchestrateur.poser("Question ?", "sess-1"))
    assert reponse.refus and reponse.motif_refus == "generation_tronquee"
    assert persister.messages[-1]["metadonnees"]["detail_verification"]


def test_reponse_sourcee_et_verifiee():
    async def chercher(question):
        assert "[PERSONNE_" not in question  # l'anonymisation précède la recherche
        return _recherche([_resultat()])

    orchestrateur, persister = _orchestrateur(
        chercher, _generation_flux(["Le délai est de trente jours [1]."])
    )
    reponse = lance(orchestrateur.poser("Quel est le délai ?", "sess-1"))
    assert not reponse.refus
    assert reponse.citations_verifiees and reponse.citations_rejetees == 0
    assert len(reponse.sources) == 1
    assert reponse.sources[0].famille == "segment"
    assert [m["role"] for m in persister.messages] == ["user", "assistant"]


def test_score_sous_le_seuil_refuse_sans_appeler_le_llm():
    async def chercher(question):
        return _recherche([_resultat(score=0.2)])

    orchestrateur, _ = _orchestrateur(chercher, _jamais_appele())
    reponse = lance(orchestrateur.poser("Question floue ?", "sess-1"))
    assert reponse.refus and reponse.motif_refus == "score_insuffisant"
    assert reponse.score_meilleur == 0.2


def test_score_dans_la_bande_genere_quand_meme():
    """Seuil d'appel franchi (0,375) mais sous l'ancien mur de 0,40 :
    la génération est tentée — le jugement revient au modèle vu les
    passages complets, pas au score du reranker (paires interlingues)."""

    async def chercher(question):
        return _recherche([_resultat(score=0.38)])

    orchestrateur, _ = _orchestrateur(
        chercher, _generation_flux(["Le délai est de trente jours [1]."])
    )
    reponse = lance(orchestrateur.poser("Question ?", "sess-1"))
    assert not reponse.refus and reponse.score_meilleur == 0.38


def test_aucune_source_est_un_refus():
    async def chercher(question):
        return _recherche([])

    orchestrateur, _ = _orchestrateur(chercher, _jamais_appele())
    reponse = lance(orchestrateur.poser("Question hors corpus ?", "sess-1"))
    assert reponse.refus and reponse.motif_refus == "aucune_source"


def test_citation_verbatim_hallucinee_est_un_refus():
    async def chercher(question):
        return _recherche([_resultat()])

    orchestrateur, persister = _orchestrateur(
        chercher, _generation_flux(["«Disposition entièrement inventée» [1]."])
    )
    reponse = lance(orchestrateur.poser("Question ?", "sess-1"))
    assert reponse.refus and reponse.motif_refus == "generation_non_verifiable"
    assert persister.messages[-1]["metadonnees"]["detail_verification"]


def test_reponse_sans_renvoi_est_un_refus():
    async def chercher(question):
        return _recherche([_resultat()])

    orchestrateur, _ = _orchestrateur(
        chercher, _generation_flux(["Une réponse sans aucune référence."])
    )
    reponse = lance(orchestrateur.poser("Question ?", "sess-1"))
    assert reponse.refus and reponse.motif_refus == "generation_non_verifiable"


def test_insuffisant_du_modele_est_un_refus():
    async def chercher(question):
        return _recherche([_resultat()])

    orchestrateur, _ = _orchestrateur(
        chercher, _generation_flux(["INSUFFISANT"])
    )
    reponse = lance(orchestrateur.poser("Question très pointue ?", "sess-1"))
    assert reponse.refus and reponse.motif_refus == "insuffisant_modele"


def test_sans_reponse_decriptive_verifiee_est_servie():
    """Aucun passage ne répond : la réponse du modèle est la DESCRIPTION
    vérifiée des passages (marqueur SANS_REPONSE retiré) — refus tracé,
    contenu utile, jamais un message système."""
    description = (
        "Les passages fournis portent sur «un délai de trente jours» [1], "
        "c'est-à-dire le délai légal. Aucun de ces passages ne répond à "
        "votre question — reformulez ou consultez un professionnel."
    )

    async def chercher(question):
        return _recherche([_resultat()])

    async def generer_flux(messages, max_jetons=None):
        yield MorceauGeneration(entete={"fournisseur": "fictif", "modele": "m"})
        yield MorceauGeneration(texte="SANS_REPONSE\n")
        yield MorceauGeneration(texte=description)
        yield MorceauGeneration(fin={"complet": True, "latence_ms": 10})

    orchestrateur, persister = _orchestrateur(chercher, generer_flux)
    reponse = lance(orchestrateur.poser("Question hors sujet ?", "sess-1"))
    assert reponse.refus and reponse.motif_refus == "insuffisant_modele"
    assert reponse.sans_reponse and not reponse.texte_partiel
    assert "SANS_REPONSE" not in reponse.reponse
    assert "«un délai de trente jours» [1]" in reponse.reponse
    assert reponse.citations_verifiees and len(reponse.sources) == 1
    persiste = persister.messages[-1]
    assert persiste["contenu"] == reponse.reponse and persiste["refus"]


def test_sans_reponse_sans_citation_sur_un_passage_est_rejetee_sans_escalade():
    """La couverture descriptive est mécanique : un passage cité sans
    citation vérifiable EN LUI rend la description invalide — et une
    tentative descriptive ne consomme JAMAIS d'escalade (1 appel max)."""

    async def chercher(question):
        return _recherche([_resultat()])

    async def generer_flux(messages, max_jetons=None):
        yield MorceauGeneration(entete={"fournisseur": "fictif", "modele": "m"})
        yield MorceauGeneration(
            texte="SANS_REPONSE\nLe passage [1] parle du délai de trente jours, "
            "aucun ne répond à la question."
        )
        yield MorceauGeneration(fin={"complet": True, "latence_ms": 10})

    etat = {"appels": 0}

    async def generer_compte(messages, max_jetons=None):
        etat["appels"] += 1
        async for morceau in generer_flux(messages, max_jetons=max_jetons):
            yield morceau

    orchestrateur, _ = _orchestrateur(chercher, generer_compte)
    reponse = lance(orchestrateur.poser("Question ?", "sess-1"))
    assert reponse.refus
    # Prose descriptive sans citation : phrases vérifiées diffusées en
    # continu → réponse partielle (le refus sec n'a jamais de contenu)…
    assert reponse.motif_refus == "generation_non_verifiable"
    # …et surtout : une seule tentative, pas d'escalade en mode descriptif.
    assert etat["appels"] == 1


def test_insuffisant_apres_phrases_verifiees_garde_le_texte():
    """Le modèle produit des phrases vérifiées PUIS son échappatoire : le
    texte vérifié reste servi (réponse partielle), l'échappatoire jamais."""
    phrase = "«L'article 3 prévoit un délai de trente jours.» [1]\n\n"

    async def chercher(question):
        return _recherche([_resultat()])

    async def generer_flux(messages, max_jetons=None):
        yield MorceauGeneration(entete={"fournisseur": "fictif", "modele": "m"})
        yield MorceauGeneration(texte=phrase)  # phrase vérifiée, diffusée
        yield MorceauGeneration(texte="INSUFFISANT")  # échappatoire, jamais
        yield MorceauGeneration(fin={"complet": True, "latence_ms": 10})

    orchestrateur, persister = _orchestrateur(chercher, generer_flux)
    evenements = _flux_en_evenements(orchestrateur, "Question ?")
    fragments = [e.texte for e in evenements if e.type == "fragment"]
    assert fragments  # la phrase vérifiée a été diffusée en continu
    fin = evenements[-1]
    assert fin.reponse.refus and fin.reponse.motif_refus == "insuffisant_modele"
    assert fin.reponse.reponse == phrase.strip()
    assert "INSUFFISANT" not in fin.reponse.reponse
    assert len(fin.reponse.sources) == 1
    assert fin.reponse.citations_verifiees
    # Persistance : contenu réel, refus tracé
    persiste = persister.messages[-1]
    assert persiste["contenu"] == phrase.strip() and persiste["refus"]
    assert persiste["metadonnees"]["motif_refus"] == "insuffisant_modele"


def test_renvoi_invalide_est_rejete_pas_destructif():
    async def chercher(question):
        return _recherche([_resultat()])

    orchestrateur, _ = _orchestrateur(
        chercher,
        _generation_flux(["Le délai est de trente jours [1] et aussi [9]."]),
    )
    reponse = lance(orchestrateur.poser("Question ?", "sess-1"))
    assert not reponse.refus
    assert reponse.citations_rejetees == 1
    assert "[9]" not in reponse.reponse and "[1]" in reponse.reponse


def test_sources_en_autre_langue_activent_le_repli_interlingue():
    async def chercher(question):
        return _recherche([_resultat(langue="ar")])

    orchestrateur, _ = _orchestrateur(
        chercher, _generation_flux(["Le délai est de trente jours [1]."])
    )
    reponse = lance(orchestrateur.poser("Quel est le délai ?", "sess-1"))
    assert reponse.repli_interlingue


def test_erreur_recherche_traverse_le_pipeline():
    async def chercher(question):
        raise ErreurRecherche("boom")

    orchestrateur, _ = _orchestrateur(chercher, _jamais_appele())
    with pytest.raises(ErreurRecherche):
        lance(orchestrateur.poser("Question ?", "sess-1"))


def test_flux_produit_statut_sources_fragments_fin():
    async def chercher(question):
        return _recherche([_resultat()])

    orchestrateur, _ = _orchestrateur(
        chercher, _generation_flux(["Réponse vérifiée [1]."])
    )
    evenements = _flux_en_evenements(orchestrateur, "Question ?")
    types = [e.type for e in evenements]
    assert types[0] == "statut"
    assert "sources" in types
    assert types[-1] == "fin"
    assert [e.texte for e in evenements if e.type == "fragment"]
    assert evenements[-1].reponse.reponse == "Réponse vérifiée [1]."


# ── Émission progressive (vérification cumulative phrase à phrase) ────────


def test_flux_emet_chaque_phrase_des_quelle_est_verifiee():
    async def chercher(question):
        return _recherche([_resultat()])

    orchestrateur, _ = _orchestrateur(
        chercher,
        _generation_flux(
            [
                "Le délai est de ",
                "trente jours [1]. ",
                "En cas de litige, ",
                "un recours existe [1].",
            ]
        ),
    )
    evenements = _flux_en_evenements(orchestrateur, "Question ?")
    fragments = [e.texte for e in evenements if e.type == "fragment"]
    # Chaque phrase complète est émise dès sa vérification — pas de bloc
    # unique à la fin du flux (l'espace séparateur ouvre la phrase suivante).
    assert fragments == [
        "Le délai est de trente jours [1].",
        " En cas de litige, un recours existe [1].",
    ]
    fin = evenements[-1]
    assert fin.type == "fin" and not fin.reponse.refus
    assert (
        fin.reponse.reponse
        == "Le délai est de trente jours [1]. En cas de litige, un recours existe [1]."
    )


def test_phrase_inverifiable_stoppe_le_flux_et_sert_le_verifie():
    async def chercher(question):
        return _recherche([_resultat()])

    orchestrateur, _ = _orchestrateur(
        chercher,
        _generation_flux(
            [
                "Le délai est de trente jours [1]. ",
                "«Disposition inventée» [1]. ",
                "La suite jamais diffusée [1].",
            ]
        ),
    )
    evenements = _flux_en_evenements(orchestrateur, "Question ?")
    fragments = [e.texte for e in evenements if e.type == "fragment"]
    # Seule la phrase vérifiée a été émise ; la suite est coupée.
    assert fragments == ["Le délai est de trente jours [1]."]
    fin = evenements[-1]
    assert fin.type == "fin" and fin.reponse.refus
    assert fin.reponse.motif_refus == "generation_non_verifiable"
    # Le texte vérifié déjà diffusé reste servi (réponse partielle) —
    # détruire ce que l'utilisateur a vu s'afficher est pire qu'une
    # réponse incomplète ; la phrase invérifiable, elle, n'est jamais servie.
    assert fin.reponse.reponse == "Le délai est de trente jours [1]."
    assert len(fin.reponse.sources) == 1


def test_insuffisant_n_est_jamais_diffuse_en_fragment():
    async def chercher(question):
        return _recherche([_resultat()])

    orchestrateur, _ = _orchestrateur(chercher, _generation_flux(["INSUFFISANT"]))
    evenements = _flux_en_evenements(orchestrateur, "Question ?")
    assert not [e for e in evenements if e.type == "fragment"]
    fin = evenements[-1]
    assert fin.reponse.refus and fin.reponse.motif_refus == "insuffisant_modele"


def test_tronquee_apres_fragments_emis_devient_reponse_partielle():
    async def chercher(question):
        return _recherche([_resultat()])

    orchestrateur, _ = _orchestrateur(
        chercher,
        _generation_flux(["Le délai est de trente jours [1]. "], complet=False),
    )
    evenements = _flux_en_evenements(orchestrateur, "Question ?")
    assert [e for e in evenements if e.type == "fragment"]  # du texte a été émis
    fin = evenements[-1]
    assert fin.reponse.refus and fin.reponse.motif_refus == "generation_tronquee"
    # Le texte vérifié diffusé reste servi en réponse partielle.
    assert fin.reponse.reponse == "Le délai est de trente jours [1]."


# ── Nouvelle tentative sur citation déformée (variance des modèles) ────────


def test_citation_deformee_regenere_une_fois_et_reussit():
    async def chercher(question):
        return _recherche([_resultat()])

    generateur = _generation_flux_variable(
        [
            ("«Disposition inventée» [1].", True),
            ("Le délai est de trente jours [1].", True),
        ]
    )
    orchestrateur, persister = _orchestrateur(chercher, generateur)
    reponse = lance(orchestrateur.poser("Question ?", "sess-1"))
    assert not reponse.refus
    assert reponse.reponse == "Le délai est de trente jours [1]."
    assert generateur.nb_appels["appels"] == 2
    # La réponse persistée est celle de la seconde tentative.
    assert persister.messages[-1]["contenu"] == "Le délai est de trente jours [1]."


def test_citation_deformee_regenere_et_echoue_reponse_partielle():
    """Les deux tentatives échouent sur une citation invérifiable : ce que
    l'utilisateur a vu (phrases vérifiées) reste servi, jamais détruit."""

    async def chercher(question):
        return _recherche([_resultat()])

    # Morceaux séparés comme dans un flux réel : la phrase vérifiée est
    # diffusée AVANT l'arrivée de la phrase fautive.
    textes_par_appel = [
        ["Le délai est de trente jours [1]. ", "«Inventée» [1]."],
        ["«Encore inventée» [1]."],
    ]
    etat = {"appels": 0}

    async def generer_flux(messages, max_jetons=None):
        morceaux = textes_par_appel[min(etat["appels"], len(textes_par_appel) - 1)]
        etat["appels"] += 1
        yield MorceauGeneration(entete={"fournisseur": "fictif", "modele": "m"})
        for morceau in morceaux:
            yield MorceauGeneration(texte=morceau)
        yield MorceauGeneration(fin={"complet": True, "latence_ms": 10})
    orchestrateur, persister = _orchestrateur(chercher, generer_flux)
    reponse = lance(orchestrateur.poser("Question ?", "sess-1"))
    assert reponse.refus and reponse.motif_refus == "generation_non_verifiable"
    assert reponse.reponse == "Le délai est de trente jours [1]."
    assert etat["appels"] == 2
    persiste = persister.messages[-1]
    assert persiste["contenu"] == "Le délai est de trente jours [1]."
    assert persiste["metadonnees"]["detail_verification"]


def test_deux_citations_deformees_refusent_apres_la_seconde_tentative():
    async def chercher(question):
        return _recherche([_resultat()])

    generateur = _generation_flux_variable(
        [
            ("«Disposition inventée» [1].", True),
            ("«Autre invention» [1].", True),
        ]
    )
    orchestrateur, _ = _orchestrateur(chercher, generateur)
    reponse = lance(orchestrateur.poser("Question ?", "sess-1"))
    assert reponse.refus and reponse.motif_refus == "generation_non_verifiable"
    assert generateur.nb_appels["appels"] == 2


# ── Troncature : régénération à budget doublé (modèles « thinking ») ───────


def test_tronquee_regenere_avec_budget_double_et_reussit():
    async def chercher(question):
        return _recherche([_resultat()])

    generateur = _generation_flux_variable(
        [
            ("Réponse coupée en plein [1].", False),
            ("Le délai est de trente jours [1].", True),
        ]
    )
    orchestrateur, _ = _orchestrateur(chercher, generateur)
    reponse = lance(orchestrateur.poser("Question ?", "sess-1"))
    assert not reponse.refus
    assert reponse.reponse == "Le délai est de trente jours [1]."
    assert generateur.nb_appels["appels"] == 2
    # Budget par défaut puis double.
    assert generateur.nb_appels["budgets"] == [4096, 8192]


def test_tronquee_recurrente_refuse_apres_budget_double():
    async def chercher(question):
        return _recherche([_resultat()])

    generateur = _generation_flux_variable([("Réponse coupée [1].", False)])
    orchestrateur, _ = _orchestrateur(chercher, generateur)
    reponse = lance(orchestrateur.poser("Question ?", "sess-1"))
    assert reponse.refus and reponse.motif_refus == "generation_tronquee"
    assert generateur.nb_appels["appels"] == 2
    assert generateur.nb_appels["budgets"] == [4096, 8192]
