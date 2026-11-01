"""Moteur de recherche hybride (phase 2).

Plein texte (ts_rank) + vectoriel (distance cosinus BGE-M3), fusionnés par
Reciprocal Rank Fusion (k = 60, standard du domaine) au sein de chaque
famille, puis comparés entre familles (mêmes paramètres de fusion).
Dégradation gracieuse : serveur d'embeddings indisponible → recherche
plein texte seul, mode « texte », sans mise en cache (pour ne pas figer un
résultat dégradé pendant le TTL).
"""

from __future__ import annotations

import logging
import time

from m3ak_contracts import (
    FAMILLES_UNITE,
    SOURCES_PAR_FAMILLE,
    ReponseRecherche,
    ResultatRecherche,
)

from . import db
from .cache import CacheRecherche
from .embeddings import ClientTEI
from .rerank import appliquer_reclassement
from .texte import normaliser

logger = logging.getLogger(__name__)

K_RRF = 60
CANDIDATS = 100  # récupération large avant fusion
CANDIDATS_RERANK = 40  # pool diversifié envoyé au reranker (choix du porteur ; ~16 s CPU mesurées pour 40×800 car)


def fusion_rrf(listes: list[list[dict]], k: int = K_RRF) -> list[dict]:
    """Fusion des classements : score = Σ 1/(k + rang + 1) sur chaque liste
    où l'unité apparaît. Les scores sont comparables d'une famille à l'autre."""
    scores: dict[str, float] = {}
    unites: dict[str, dict] = {}
    for liste in listes:
        for rang, unite in enumerate(liste):
            cle = unite["unite_id"]
            scores[cle] = scores.get(cle, 0.0) + 1.0 / (k + rang + 1)
            unites.setdefault(cle, unite)
    ordonnes = sorted(scores.items(), key=lambda paire: paire[1], reverse=True)
    return [{**unites[cle], "score": score} for cle, score in ordonnes]


def dedupliquer_passages(resultats: list[dict]) -> list[dict]:
    """Note chaque décision au maximum de ses passages.

    Les fenêtres d'une même décision ne doivent pas se concurrencer : une
    décision est représentée par son meilleur passage — de préférence son
    résumé quand son score est proche — et la citation restitue de toute
    façon la décision entière. Seuls les passages sont dédupliqués ; l'ordre
    par score décroissant est conservé.
    """
    groupes: dict[str, list[dict]] = {}
    autres: list[dict] = []
    for unite in resultats:
        cle = (unite.get("contexte") or {}).get("decision_id")
        if unite["famille"] == "passage" and cle:
            groupes.setdefault(cle, []).append(unite)
        else:
            autres.append(unite)
    representants: list[dict] = []
    for passages_decision in groupes.values():
        meilleur = max(passages_decision, key=lambda u: u["score"])
        resume = next(
            (
                u
                for u in passages_decision
                if (u.get("contexte") or {}).get("type_contenu") == "resume"
            ),
            None,
        )
        if resume is not None and resume["score"] >= meilleur["score"] - 0.05:
            representant = resume
        else:
            representant = meilleur
        representants.append(representant)
    return sorted(autres + representants, key=lambda u: u["score"], reverse=True)


def entrelacer_par_famille(listes_par_famille: dict[str, list[dict]], top_k: int) -> list[dict]:
    """Entrelacement round-robin : le meilleur de chaque famille à tour de
    rôle, puis le suivant de chaque famille, jusqu'à top_k.

    Une fusion par score global seul laisse une famille dont les deux
    systèmes (plein texte et vectoriel) sont d'accord monopoliser le
    top-k et en évincer les autres — le pool de candidats doit rester
    diversifié (le reranking de l'étape 2 re-classe ce pool). L'ordre RRF
    est conservé au sein de chaque famille.
    """
    fusionnes: list[dict] = []
    rangs = {famille: 0 for famille in listes_par_famille}
    while len(fusionnes) < top_k:
        progres = False
        for famille, liste in listes_par_famille.items():
            if len(fusionnes) >= top_k:
                break
            rang = rangs[famille]
            if rang < len(liste):
                fusionnes.append(liste[rang])
                rangs[famille] = rang + 1
                progres = True
        if not progres:
            break
    return fusionnes


class MoteurRecherche:
    def __init__(self, tei: ClientTEI, cache: CacheRecherche, reranker) -> None:
        self._tei = tei
        self._cache = cache
        self._reranker = reranker

    async def rechercher(
        self, question: str, top_k: int, familles: list[str] | None
    ) -> ReponseRecherche:
        debut = time.perf_counter()
        question_normalisee = normaliser(question)

        cle = CacheRecherche.cle(question_normalisee, top_k, tuple(familles or ()))
        en_cache = await self._cache.obtenir(cle)
        if en_cache is not None:
            en_cache.pop("cache", None)
            reponse = ReponseRecherche(**en_cache, cache=True)
            reponse.duree_ms = int((time.perf_counter() - debut) * 1000)
            return reponse

        vecteur: list[float] | None = None
        mode = "texte"
        try:
            vecteur = (await self._tei.encoder([question]))[0]
            mode = "hybride"
        except Exception as erreur:
            logger.warning("embeddings indisponibles : recherche plein texte seul (%s)", erreur)

        familles_actives = familles or list(FAMILLES_UNITE)
        fusionnees_par_famille: dict[str, list[dict]] = {}
        for famille in familles_actives:
            listes = [await db.chercher_plein_texte(famille, question_normalisee, CANDIDATS)]
            if vecteur is not None:
                listes.append(await db.chercher_vectoriel(famille, vecteur, CANDIDATS))
            source, collection = SOURCES_PAR_FAMILLE[famille]
            fusionnees = [
                {**unite, "famille": famille, "source": source, "collection": collection}
                for unite in fusion_rrf(listes)
            ]
            if famille == "passage":
                fusionnees = dedupliquer_passages(fusionnees)
            fusionnees_par_famille[famille] = fusionnees

        candidats = entrelacer_par_famille(fusionnees_par_famille, CANDIDATS_RERANK)
        fusionnes = candidats[:top_k]
        rerank = False
        if mode == "hybride" and candidats:
            try:
                paires = await self._reranker.reclasser(question, [c["texte"] for c in candidats])
                fusionnes = dedupliquer_passages(appliquer_reclassement(candidats, paires))[:top_k]
                rerank = True
            except Exception as erreur:
                logger.warning("reranker indisponible : ordre d'entrelacement conservé (%s)", erreur)

        reponse = ReponseRecherche(
            resultats=[ResultatRecherche(**unite) for unite in fusionnes],
            mode=mode,
            rerank=rerank,
            duree_ms=int((time.perf_counter() - debut) * 1000),
        )
        if mode == "hybride" and reponse.resultats:
            await self._cache.stocker(cle, reponse.model_dump(mode="json"))
        return reponse
