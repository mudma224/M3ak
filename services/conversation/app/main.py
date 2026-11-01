"""Point d'entrée du service conversation (phase 2, étape 4).

Orchestrateur client : sessions sans compte, questions en arabe ou en
français, réponses sourcées et vérifiées ou refus explicite. Points
d'entrée publics (frontend) : création de session, question en mode bloc
(harnais du jeu doré) ou en flux SSE (interface), historique,
signalement. Les appels inter-services (recherche, llm) portent le jeton
interne et le correlation ID.
"""

from __future__ import annotations

import contextlib
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from httpx import AsyncClient, Timeout
from m3ak_contracts import (
    DemandeQuestion,
    DemandeSignalement,
    ErreurCommune,
    EtatDependance,
    EvenementConversation,
    MessageHistorique,
    ReponseEcho,
    ReponseHistorique,
    ReponsePret,
    ReponseQuestion,
    ReponseRecherche,
    ReponseSante,
    ReponseSession,
    ReponseSignalement,
    SourceCitee,
)

from . import db
from .config import get_settings
from .logging_setup import configurer_logging, correlation_id
from .orchestrateur import (
    ErreurGeneration,
    ErreurRecherche,
    MorceauGeneration,
    Orchestrateur,
)
from .security import exiger_jeton_interne

