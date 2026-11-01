"""Point d'entrée du service corpus."""

from __future__ import annotations

import asyncio
import contextlib
import html as html_mod
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from m3ak_contracts import (
    FILE_DECISIONS_TO_INDEX,
    FILE_FICHES_TO_INDEX,
    FILE_SEGMENTS_TO_INDEX,
    ErreurCommune,
    EtatDependance,
    ReponseEcho,
    ReponsePret,
    ReponseSante,
)
from pydantic import BaseModel, Field

from . import base, veille
from .config import get_settings
from .connecteurs import adala, idarati, jurisprudence
from .logging_setup import configurer_logging, correlation_id
from .messaging import BusRabbitMQ
from .pipeline import DepotDocument, ingest_document
from .security import exiger_jeton_interne
from .stockage import creer_stockage

VERSION = "0.3.0"
logger = logging.getLogger(__name__)


async def _volumes_index(parametres) -> dict[str, int | None]:
    """Volumes d'indexation portés par le service recherche (API interne,
    timeout court) — None si le service est indisponible : le tableau de
    bord reste lisible en dégradation gracieuse."""
    entetes = {"X-Internal-Token": parametres.internal_token}
    cibles = {
        "segments": "/internal/segments/compte",
        "fiches": "/internal/fiches/compte",
        "decisions": "/internal/decisions/compte",
    }
    resultats: dict[str, int | None] = {}
    async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
        for cle, chemin in cibles.items():
            try:
                reponse = await client.get(
                    parametres.index_service_url + chemin, headers=entetes
                )
                reponse.raise_for_status()
                resultats[cle] = int(reponse.json()[f"nb_{cle}"])
            except Exception:  # noqa: BLE001 — dégradation gracieuse consignée
                logger.warning("volumes d'index indisponibles pour %s", cle)
                resultats[cle] = None
    return resultats


def _rendre_tableau_de_bord(
    stats: dict[str, Any],
    repartition: list[dict[str, Any]],
    collectes: list[dict[str, Any]],
    volumes_index: dict[str, int | None],
) -> str:
    """Page HTML autonome (aucun script, aucun appel externe)."""

    def echapper(valeur: Any) -> str:
        return html_mod.escape(str(valeur))

    def table_entetes(entetes: list[str]) -> str:
        return "<tr>" + "".join(f"<th>{echapper(e)}</th>" for e in entetes) + "</tr>"

    par_source: dict[str, dict[str, Any]] = {}
    for ligne in repartition:
        entree = par_source.setdefault(
            ligne["source"], {"documents": 0, "articles": 0, "classes": {}, "langues": {}}
        )
        entree["documents"] += ligne["documents"]
        entree["articles"] += ligne["articles"]
        entree["classes"][ligne["classe"]] = (
            entree["classes"].get(ligne["classe"], 0) + ligne["documents"]
        )
        entree["langues"][ligne["langue"]] = (
            entree["langues"].get(ligne["langue"], 0) + ligne["documents"]
        )

    lignes_sources = "".join(
        f"<tr><td>{echapper(source)}</td><td>{donnees['documents']}</td>"
        f"<td>{echapper(', '.join(f'{c} : {n}' for c, n in sorted(donnees['classes'].items())))}</td>"
        f"<td>{echapper(', '.join(f'{l} : {n}' for l, n in sorted(donnees['langues'].items())))}</td>"
        f"<td>{donnees['articles']}</td></tr>"
        for source, donnees in sorted(par_source.items())
    )

    lignes_classes = "".join(
        f"<tr><td>{classe}</td><td>{nombre}</td></tr>"
        for classe, nombre in sorted(stats["par_classe"].items())
    )

    lignes_collectes = "".join(
        f"<tr><td>#{collecte['id']}</td><td>{echapper(collecte['source'])}</td>"
        f"<td>{echapper(collecte['date_debut'])}</td>"
        f"<td>{echapper(json.dumps(collecte['volume'], ensure_ascii=False))}</td></tr>"
        for collecte in collectes
    )

    index_lignes = "".join(
        f"<tr><td>{echapper(cle)}</td>"
        f"<td>{echapper('indisponible' if valeur is None else valeur)}</td></tr>"
        for cle, valeur in volumes_index.items()
    )

    return f"""<!DOCTYPE html>
<html lang="fr">
<head><meta charset="utf-8"><title>M3ak — tableau de bord du corpus</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #1a1a2e; }}
h1 {{ font-size: 1.4rem; }} h2 {{ font-size: 1.1rem; margin-top: 2rem; }}
table {{ border-collapse: collapse; margin-top: 0.5rem; }}
th, td {{ border: 1px solid #ccc; padding: 0.3rem 0.7rem; text-align: left; }}
th {{ background: #eef1f7; }}
.totaux span {{ display: inline-block; margin-right: 1.5rem; }}
.mut {{ color: #666; }}
</style></head>
<body>
<h1>M3ak — tableau de bord du corpus</h1>
<p class="totaux">
<b>{stats['total_documents']}</b> documents ·
<b>{stats['total_articles']}</b> articles segmentés ·
<b>{stats['en_quarantaine']}</b> en quarantaine
</p>

<h2>Par source</h2>
<table>
{table_entetes(['source', 'documents', 'classes', 'langues', 'articles'])}
{lignes_sources}
</table>

<h2>Qualité d'extraction (toutes sources)</h2>
<table>
{table_entetes(['classe', 'documents'])}
{lignes_classes}
</table>

<h2>Volumes d'indexation (service recherche)</h2>
<table>
{table_entetes(['unité', 'volume'])}
{index_lignes}
</table>

<h2>Dernières collectes</h2>
<table>
{table_entetes(['session', 'source', 'début', 'statuts'])}
{lignes_collectes}
</table>

<p class="mut">Lecture seule — agrégats uniquement. Régénéré à chaque
chargement ; les volumes d'indexation viennent de l'API interne du
service recherche.</p>
</body></html>"""


