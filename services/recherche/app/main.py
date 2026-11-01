"""Point d'entrée du service recherche."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from m3ak_contracts import (
    FAMILLES_UNITE,
    FILE_DECISIONS_TO_INDEX,
    FILE_FICHES_TO_INDEX,
    FILE_SEGMENTS_TO_INDEX,
    ErreurCommune,
    EtatDependance,
    MessageDecisionsAIndexer,
    MessageFichesAIndexer,
    MessageSegmentsAIndexer,
    ReponseEcho,
    ReponsePret,
    ReponseRecherche,
    ReponseSante,
    RequeteRecherche,
)

from . import backfill, db
from .cache import CacheRecherche
from .config import get_settings
from .embeddings import ClientTEI
from .logging_setup import configurer_logging, correlation_id
from .messaging import BusRabbitMQ
from .moteur import MoteurRecherche
from .rerank import ClientReranker
from .security import exiger_jeton_interne

VERSION = "0.2.1"
logger = logging.getLogger(__name__)

async def traiter_segments(message: MessageSegmentsAIndexer) -> None:
    """Persiste le lot de segments de façon idempotente (clé = segment_id).

    Un échec (base indisponible) propage l'exception : le message part en
    DLQ et pourra être rejeté après inspection — jamais perdu, jamais
    traité deux fois.
    """
    premier = message.charge.segments[0]
    inseres_ids, presents = await db.upserter_segments(message.charge.segments)
    if inseres_ids:
        await backfill.indexer_unites_fraiches(
            application.state.tei, "segment", inseres_ids
        )
    logger.info(
        "segments indexes : %d nouveaux, %d deja presents (document=%s, emis_par=%s)",
        len(inseres_ids),
        presents,
        premier.document_id,
        message.emis_par,
    )


async def traiter_fiches(message: MessageFichesAIndexer) -> None:
    """Persiste le lot de fiches de procédures de façon idempotente
    (clé = fiche_id) — gestion séparée des segments d'articles."""
    premiere = message.charge.fiches[0]
    inseres_ids, presents = await db.upserter_fiches(message.charge.fiches)
    if inseres_ids:
        await backfill.indexer_unites_fraiches(
            application.state.tei, "fiche", inseres_ids
        )
    logger.info(
        "fiches indexees : %d nouvelles, %d deja presentes (document=%s, emis_par=%s)",
        len(inseres_ids),
        presents,
        premiere.document_id,
        message.emis_par,
    )


async def traiter_decisions(message: MessageDecisionsAIndexer) -> None:
    """Persiste le lot de décisions de justice de façon idempotente
    (clé = decision_id), génère leurs passages et les vectorise —
    gestion séparée des segments et des fiches."""
    premiere = message.charge.decisions[0]
    from .passages import generer_passages

    inseres, presents = await db.upserter_decisions(message.charge.decisions)
    passage_ids: list[str] = []
    for decision in message.charge.decisions:
        passages = generer_passages(decision)
        await db.inserer_passages(passages)
        passage_ids.extend(passage.passage_id for passage in passages)
    if passage_ids:
        await backfill.indexer_unites_fraiches(application.state.tei, "passage", passage_ids)
    logger.info(
        "decisions indexees : %d nouvelles, %d deja presentes, %d passages (document=%s, emis_par=%s)",
        inseres,
        presents,
        len(passage_ids),
        premiere.document_id,
        message.emis_par,
    )