VERSION = "0.5.1"
logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def duree_de_vie(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configurer_logging(settings.log_level)
    if settings.database_url:
        await db.initialiser_base(settings.database_url)
    yield
    await db.fermer_base()


def _evenement_sse(evenement: EvenementConversation) -> str:
    return (
        f"event: {evenement.type}\n"
        f"data: {evenement.model_dump_json(exclude_none=True)}\n\n"
    )


def create_app() -> FastAPI:
    """Fabrique de l'application — facilite les tests sans infrastructure."""
    application = FastAPI(
        title="M3ak — service conversation",
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
        return ReponseSante(status="ok", service="conversation", version=VERSION)

    @application.get("/ready", response_model=ReponsePret)
    async def pret(_: str = Depends(exiger_jeton_interne)) -> ReponsePret:
        """Prêt à servir — protégé par jeton interne ; la base, la recherche
        et le llm sont des dépendances (dégradation assumée, pas d'arrêt)."""
        settings = get_settings()
        dependances: list[EtatDependance] = []
        if settings.database_url:
            dependances.append(
                EtatDependance(nom="base", etat="ok" if await db.verifier_base() else "degrade")
            )
        for nom, url in (
            ("recherche", settings.url_recherche),
            ("llm", settings.url_llm),
        ):
            try:
                async with AsyncClient(timeout=5.0, trust_env=False) as client:
                    reponse = await client.get(f"{url}/health")
                etat = "ok" if reponse.status_code == 200 else "degrade"
            except Exception:
                etat = "degrade"
            dependances.append(EtatDependance(nom=nom, etat=etat))
        statut = "ok" if all(dep.etat == "ok" for dep in dependances) else "degrade"
        return ReponsePret(status=statut, service="conversation", dependances=dependances)

    @application.get("/internal/echo", response_model=ReponseEcho)
    async def echo(_: str = Depends(exiger_jeton_interne)) -> ReponseEcho:
        """Écho protégé — vérifie jeton et propagation du correlation ID."""
        return ReponseEcho(service="conversation", correlation_id=correlation_id.get())

    # ── Fabrique de l'orchestrateur, liée à une session ──────────────────

    def _orchestrateur(session_id: str, fournisseur: str | None = None) -> Orchestrateur:
        """L'orchestrateur avec ses dépendances réelles : recherche et llm
        appelés en HTTP (jeton interne + correlation propagés), messages
        persistés dans la base du service. `fournisseur` : option de
        test/évaluation transmise au service llm (None = défaut plateforme)."""
        settings = get_settings()
        entetes = {
            "X-Internal-Token": settings.internal_token,
            "X-Correlation-ID": correlation_id.get(),
        }

        async def chercher(question: str) -> ReponseRecherche:
            try:
                async with AsyncClient(
                    timeout=settings.delai_recherche_secondes, trust_env=False
                ) as client:
                    reponse = await client.post(
                        f"{settings.url_recherche}/internal/recherche",
                        headers=entetes,
                        json={"question": question, "top_k": settings.nb_sources_prompt},
                    )
            except Exception as erreur:
                detail = str(erreur) or type(erreur).__name__
                raise ErreurRecherche(f"recherche injoignable : {detail}") from erreur
            if reponse.status_code != 200:
                raise ErreurRecherche(f"recherche : HTTP {reponse.status_code}")
            return ReponseRecherche(**reponse.json())

        async def generer_flux(messages, max_jetons=None):
            """Génération via le FLUX du service llm (étape 3bis) : les
            morceaux sont transmis à l'orchestrateur au fil de leur arrivée
            (il les vérifie phrase à phrase avant diffusion) — la lecture ne
            peut pas expirer pendant la réflexion des modèles locaux sur CPU,
            et les erreurs du fournisseur voyagent en exception typée.
            `max_jetons` : budget ponctuel (régénération à budget doublé
            après troncature) ; None = budget par défaut de la plateforme."""
            try:
                async with AsyncClient(
                    timeout=Timeout(settings.delai_llm_secondes, connect=10.0),
                    trust_env=False,
                ) as client, client.stream(
                    "POST",
                    f"{settings.url_llm}/internal/generation/stream",
                    headers=entetes,
                    json={
                        "messages": [m.model_dump() for m in messages],
                        "temperature": 0.0,
                        "max_jetons": max_jetons or settings.max_jetons_generation,
                        "fournisseur": fournisseur,
                    },
                ) as reponse:
                    if reponse.status_code != 200:
                        corps = (await reponse.aread()).decode("utf-8", "replace")
                        raise ErreurGeneration(
                            f"génération : HTTP {reponse.status_code} — {corps[:200]}",
                            statut_http=(
                                429 if reponse.status_code == 429 else 502
                            ),
                        )
                    type_evenement = None
                    async for ligne in reponse.aiter_lines():
                        if ligne.startswith("event: "):
                            type_evenement = ligne[len("event: ") :]
                        elif ligne.startswith("data: ") and type_evenement:
                            charge = json.loads(ligne[len("data: ") :])
                            if type_evenement == "debut":
                                yield MorceauGeneration(
                                    entete={
                                        "fournisseur": charge.get(
                                            "fournisseur", "inconnu"
                                        ),
                                        "modele": charge.get("modele", "inconnu"),
                                    }
                                )
                            elif type_evenement == "fragment":
                                yield MorceauGeneration(
                                    texte=charge.get("texte") or ""
                                )
                            elif type_evenement == "fin":
                                yield MorceauGeneration(fin=charge)
                            elif type_evenement == "erreur":
                                message = charge.get("erreur") or "flux en erreur"
                                raise ErreurGeneration(
                                    message,
                                    statut_http=429 if "quota" in message else 502,
                                )
                            type_evenement = None
            except ErreurGeneration:
                raise
            except Exception as erreur:
                # Certaines exceptions réseau (timeouts httpx) n'ont pas de
                # message : le type au moins nomme la cause au journal.
                detail = str(erreur) or type(erreur).__name__
                raise ErreurGeneration(f"génération injoignable : {detail}") from erreur

        async def enregistrer(role, contenu, langue, refus, metadonnees) -> str:
            return await db.ajouter_message(
                session_id, role, contenu, langue, refus, metadonnees
            )

        return Orchestrateur(
            chercher,
            generer_flux,
            enregistrer,
            seuil_appel=settings.seuil_appel_generation,
            nb_sources=settings.nb_sources_prompt,
            max_caracteres_passage=settings.max_caracteres_passage,
            max_jetons=settings.max_jetons_generation,
        )

    async def _session_ou_404(session_id: str) -> None:
        if not await db.session_existe(session_id):
            raise HTTPException(status_code=404, detail="session inconnue")

    # Passée par l'état de l'application : les tests remplacent la fabrique
    # (et le module db) pour tourner sans infrastructure.
    application.state.fabrique_orchestrateur = _orchestrateur

    # ── API publique (accès sans compte en V1) ───────────────────────────

    @application.post("/sessions", response_model=ReponseSession)
    async def creer_session() -> ReponseSession:
        """Ouvre une session anonyme."""
        session_id = await db.creer_session()
        return ReponseSession(
            session_id=session_id,
            cree_le=datetime.now(UTC).isoformat(),
        )

    @application.post(
        "/sessions/{session_id}/messages", response_model=ReponseQuestion
    )
    async def poser_question(
        session_id: str, demande: DemandeQuestion
    ) -> ReponseQuestion:
        """Mode bloc — toute la réponse d'un coup (harnais du jeu doré,
        temperature 0). La réponse est vérifiée ou c'est un refus."""
        await _session_ou_404(session_id)
        try:
            return await application.state.fabrique_orchestrateur(
                session_id, demande.fournisseur
            ).poser(demande.question, session_id)
        except ErreurRecherche as erreur:
            raise HTTPException(
                status_code=503, detail="service de recherche indisponible"
            ) from erreur
        except ErreurGeneration as erreur:
            raise HTTPException(
                status_code=erreur.statut_http if erreur.statut_http in (429, 502) else 502,
                detail=str(erreur),
            ) from erreur

    @application.post("/sessions/{session_id}/messages/flux")
    async def poser_question_flux(
        session_id: str, demande: DemandeQuestion
    ) -> StreamingResponse:
        """Mode flux SSE — statut, sources, puis la réponse VÉRIFIÉE en
        fragments, puis fin avec les métadonnées complètes."""
        await _session_ou_404(session_id)
        orchestrateur = application.state.fabrique_orchestrateur(
            session_id, demande.fournisseur
        )

        async def flux() -> AsyncIterator[str]:
            try:
                async for evenement in orchestrateur.flux(demande.question, session_id):
                    yield _evenement_sse(evenement)
            except ErreurRecherche as erreur:
                yield _evenement_sse(
                    EvenementConversation(
                        type="erreur", erreur="service de recherche indisponible"
                    )
                )
                logger.warning("flux : recherche indisponible (%s)", erreur)
            except ErreurGeneration as erreur:
                yield _evenement_sse(
                    EvenementConversation(
                        type="erreur",
                        erreur=(
                            "service de génération momentanément indisponible"
                            if erreur.statut_http != 429
                            else "quota de génération atteint — réessayez un peu plus tard"
                        ),
                    )
                )
                logger.warning("flux : génération indisponible (%s)", erreur)

        return StreamingResponse(
            flux(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @application.get("/sessions/{session_id}", response_model=ReponseHistorique)
    async def historique(session_id: str) -> ReponseHistorique:
        """Historique de la session — avec ce que l'affichage restaure à
        l'identique : sources citées (consultation PDF, checklist de
        démarche), motif de refus et marqueurs. Sans métadonnées internes
        (diagnostic, scores, fournisseur) ni identifiants de messages."""
        await _session_ou_404(session_id)
        restaurés = []
        for ligne in await db.historique(session_id):
            meta = ligne.get("metadonnees") or {}
            restaurés.append(
                MessageHistorique(
                    role=ligne["role"],
                    contenu=ligne["contenu"],
                    langue=ligne.get("langue"),
                    cree_le=ligne["cree_le"],
                    refus=ligne["refus"],
                    signale=ligne["signale"],
                    sources=[SourceCitee(**s) for s in meta.get("sources") or []],
                    motif_refus=meta.get("motif_refus"),
                    avertissements=meta.get("avertissements") or [],
                    repli_interlingue=bool(meta.get("repli_interlingue")),
                    texte_partiel=bool(meta.get("texte_partiel")),
                    sans_reponse=bool(meta.get("sans_reponse")),
                )
            )
        return ReponseHistorique(session_id=session_id, messages=restaurés)

    @application.delete("/sessions/{session_id}", status_code=204)
    async def supprimer_session(session_id: str) -> Response:
        """Supprime la session et ses messages (droit à l'effacement)."""
        if not await db.supprimer_session(session_id):
            raise HTTPException(status_code=404, detail="session inconnue")
        return Response(status_code=204)

    @application.post(
        "/messages/{message_id}/signaler", response_model=ReponseSignalement
    )
    async def signaler(message_id: str, demande: DemandeSignalement) -> ReponseSignalement:
        """Signale une réponse (contrôle qualité humain, revue en V1)."""
        if not await db.signaler(message_id, demande.motif):
            raise HTTPException(status_code=404, detail="message inconnu")
        return ReponseSignalement(message_id=message_id, signale=True)

    return application


application = create_app()