class ChargeCollecteAdala(BaseModel):
    """Paramètres de lancement d'une collecte Adala."""

    theme: int
    limite: int = Field(default=200, ge=1, le=1000)
    dry_run: bool = False


class ChargeCollecteIdarati(BaseModel):
    """Paramètres de lancement d'une collecte Idarati (thématiques du menu)."""

    thematiques: list[str] = Field(min_length=1)
    limite: int = Field(default=1000, ge=1, le=5000)
    dry_run: bool = False


class ChargeCollecteJurisprudence(BaseModel):
    """Paramètres de lancement d'une collecte Jurisprudence.ma (slugs de
    thématiques, bornes optionnelles par thématique — intégral si absent)."""

    thematiques: list[str] = Field(min_length=1)
    limites: dict[str, int] = Field(default_factory=dict)
    dry_run: bool = False

@contextlib.asynccontextmanager
async def duree_de_vie(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configurer_logging(settings.log_level)
    if settings.database_url:
        await base.initialiser_base(settings.database_url)
    bus = BusRabbitMQ(settings.rabbitmq_url or "", settings.service_name)
    await bus.connecter()
    await bus.declarer_file_travail(FILE_SEGMENTS_TO_INDEX)
    await bus.declarer_file_travail(FILE_FICHES_TO_INDEX)
    await bus.declarer_file_travail(FILE_DECISIONS_TO_INDEX)
    app.state.bus = bus
    app.state.stockage = creer_stockage(settings)
    yield
    app.state.bus = None
    app.state.stockage = None
    await bus.fermer()
    await base.fermer_base()


def create_app() -> FastAPI:
    """Fabrique de l'application — facilite les tests sans infrastructure."""
    application = FastAPI(
        title="M3ak — service corpus",
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
        return ReponseSante(status="ok", service="corpus", version=VERSION)

    @application.get("/ready", response_model=ReponsePret)
    async def pret(_: str = Depends(exiger_jeton_interne)) -> ReponsePret:
        """Prêt à servir — protégé par jeton interne."""
        settings = get_settings()
        dependances: list[EtatDependance] = []
        if settings.database_url:
            dependances.append(
                EtatDependance(nom="base", etat="ok" if await base.verifier_base() else "degrade")
            )
        statut = "ok" if all(dep.etat == "ok" for dep in dependances) else "degrade"
        return ReponsePret(status=statut, service="corpus", dependances=dependances)

    @application.get("/internal/echo", response_model=ReponseEcho)
    async def echo(_: str = Depends(exiger_jeton_interne)) -> ReponseEcho:
        """Écho protégé — vérifie jeton et propagation du correlation ID."""
        return ReponseEcho(service="corpus", correlation_id=correlation_id.get())

    @application.post("/internal/documents/ingester")
    async def ingester_document(
        fichier: Annotated[UploadFile, File()],
        reference: Annotated[str, Form()],
        titre: Annotated[str, Form()],
        langue: Annotated[str, Form()] = "",
        source: Annotated[str, Form()] = "adala",
        url_source: Annotated[str | None, Form()] = None,
        _: str = Depends(exiger_jeton_interne),
    ) -> dict:
        """Ingestion d'un document de bout en bout (phase 1) : couche brute,
        extraction, segmentation, scoring qualité A/B/C (C = quarantaine,
        jamais indexée), couche normalisée, publication des segments."""
        parametres = get_settings()
        if not parametres.database_url:
            raise HTTPException(status_code=503, detail="base non configuree")
        bus: BusRabbitMQ | None = getattr(application.state, "bus", None)
        if bus is None:
            raise HTTPException(status_code=503, detail="bus non initialise")
        stockage = getattr(application.state, "stockage", None)
        if stockage is None:
            raise HTTPException(status_code=503, detail="stockage non initialise")

        contenu = await fichier.read()
        if not contenu:
            raise HTTPException(status_code=400, detail="fichier vide")
        depot = DepotDocument(
            reference=reference,
            titre=titre,
            langue_indiquee=langue if langue in ("ar", "fr") else None,
            source=source,
            url_source=url_source,
        )
        bilan = await ingest_document(contenu, depot, bus, stockage)
        return {
            "document_id": bilan.document_id,
            "empreinte_sha256": bilan.empreinte_sha256,
            "statut": bilan.statut,
            "classe_qualite": bilan.classe_qualite,
            "nb_articles": bilan.nb_articles,
            "nb_messages_publies": bilan.nb_messages_publies,
        }

    @application.get("/internal/documents/stats")
    async def stats_documents(_: str = Depends(exiger_jeton_interne)) -> dict:
        """Volumétrie et qualité du corpus — base du tableau de bord phase 1."""
        parametres = get_settings()
        if not parametres.database_url:
            raise HTTPException(status_code=503, detail="base non configuree")
        return await base.stats_documents()

    @application.get("/tableau-de-bord")
    async def tableau_de_bord() -> Response:
        """Vue HTML en lecture seule : volumétrie et qualité du corpus
        (base du service) et volumes d'indexation (API interne du service
        recherche). Agrégats uniquement — aucune donnée de contenu ; la
        page est ouverte pour être ouverte depuis un navigateur."""
        parametres = get_settings()
        if not parametres.database_url:
            raise HTTPException(status_code=503, detail="base non configuree")
        stats = await base.stats_documents()
        repartition = await base.repartition_documents()
        collectes = await base.dernieres_collectes()
        volumes_index = await _volumes_index(parametres)
        return Response(
            _rendre_tableau_de_bord(stats, repartition, collectes, volumes_index),
            media_type="text/html; charset=utf-8",
        )

    @application.get("/internal/connecteurs/adala/themes")
    async def themes_adala(_: str = Depends(exiger_jeton_interne)) -> dict:
        """Thèmes racine d'Adala avec leur volumétrie."""
        async with adala._client() as client:
            liste = await adala.themes(client)
        return {"themes": liste}

    @application.post("/internal/connecteurs/adala/executer")
    async def executer_adala(
        charge: ChargeCollecteAdala, _: str = Depends(exiger_jeton_interne)
    ) -> dict:
        """Lance une collecte Adala en tâche de fond (une seule à la fois)."""
        parametres = get_settings()
        if not parametres.database_url:
            raise HTTPException(status_code=503, detail="base non configuree")
        if adala.ETAT.en_cours:
            raise HTTPException(status_code=409, detail="une collecte est deja en cours")
        bus: BusRabbitMQ | None = getattr(application.state, "bus", None)
        stockage = getattr(application.state, "stockage", None)
        if bus is None or stockage is None:
            raise HTTPException(status_code=503, detail="bus ou stockage non initialise")

        async def _tache() -> None:
            try:
                await adala.executer_collecte(
                    bus, stockage, charge.theme, charge.limite, charge.dry_run
                )
            except Exception:
                logger.exception("collecte adala interrompue (theme=%s)", charge.theme)
                adala.ETAT.en_cours = False

        asyncio.create_task(_tache())
        return {"statut": "demarree", "theme": charge.theme, "limite": charge.limite, "dry_run": charge.dry_run}

    @application.get("/internal/connecteurs/adala/statut")
    async def statut_adala(_: str = Depends(exiger_jeton_interne)) -> dict:
        """État de la dernière collecte Adala (journal borné)."""
        return {
            "en_cours": adala.ETAT.en_cours,
            "theme": adala.ETAT.theme,
            "dry_run": adala.ETAT.dry_run,
            "debut": adala.ETAT.debut,
            "fin": adala.ETAT.fin,
            "statuts": adala.ETAT.statuts,
            "journal": list(adala.ETAT.journal),
        }

    @application.get("/internal/connecteurs/idarati/thematiques")
    async def thematiques_idarati(_: str = Depends(exiger_jeton_interne)) -> dict:
        """Thématiques du menu des catégories Idarati (aplaties)."""
        async with idarati._client() as client:
            liste = await idarati.thematiques(client)
        return {"thematiques": liste}

    @application.post("/internal/connecteurs/idarati/executer")
    async def executer_idarati(
        charge: ChargeCollecteIdarati, _: str = Depends(exiger_jeton_interne)
    ) -> dict:
        """Lance une collecte Idarati en tâche de fond (une seule à la fois)."""
        parametres = get_settings()
        if not parametres.database_url:
            raise HTTPException(status_code=503, detail="base non configuree")
        if idarati.ETAT.en_cours:
            raise HTTPException(status_code=409, detail="une collecte est deja en cours")
        bus: BusRabbitMQ | None = getattr(application.state, "bus", None)
        stockage = getattr(application.state, "stockage", None)
        if bus is None or stockage is None:
            raise HTTPException(status_code=503, detail="bus ou stockage non initialise")

        async def _tache() -> None:
            try:
                await idarati.executer_collecte(
                    bus, stockage, charge.thematiques, charge.limite, charge.dry_run
                )
            except Exception:
                logger.exception(
                    "collecte idarati interrompue (%d thematiques)", len(charge.thematiques)
                )
                idarati.ETAT.en_cours = False

        asyncio.create_task(_tache())
        return {
            "statut": "demarree",
            "thematiques": charge.thematiques,
            "limite": charge.limite,
            "dry_run": charge.dry_run,
        }

    @application.get("/internal/connecteurs/idarati/statut")
    async def statut_idarati(_: str = Depends(exiger_jeton_interne)) -> dict:
        """État de la dernière collecte Idarati (journal borné)."""
        return {
            "en_cours": idarati.ETAT.en_cours,
            "thematiques": idarati.ETAT.thematiques,
            "dry_run": idarati.ETAT.dry_run,
            "debut": idarati.ETAT.debut,
            "fin": idarati.ETAT.fin,
            "statuts": idarati.ETAT.statuts,
            "journal": list(idarati.ETAT.journal),
        }

    @application.post("/internal/connecteurs/jurisprudence/executer")
    async def executer_jurisprudence(
        charge: ChargeCollecteJurisprudence, _: str = Depends(exiger_jeton_interne)
    ) -> dict:
        """Lance une collecte Jurisprudence.ma en tâche de fond (une seule
        à la fois). Le référentiel des thématiques vit dans
        ``pilot/jurisprudence_volumetrie.json``."""
        parametres = get_settings()
        if not parametres.database_url:
            raise HTTPException(status_code=503, detail="base non configuree")
        if jurisprudence.ETAT.en_cours:
            raise HTTPException(status_code=409, detail="une collecte est deja en cours")
        bus: BusRabbitMQ | None = getattr(application.state, "bus", None)
        stockage = getattr(application.state, "stockage", None)
        if bus is None or stockage is None:
            raise HTTPException(status_code=503, detail="bus ou stockage non initialise")

        async def _tache() -> None:
            try:
                await jurisprudence.executer_collecte(
                    bus, stockage, charge.thematiques, charge.limites, charge.dry_run
                )
            except Exception:
                logger.exception(
                    "collecte jurisprudence interrompue (%d thematiques)",
                    len(charge.thematiques),
                )
                jurisprudence.ETAT.en_cours = False

        asyncio.create_task(_tache())
        return {
            "statut": "demarree",
            "thematiques": charge.thematiques,
            "limites": charge.limites,
            "dry_run": charge.dry_run,
        }

    @application.get("/internal/connecteurs/jurisprudence/statut")
    async def statut_jurisprudence(_: str = Depends(exiger_jeton_interne)) -> dict:
        """État de la dernière collecte Jurisprudence.ma (journal borné)."""
        return {
            "en_cours": jurisprudence.ETAT.en_cours,
            "thematiques": jurisprudence.ETAT.thematiques,
            "dry_run": jurisprudence.ETAT.dry_run,
            "debut": jurisprudence.ETAT.debut,
            "fin": jurisprudence.ETAT.fin,
            "statuts": jurisprudence.ETAT.statuts,
            "journal": list(jurisprudence.ETAT.journal),
        }

    @application.post("/internal/veille/executer")
    async def executer_veille(_: str = Depends(exiger_jeton_interne)) -> dict:
        """Lance un scan léger des sources (diff nouveaux/disparus/modifiés,
        aucun téléchargement) en tâche de fond ; le rapport est tracé en
        manifeste."""
        parametres = get_settings()
        if not parametres.database_url:
            raise HTTPException(status_code=503, detail="base non configuree")
        if veille.ETAT.en_cours:
            raise HTTPException(status_code=409, detail="une veille est deja en cours")

        async def _tache() -> None:
            try:
                await veille.executer_veille()
            except Exception:
                logger.exception("veille interrompue")
                veille.ETAT.en_cours = False

        asyncio.create_task(_tache())
        return {"statut": "demarree"}

    @application.get("/internal/veille/statut")
    async def statut_veille(_: str = Depends(exiger_jeton_interne)) -> dict:
        """État de la dernière veille : rapport du dernier scan terminé."""
        return {
            "en_cours": veille.ETAT.en_cours,
            "debut": veille.ETAT.debut,
            "fin": veille.ETAT.fin,
            "rapport": veille.ETAT.rapport,
            "journal": list(veille.ETAT.journal),
        }

    @application.get("/internal/documents/brut/{empreinte}")
    async def lire_brut(empreinte: str, _: str = Depends(exiger_jeton_interne)) -> Response:
        """Renvoie un document de la couche brute (lecture seule).

        Sert aux outils de re-segmentation : quand la grammaire évolue, on
        rejoue le pipeline sur les contenus déjà collectés sans rien
        re-télécharger depuis la source.
        """
        if not re.fullmatch(r"[0-9a-f]{64}", empreinte):
            raise HTTPException(status_code=400, detail="empreinte invalide")
        stockage = getattr(application.state, "stockage", None)
        if stockage is None:
            raise HTTPException(status_code=503, detail="stockage non initialise")
        source = "adala"
        try:
            contenu = await asyncio.to_thread(
                stockage.lire, f"{source}/{empreinte}.pdf"
            )
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="document brut introuvable")
        return Response(
            content=contenu,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{empreinte}.pdf"'},
        )

    @application.get("/internal/documents/{document_id}/pdf")
    async def lire_pdf_document(
        document_id: str, _: str = Depends(exiger_jeton_interne)
    ) -> Response:
        """Le PDF brut d'un document, par identifiant stable — pour la
        CONSULTATION dans l'interface (rendu canvas côté navigateur, sans
        lien de téléchargement). Le navigateur ne l'atteint jamais
        directement : seul le proxy du frontend le convoque, avec le jeton
        interne resté côté serveur, et aucun listage public n'existe."""
        fiche = await base.lire_document(document_id)
        if fiche is None:
            raise HTTPException(status_code=404, detail="document inconnu")
        # Les fiches (Idarati) et les décisions (Jurisprudence.ma) sont des
        # JSON : pas de PDF à montrer ; la quarantaine n'est jamais citée
        # dans les réponses et ne se consulte pas non plus.
        if fiche["en_quarantaine"] or not fiche["chemin_brut"].endswith(".pdf"):
            raise HTTPException(
                status_code=404, detail="aucun PDF consultable pour ce document"
            )
        stockage = getattr(application.state, "stockage", None)
        if stockage is None:
            raise HTTPException(status_code=503, detail="stockage non initialise")
        cle_brut = fiche["chemin_brut"]
        if cle_brut.startswith("minio://"):
            # Le dépôt MinIO conserve une URI (minio://<bucket>/<clé>) ;
            # la lecture, elle, attend la clé seule.
            cle_brut = cle_brut[len("minio://") :].split("/", 1)[1]
        try:
            contenu = await asyncio.to_thread(stockage.lire, cle_brut)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="document brut introuvable")
        return Response(content=contenu, media_type="application/pdf")

    @application.get("/internal/documents/{document_id}/fiche")
    async def lire_fiche_document(
        document_id: str, _: str = Depends(exiger_jeton_interne)
    ) -> dict:
        """La fiche Idarati STRUCTURÉE (JSON brut tel que servi par la
        source) + ses pièces requises si elles ont été collectées — pour la
        checklist de démarche de l'interface (étapes cochables). Même
        discipline que le PDF : jeton interne, jamais atteint directement
        par le navigateur, quarantaine exclue. Les pièces vivent sous la
        clé dérivée ``idarati/{empreinte}.pieces.json`` ; leur absence
        (fiches collectées avant l'enrichissement) n'est pas une erreur."""
        fiche = await base.lire_document(document_id)
        if fiche is None or fiche["source"] != "idarati" or fiche["en_quarantaine"]:
            raise HTTPException(status_code=404, detail="fiche inconnue")
        stockage = getattr(application.state, "stockage", None)
        if stockage is None:
            raise HTTPException(status_code=503, detail="stockage non initialise")
        cle_brut = fiche["chemin_brut"]
        if cle_brut.startswith("minio://"):
            # Le dépôt MinIO conserve une URI ; la lecture attend la clé seule.
            cle_brut = cle_brut[len("minio://") :].split("/", 1)[1]
        if not cle_brut.endswith(".json"):
            raise HTTPException(status_code=404, detail="fiche inconnue")
        try:
            contenu_fiche = await asyncio.to_thread(stockage.lire, cle_brut)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="document brut introuvable")
        pieces = None
        try:
            contenu_pieces = await asyncio.to_thread(
                stockage.lire, cle_brut[: -len(".json")] + ".pieces.json"
            )
            pieces = json.loads(contenu_pieces)
        except FileNotFoundError:
            pass
        return {
            "document_id": document_id,
            "titre": fiche["titre"],
            "fiche": json.loads(contenu_fiche),
            "pieces": pieces,
        }

    @application.post("/internal/documents/classement/reorganiser")
    async def reorganiser_classement(_: str = Depends(exiger_jeton_interne)) -> dict:
        """Régénère la vue de classement MinIO : classe/<langue>/<A-fiable|
        B-marque|C-quarantaine>/<empreinte>.pdf (copies server-side ; la
        couche brute par empreinte ne bouge jamais)."""
        parametres = get_settings()
        if not parametres.database_url:
            raise HTTPException(status_code=503, detail="base non configuree")
        stockage = getattr(application.state, "stockage", None)
        if stockage is None:
            raise HTTPException(status_code=503, detail="stockage non initialise")

        documents = await base.documents_pour_classement()
        supprimes = await asyncio.to_thread(stockage.supprimer_prefixe, "classe/")
        dossiers = {"A": "A-fiable", "B": "B-marque", "C": "C-quarantaine"}
        nb_copies = 0
        for document in documents:
            destination = f"classe/{document['langue']}/{dossiers[document['classe']]}/{document['empreinte']}.pdf"
            await asyncio.to_thread(
                stockage.copier, document["chemin_brut"], destination
            )
            nb_copies += 1
        logger.info(
            "vue classement regeneree : %d copies (%d anciennes supprimees)", nb_copies, supprimes
        )
        return {"copies": nb_copies, "anciennes_supprimees": supprimes}

    return application


application = create_app()