@contextlib.asynccontextmanager
async def duree_de_vie(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configurer_logging(settings.log_level)
    if settings.database_url:
        await db.initialiser_base(settings.database_url)
    tei = ClientTEI(settings.tei_url)
    reranker = ClientReranker(settings.reranker_url)
    cache = CacheRecherche(settings.redis_url)
    application.state.tei = tei
    application.state.reranker = reranker
    application.state.cache = cache
    application.state.moteur = MoteurRecherche(tei, cache, reranker)
    bus = BusRabbitMQ(settings.rabbitmq_url or "", settings.service_name)
    await bus.connecter()
    await bus.declarer_file_travail(FILE_SEGMENTS_TO_INDEX)
    await bus.consommer(FILE_SEGMENTS_TO_INDEX, MessageSegmentsAIndexer, traiter_segments)
    await bus.declarer_file_travail(FILE_FICHES_TO_INDEX)
    await bus.consommer(FILE_FICHES_TO_INDEX, MessageFichesAIndexer, traiter_fiches)
    await bus.declarer_file_travail(FILE_DECISIONS_TO_INDEX)
    await bus.consommer(FILE_DECISIONS_TO_INDEX, MessageDecisionsAIndexer, traiter_decisions)
    app.state.bus = bus
    yield
    app.state.bus = None
    await bus.fermer()
    await cache.fermer()
    await db.fermer_base()


def create_app() -> FastAPI:
    """Fabrique de l'application — facilite les tests sans infrastructure."""
    application = FastAPI(
        title="M3ak — service recherche",
        version=VERSION,
        lifespan=duree_de_vie,
    )

    @application.middleware("http")
    async def middleware_correlation(requete: Request, suivant) -> Response:
        identifiant = requete.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        jeton = correlation_id.set(identifiant)
        try:
            reponse: Response = await suivant(requete)
            reponse.headers["X-Correlation-ID"] = identifiant
            return reponse
        finally:
            correlation_id.reset(jeton)

    @application.exception_handler(HTTPException)
    async def gestion_http(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ErreurCommune(
                detail=str(exc.detail), correlation_id=correlation_id.get()
            ).model_dump(),
        )

    @application.get("/health", response_model=ReponseSante)
    async def sante() -> ReponseSante:
        """Vivacité — ouverte (utilisée par les healthchecks Docker/Traefik)."""
        return ReponseSante(status="ok", service="recherche", version=VERSION)

    @application.get("/ready", response_model=ReponsePret)
    async def pret(_: str = Depends(exiger_jeton_interne)) -> ReponsePret:
        """Prêt à servir — protégé par jeton interne.

        Le serveur d'embeddings est une dépendance : son absence dégrade le
        service (recherche plein texte seul) sans l'arrêter.
        """
        settings = get_settings()
        dependances: list[EtatDependance] = []
        if settings.database_url:
            dependances.append(
                EtatDependance(nom="base", etat="ok" if await db.verifier_base() else "degrade")
            )
        dependances.append(
            EtatDependance(
                nom="embeddings (tei)",
                etat="ok" if await application.state.tei.verifier() else "degrade",
            )
        )
        dependances.append(
            EtatDependance(
                nom="rerank (tei)",
                etat="ok" if await application.state.reranker.verifier() else "degrade",
            )
        )
        statut = "ok" if all(dep.etat == "ok" for dep in dependances) else "degrade"
        return ReponsePret(status=statut, service="recherche", dependances=dependances)

    @application.get("/internal/echo", response_model=ReponseEcho)
    async def echo(_: str = Depends(exiger_jeton_interne)) -> ReponseEcho:
        """Écho protégé — vérifie jeton et propagation du correlation ID."""
        return ReponseEcho(service="recherche", correlation_id=correlation_id.get())

    @application.get("/internal/segments/compte")
    async def compte_segments(_: str = Depends(exiger_jeton_interne)) -> JSONResponse:
        """Volumétrie de l'index — protégé par jeton interne.

        503 si la base n'est pas configurée : le service ne peut pas
        répondre honnêtement sans sa base.
        """
        parametres = get_settings()
        if not parametres.database_url:
            raise HTTPException(status_code=503, detail="base non configuree")
        nb_segments, nb_documents = await db.compter_segments()
        return JSONResponse(
            content={"nb_segments": nb_segments, "nb_documents": nb_documents}
        )

    @application.get("/internal/fiches/compte")
    async def compte_fiches(_: str = Depends(exiger_jeton_interne)) -> JSONResponse:
        """Volumétrie des fiches de procédures indexées."""
        parametres = get_settings()
        if not parametres.database_url:
            raise HTTPException(status_code=503, detail="base non configuree")
        nb_fiches, nb_documents = await db.compter_fiches()
        return JSONResponse(
            content={"nb_fiches": nb_fiches, "nb_documents": nb_documents}
        )

    @application.get("/internal/decisions/compte")
    async def compte_decisions(_: str = Depends(exiger_jeton_interne)) -> JSONResponse:
        """Volumétrie des décisions de justice indexées."""
        parametres = get_settings()
        if not parametres.database_url:
            raise HTTPException(status_code=503, detail="base non configuree")
        nb_decisions, nb_documents = await db.compter_decisions()
        return JSONResponse(
            content={"nb_decisions": nb_decisions, "nb_documents": nb_documents}
        )

    @application.get("/internal/passages/compte")
    async def compte_passages(_: str = Depends(exiger_jeton_interne)) -> JSONResponse:
        """Volumétrie des passages de décisions (recherche vectorielle)."""
        parametres = get_settings()
        if not parametres.database_url:
            raise HTTPException(status_code=503, detail="base non configuree")
        return JSONResponse(content={"nb_passages": await db.compter_passages()})

    @application.post("/internal/recherche", response_model=ReponseRecherche)
    async def recherche(
        requete: RequeteRecherche, _: str = Depends(exiger_jeton_interne)
    ) -> ReponseRecherche:
        """Recherche hybride (plein texte + vectoriel, fusion RRF) — protégé
        par jeton interne. Mode « texte » si le serveur d'embeddings est
        indisponible (dégradation gracieuse)."""
        familles = requete.familles
        if familles:
            invalides = [f for f in familles if f not in FAMILLES_UNITE]
            if invalides:
                raise HTTPException(
                    status_code=400,
                    detail=f"familles inconnues : {', '.join(invalides)}",
                )
        return await application.state.moteur.rechercher(requete.question, requete.top_k, familles)

    @application.post("/internal/index/backfill")
    async def lancer_backfill(_: str = Depends(exiger_jeton_interne)) -> JSONResponse:
        """Vectorisation des unités sans embedding + passages des décisions.

        Tâche de fond (~1-3 h sur CPU au premier lancement) : répond
        immédiatement, suivi sur /internal/index/backfill/statut.
        """
        if backfill.etat_backfill().get("en_cours"):
            raise HTTPException(status_code=409, detail="backfill deja en cours")
        asyncio.create_task(backfill.executer_backfill(application.state.tei))
        return JSONResponse(content={"demarre": True})

    @application.get("/internal/index/backfill/statut")
    async def statut_backfill(_: str = Depends(exiger_jeton_interne)) -> JSONResponse:
        return JSONResponse(content=backfill.etat_backfill())

    return application


application = create_app()
