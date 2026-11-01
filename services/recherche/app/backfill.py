"""Backfill des embeddings (phase 2).

Complète, par lots, ce que l'indexation a fait sans serveur d'embeddings :
génération des passages des décisions, vectorisation des unités sans
embedding (segments, fiches, passages), puis création des index HNSW.
Idempotent : re-lancer ne traite que ce qui manque. Tâche de fond
déclenchée manuellement (endpoint interne) — le service reste responsive
pendant l'exécution.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC

from m3ak_contracts import FAMILLES_UNITE, DecisionAIndexer

from . import db, maintenance
from .embeddings import ClientTEI
from .passages import generer_passages

logger = logging.getLogger(__name__)

TAILLE_LOT = 64
DECISIONS_PAR_LOT = 200

# Une exécution de plusieurs dizaines d'heures rencontrera forcément un
# redémarrage du serveur d'embeddings (relancé automatiquement par son
# policy Docker) : la boucle attend son retour au lieu d'abandonner.
SECONDES_ENTRE_TENTATIVES = 30
TENTATIVES_MAXIMUM = 60  # ~30 min de patience au total

_etat: dict = {"en_cours": False}


def etat_backfill() -> dict:
    return dict(_etat)


async def _encoder_avec_reprise(tei: ClientTEI, textes: list[str]) -> list[list[float]]:
    """Encode un lot en tolérant les coupures du serveur d'embeddings.

    Une coupure de TEI (arrêt gracieux, redémarrage Docker, hiccup WSL)
    ne doit pas arrêter un backfill de 30 h : on attend le retour du
    serveur puis on rejoue le même lot — aucun travail n'est perdu (les
    vecteurs sont écrits juste après, unité par unité idempotente).
    """
    for tentative in range(1, TENTATIVES_MAXIMUM + 1):
        try:
            return await tei.encoder(textes)
        except Exception as erreur:
            if tentative == TENTATIVES_MAXIMUM:
                raise
            logger.warning(
                "serveur d'embeddings injoignable (tentative %d/%d) : %s — "
                "nouvel essai dans %d s",
                tentative,
                TENTATIVES_MAXIMUM,
                erreur,
                SECONDES_ENTRE_TENTATIVES,
            )
            await asyncio.sleep(SECONDES_ENTRE_TENTATIVES)
    raise RuntimeError("inatteignable")  # boucle lève toujours avant


async def executer_backfill(tei: ClientTEI) -> None:
    """Tâche de fond — mise à jour de l'état global, jamais d'exception
    qui s'échappe (le service reste sain ; l'état porte l'erreur)."""
    _etat.update(
        en_cours=True,
        etape="passages",
        passages_crees=0,
        traites=0,
        total=0,
        erreur=None,
        termine_le=None,
    )
    try:
        await db.supprimer_index_hnsw()
        await _generer_passages_manquants()
        _etat["etape"] = "embeddings"
        restants = await db.compter_sans_embedding()
        _etat["total"] = sum(restants.values())
        logger.info("backfill : %d unités sans embedding", _etat["total"])
        for famille in FAMILLES_UNITE:
            await _vectoriser_famille(tei, famille)
        _etat["etape"] = "index_hnsw"
        await db.creer_index_hnsw()
        _etat["termine_le"] = _horodatage()
        logger.info("backfill terminé : %d unités vectorisées", _etat["traites"])
    except Exception as erreur:
        _etat["erreur"] = str(erreur)
        logger.exception("backfill interrompu")
    finally:
        _etat["en_cours"] = False


async def _generer_passages_manquants() -> None:
    creees = 0
    while True:
        lignes = await db.decisions_sans_passages(DECISIONS_PAR_LOT)
        if not lignes:
            break
        for ligne in lignes:
            decision = DecisionAIndexer(**ligne)
            passages = generer_passages(decision)
            creees += await db.inserer_passages(passages)
            if not passages:
                await db.marquer_sans_passages(decision.decision_id)
            _etat["passages_crees"] = creees


async def _vectoriser_famille(tei: ClientTEI, famille: str) -> None:
    await maintenance.remplir_tsv(famille)
    while True:
        lot = await db.lot_sans_embedding(famille, TAILLE_LOT)
        if not lot:
            return
        vecteurs = await _encoder_avec_reprise(tei, [ligne["texte"] for ligne in lot])
        await maintenance.appliquer_embeddings(
            famille,
            [
                {"unite_id": ligne["unite_id"], "embedding": vecteur}
                for ligne, vecteur in zip(lot, vecteurs)
            ],
        )
        _etat["traites"] += len(lot)


async def indexer_unites_fraiches(tei: ClientTEI, famille: str, unite_ids: list[str]) -> int:
    """Vectorise les unités insérées par un consommateur — best effort :
    une indisponibilité du serveur d'embeddings ne fait pas échouer le
    message (l'unité est déjà indexée en plein texte ; le backfill
    rattrapera). Aucune exception ne franchit cette fonction : le message
    appelant est déjà persisté, le rejeter ne servirait qu'à le DLQ-er."""
    ids = list(unite_ids) if isinstance(unite_ids, (list, tuple)) else []
    if not ids:
        if unite_ids:
            logger.error(
                "indexer_unites_fraiches attend une liste d'identifiants, "
                "reçu %s — vectorisation différée (le backfill rattrapera)",
                type(unite_ids).__name__,
            )
        return 0
    try:
        unites = await db.lire_textes(famille, ids)
        vecteurs = await tei.encoder([unite["texte"] for unite in unites])
        await maintenance.appliquer_embeddings(
            famille,
            [
                {"unite_id": unite["unite_id"], "embedding": vecteur}
                for unite, vecteur in zip(unites, vecteurs)
            ],
        )
        return len(unites)
    except Exception as erreur:
        logger.warning(
            "embeddings différés pour %d unité(s) %s : %s (le backfill rattrapera)",
            len(ids),
            famille,
            erreur,
        )
        return 0


def _horodatage() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat()
