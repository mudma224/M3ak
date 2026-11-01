"""Point d'entrée du service llm (phase 2, étapes 3 et 3bis).

Brique interne « messages entrants → texte généré » : fournisseurs
commutables par configuration, quota global plateforme (fenêtres
glissantes), file d'attente (la requête attend au lieu d'échouer) et
journalisation de la consommation. Génération en un bloc ou en flux
SSE (perception de latence, indispensable en génération locale sur
CPU). L'orchestration (citations, langue, anonymisation) relève de la
couche conversation (étape 4).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from m3ak_contracts import (
    DemandeGeneration,
    ErreurCommune,
    EtatDependance,
    EvenementGeneration,
    ReponseEcho,
    ReponseGeneration,
    ReponsePret,
    ReponseSante,
)

from . import db
from .config import get_settings
from .fournisseurs import (
    ErreurFournisseur,
    ErreurQuotaFournisseur,
    FournisseurOpenAI,
    complet_depuis_motif,
)
from .logging_setup import configurer_logging, correlation_id
from .quota import GestionnaireQuota
from .security import exiger_jeton_interne

VERSION = "0.3.1"
logger = logging.getLogger(__name__)

# Références fortes aux tâches de fond (consignation du quota) — une tâche
# orpheline peut être ramassée par le ramasse-miettes avant son terme.
_taches_fond: set[asyncio.Task] = set()


def _evenement_sse(evenement: EvenementGeneration) -> str:
    """Un événement SSE : `event:` porte le type, `data:` la charge JSON."""
    return (
        f"event: {evenement.type}\n"
        f"data: {evenement.model_dump_json(exclude_none=True)}\n\n"
    )


def fabrique_fournisseurs() -> dict[str, FournisseurOpenAI]:
    """Les fournisseurs actés — même protocole OpenAI, bascule par config :
    Z.ai (GLM), Google AI Studio (couche OpenAI de Gemini) et serveur
    local (LM Studio en dev ; Ollama/llama.cpp à l'hébergement)."""
    settings = get_settings()
    return {
        "zai": FournisseurOpenAI(
            nom="zai",
            url_base=settings.zai_url,
            modele=settings.zai_modele,
            cle=settings.zai_api_key,
            timeout=settings.delai_fournisseur_secondes,
        ),
        "google": FournisseurOpenAI(
            nom="google",
            url_base=settings.google_url,
            modele=settings.google_modele,
            cle=settings.google_api_key,
            timeout=settings.delai_fournisseur_secondes,
        ),
        "local": FournisseurOpenAI(
            nom="local",
            url_base=settings.local_url,
            modele=settings.local_modele,
            cle=settings.local_api_key,
            timeout=settings.delai_fournisseur_secondes,
        ),
    }


@contextlib.asynccontextmanager
async def duree_de_vie(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configurer_logging(settings.log_level)
    if settings.database_url:
        await db.initialiser_base(settings.database_url)
    app.state.fournisseurs = fabrique_fournisseurs()
    app.state.quota = GestionnaireQuota(
        settings.redis_url,
        settings.quota_requetes_minute,
        settings.quota_jetons_jour,
    )
    app.state.semaphore_local = asyncio.Semaphore(max(1, settings.local_concurrence))
    yield
    await app.state.quota.fermer()
    await db.fermer_base()


def create_app() -> FastAPI:
    """Fabrique de l'application — facilite les tests sans infrastructure."""
    application = FastAPI(
        title="M3ak — service llm",
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
        return ReponseSante(status="ok", service="llm", version=VERSION)

    @application.get("/ready", response_model=ReponsePret)
    async def pret(_: str = Depends(exiger_jeton_interne)) -> ReponsePret:
        """Prêt à servir — protégé par jeton interne.

        Le fournisseur actif est une dépendance : son absence dégrade le
        service (la file d'attente renverra une erreur claire), sans
        l'arrêter.
        """
        settings = get_settings()
        dependances: list[EtatDependance] = []
        if settings.database_url:
            dependances.append(
                EtatDependance(nom="base", etat="ok" if await db.verifier_base() else "degrade")
            )
        fournisseur = application.state.fournisseurs.get(settings.fournisseur_defaut)
        if fournisseur is not None:
            dependances.append(
                EtatDependance(
                    nom=f"fournisseur ({settings.fournisseur_defaut})",
                    etat="ok" if await fournisseur.verifier() else "degrade",
                )
            )
        statut = "ok" if all(dep.etat == "ok" for dep in dependances) else "degrade"
        return ReponsePret(status=statut, service="llm", dependances=dependances)

    @application.get("/internal/echo", response_model=ReponseEcho)
    async def echo(_: str = Depends(exiger_jeton_interne)) -> ReponseEcho:
        """Écho protégé — vérifie jeton et propagation du correlation ID."""
        return ReponseEcho(service="llm", correlation_id=correlation_id.get())

    @application.post("/internal/generation", response_model=ReponseGeneration)
    async def generation(
        demande: DemandeGeneration, _: str = Depends(exiger_jeton_interne)
    ) -> ReponseGeneration:
        """Génération en un bloc — quota respecté, file d'attente intégrée.

        Quand le quota plateforme est atteint, la requête patiente (au lieu
        d'échouer) jusqu'à `attente_max_secondes` ; au-delà, erreur 429
        claire. Un fournisseur saturé (429 amont) est signalé tel quel.
        """
        settings = get_settings()
        nom = demande.fournisseur or settings.fournisseur_defaut
        fournisseur = application.state.fournisseurs.get(nom)
        if fournisseur is None:
            raise HTTPException(status_code=400, detail=f"fournisseur inconnu : {nom}")

        caracteres = sum(len(m.contenu) for m in demande.messages)
        attendu, attente_secondes = await _attendre_quota(application, settings, caracteres)

        if nom == "local":
            # Concurrency locale bornée — l'acquisition est BORNÉE : un
            # générateur coincé ne peut pas faire patienter les suivants
            # sans fin (503 propre au lieu d'un blocage silencieux).
            try:
                await asyncio.wait_for(
                    application.state.semaphore_local.acquire(),
                    timeout=settings.attente_max_secondes,
                )
            except TimeoutError:
                raise HTTPException(
                    status_code=503,
                    detail=f"fournisseur {nom} occupé — réessayez dans un instant",
                )
            try:
                return await _generer(
                    application, fournisseur, demande, nom, attendu, attente_secondes
                )
            finally:
                application.state.semaphore_local.release()
        return await _generer(
            application, fournisseur, demande, nom, attendu, attente_secondes
        )

    async def _attendre_quota(
        application: FastAPI, settings, caracteres: int
    ) -> tuple[bool, float]:
        """File d'attente : sonde le quota jusqu'à libération ou épuisement
        de la patience (erreur 429) ; retourne (a_patiente, secondes)."""
        jetons_estimes = caracteres // 4 + 64
        debut_attente = time.perf_counter()
        while True:
            autorise, _attente = await application.state.quota.verifier(jetons_estimes)
            if autorise:
                break
            ecoule = time.perf_counter() - debut_attente
            if ecoule >= settings.attente_max_secondes:
                raise HTTPException(
                    status_code=429,
                    detail="quota plateforme atteint — réessayez ultérieurement",
                )
            await asyncio.sleep(settings.pas_attente_secondes)
        attente_secondes = time.perf_counter() - debut_attente
        return attente_secondes > settings.pas_attente_secondes / 2, attente_secondes

    @application.post("/internal/generation/stream")
    async def generation_flux(
        demande: DemandeGeneration, _: str = Depends(exiger_jeton_interne)
    ) -> StreamingResponse:
        """Génération en flux SSE (étape 3bis) — perception de latence.

        Mêmes règles que la génération en un bloc : quota plateforme et
        file d'attente AVANT l'ouverture du flux (erreurs HTTP propres),
        concurrence locale bornée, journalisation de la consommation.
        Un incident du fournisseur survenant après ouverture (statut HTTP
        déjà parti) voyage en événement `erreur` dans le flux.
        """
        settings = get_settings()
        nom = demande.fournisseur or settings.fournisseur_defaut
        fournisseur = application.state.fournisseurs.get(nom)
        if fournisseur is None:
            raise HTTPException(status_code=400, detail=f"fournisseur inconnu : {nom}")

        caracteres = sum(len(m.contenu) for m in demande.messages)
        attendu, attente_secondes = await _attendre_quota(application, settings, caracteres)

        async def flux() -> AsyncIterator[str]:
            debut_generation = time.perf_counter()
            modele_resolu: str | None = None
            jetons_entree: int | None = None
            jetons_sortie: int | None = None
            fin_motif: str | None = None
            erreur_message: str | None = None
            semaphore_acquis = False
            try:
                if nom == "local":
                    # Acquisition BORNÉE : si une génération précédente
                    # retient le sémaphore (client disparu sans
                    # propagation de la fermeture), les suivantes reçoivent
                    # un événement `erreur` au lieu de patienter sans fin.
                    try:
                        await asyncio.wait_for(
                            application.state.semaphore_local.acquire(),
                            timeout=settings.attente_max_secondes,
                        )
                        semaphore_acquis = True
                    except TimeoutError:
                        erreur_message = (
                            f"fournisseur {nom} occupé — réessayez dans un instant"
                        )
                if erreur_message is None:
                    async for morceau in fournisseur.generer_flux(
                        demande.messages, demande.temperature, demande.max_jetons
                    ):
                        if morceau.modele:
                            modele_resolu = morceau.modele
                            yield _evenement_sse(
                                EvenementGeneration(
                                    type="debut", fournisseur=nom, modele=morceau.modele
                                )
                            )
                        elif morceau.texte:
                            yield _evenement_sse(
                                EvenementGeneration(type="fragment", texte=morceau.texte)
                            )
                        else:
                            jetons_entree = morceau.jetons_entree
                            jetons_sortie = morceau.jetons_sortie
                            fin_motif = morceau.fin_motif
            except ErreurQuotaFournisseur as erreur:
                erreur_message = (
                    f"quota du fournisseur {nom} épuisé — réessayez ultérieurement ({erreur})"
                )
            except ErreurFournisseur as erreur:
                # Certaines exceptions réseau n'ont pas de message : le type
                # au moins nomme la cause dans le journal.
                erreur_message = str(erreur) or type(erreur).__name__
            latence_ms = int((time.perf_counter() - debut_generation) * 1000)
            try:
                if erreur_message is not None:
                    yield _evenement_sse(
                        EvenementGeneration(type="erreur", erreur=erreur_message)
                    )
                else:
                    yield _evenement_sse(
                        EvenementGeneration(
                            type="fin",
                            jetons_entree=jetons_entree,
                            jetons_sortie=jetons_sortie,
                            latence_ms=latence_ms,
                            attendu=attendu,
                            attente_secondes=round(attente_secondes, 2),
                            complet=complet_depuis_motif(fin_motif),
                        )
                    )
            finally:
                # Exécuté à la complétion comme à la déconnexion du client.
                # La consignation part en tâche de fond : ni `yield` ni
                # `await` ne sont permis dans un générateur déjà fermé.
                if erreur_message is None:
                    entree = max(jetons_entree or 0, 1)
                    sortie = max(jetons_sortie or 0, 0)
                    latence = latence_ms

                    async def consigner() -> None:
                        with contextlib.suppress(Exception):
                            await application.state.quota.consommer(max(sortie, 1))
                        await db.journaliser_consommation(
                            fournisseur=nom,
                            modele=modele_resolu or "",
                            jetons_entree=entree,
                            jetons_sortie=sortie,
                            latence_ms=latence,
                            attendu=attendu,
                            correlation_id=correlation_id.get(),
                        )

                    tache = asyncio.create_task(consigner())
                    _taches_fond.add(tache)
                    tache.add_done_callback(_taches_fond.discard)
                if nom == "local" and semaphore_acquis:
                    application.state.semaphore_local.release()

        return StreamingResponse(
            flux(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async def _generer(
        application: FastAPI,
        fournisseur,
        demande: DemandeGeneration,
        nom: str,
        attendu: bool,
        attente_secondes: float,
    ) -> ReponseGeneration:
        debut = time.perf_counter()
        try:
            donnees = await fournisseur.generer(
                demande.messages, demande.temperature, demande.max_jetons
            )
        except ErreurQuotaFournisseur as erreur:
            raise HTTPException(
                status_code=429,
                detail=f"quota du fournisseur {nom} épuisé — réessayez ultérieurement ({erreur})",
            )
        except ErreurFournisseur as erreur:
            raise HTTPException(status_code=502, detail=str(erreur))
        latence_ms = int((time.perf_counter() - debut) * 1000)

        jetons_sortie = donnees.jetons_sortie or 0
        await application.state.quota.consommer(max(jetons_sortie, 1))
        await db.journaliser_consommation(
            fournisseur=nom,
            modele=donnees.modele,
            jetons_entree=donnees.jetons_entree,
            jetons_sortie=donnees.jetons_sortie,
            latence_ms=latence_ms,
            attendu=attendu,
            correlation_id=correlation_id.get(),
        )
        return ReponseGeneration(
            texte=donnees.texte,
            fournisseur=nom,
            modele=donnees.modele,
            jetons_entree=donnees.jetons_entree,
            jetons_sortie=donnees.jetons_sortie,
            latence_ms=latence_ms,
            attendu=attendu,
            attente_secondes=round(attente_secondes, 2),
            complet=donnees.complet,
        )

    @application.get("/internal/generation/quota")
    async def quota(_: str = Depends(exiger_jeton_interne)) -> JSONResponse:
        """État courant du quota plateforme (fenêtres glissantes)."""
        return JSONResponse(content=await application.state.quota.etat())

    @application.get("/internal/generation/compte")
    async def compte(_: str = Depends(exiger_jeton_interne)) -> JSONResponse:
        """Volumétrie du journal de consommation."""
        return JSONResponse(content={"nb_generations": await db.compter_consommations()})

    return application


application = create_app()
