"""Connecteur Adala (adala.justice.gov.ma) — corpus principal des textes
normatifs (phase 1).

Parcours par thème via la recherche du site (métadonnées structurées dans
le JSON ``__NEXT_DATA__`` des pages), choix de la meilleure version de
chaque texte (version consolidée à privilégier, AGENTS.md §3.1),
téléchargement des PDF, ingestion via le pipeline du service.

Règles de collecte (AGENTS.md §3.2) : rythme raisonnable (une requête par
~1,2 s), agent utilisateur identifiable, manifeste de session en base.
L'idempotence du pipeline (empreinte du contenu) fait qu'une nouvelle
collecte ne retraite jamais un document déjà ingéré.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from .. import base
from ..messaging import BusRabbitMQ
from ..pipeline import DepotDocument, ingest_document
from ..stockage import StockageBrut

logger = logging.getLogger(__name__)

HOTE = "adala.justice.gov.ma"
AGENT_UTILISATEUR = "M3ak-connecteur/0.1 (projet educatif gratuit; contact via le depot public)"
DELAI_SECONDES = 1.2
TAILLE_MAX_OCTETS = 80 * 1024 * 1024
JOURNAL_MAX_LIGNES = 60


@dataclass
class ResultatAdala:
    """Un document tel qu'exposé par la recherche Adala."""

    reference: str  # lawNumber (dahir de promulgation ou numéro)
    titre: str
    chemin_pdf: str  # chemin Adala « uploads/… »
    type_texte: str = ""
    theme: str = ""
    date_publication: str | None = None
    langue_indiquee: str | None = None  # champ « language » d'Adala (AR/FR)

    @property
    def url_source(self) -> str:
        return f"https://{HOTE}/api/uploads/{self.chemin_pdf[len('uploads/'):]}"


@dataclass
class EtatCollecte:
    """État partageé de la dernière collecte (suivi par /statut)."""

    en_cours: bool = False
    theme: int | None = None
    dry_run: bool = False
    debut: str | None = None
    fin: str | None = None
    statuts: dict[str, int] = field(default_factory=dict)
    journal: list[str] = field(default_factory=list)

    def noter(self, message: str) -> None:
        horodatage = datetime.now(UTC).strftime("%H:%M:%S")
        self.journal.append(f"{horodatage} {message}")
        del self.journal[:-JOURNAL_MAX_LIGNES]


ETAT = EtatCollecte()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=f"https://{HOTE}",
        headers={"User-Agent": AGENT_UTILISATEUR},
        timeout=60,
        follow_redirects=False,
    )


def _page_props(html: str) -> dict:
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL
    )
    if not match:
        raise ValueError("page Adala sans __NEXT_DATA__")
    return json.loads(match.group(1))["props"]["pageProps"]


def _resultat_depuis_entree(entree: dict) -> ResultatAdala:
    metadonnees = entree.get("fileMeta", {}) or {}
    numero = (metadonnees.get("lawNumber") or "").strip()
    chemin = entree.get("path") or ""
    if not numero:
        # Référence de repli unique par document (circulaires, décisions,
        # textes non numérotés) : empreinte courte du chemin Adala —
        # déterministe et stable, une clé partagée ferait entrer chaque
        # texte sans numéro en collision avec le premier venu.
        numero = "adala-" + hashlib.sha256(chemin.encode("utf-8")).hexdigest()[:12]
    return ResultatAdala(
        reference=numero,
        titre=(entree.get("name") or "").strip() or "sans-titre",
        chemin_pdf=chemin,
        type_texte=(metadonnees.get("LawType") or {}).get("name", ""),
        theme=(metadonnees.get("theme") or {}).get("name", ""),
        date_publication=(metadonnees.get("gregorianDate") or "")[:10] or None,
        langue_indiquee=((entree.get("language") or "").strip().lower() or None),
    )


def _est_consolidee(resultat: ResultatAdala) -> bool:
    return "consolid" in (resultat.titre + resultat.chemin_pdf).lower()


def choisir_version(resultats: list[ResultatAdala]) -> list[ResultatAdala]:
    """Un seul document par référence : version consolidée à privilégier,
    sinon la plus récente ; ex aequo, la première rencontrée. Les textes
    sans numéro portent une référence unique (empreinte du chemin), donc
    ne sont jamais regroupés — sauf doublons exacts d'Adala (même chemin)."""
    groupes: dict[str, ResultatAdala] = {}
    for resultat in resultats:
        cle = resultat.reference
        actuel = groupes.get(cle)
        if actuel is None:
            groupes[cle] = resultat
            continue
        if _est_consolidee(resultat) and not _est_consolidee(actuel) or (
            _est_consolidee(resultat) == _est_consolidee(actuel)
            and (resultat.date_publication or "") > (actuel.date_publication or "")
        ):
            groupes[cle] = resultat
    return list(groupes.values())


async def themes(client: httpx.AsyncClient) -> list[dict]:
    """Thèmes racine d'Adala (clé, nom, volumétrie)."""
    reponse = await client.get("/fr/search", params={"term": "", "page": 1})
    reponse.raise_for_status()
    brutes = _page_props(reponse.text).get("themesResult") or []
    return [
        {"cle": t.get("key"), "nom": t.get("name"), "documents": t.get("count", 0)}
        for t in brutes
    ]


async def chercher_par_theme(
    client: httpx.AsyncClient, theme: int, page: int
) -> tuple[list[ResultatAdala], int]:
    """Résultats d'une page de recherche par thème + total du thème."""
    reponse = await client.get("/fr/search", params={"term": "", "themes": theme, "page": page})
    reponse.raise_for_status()
    recherche = _page_props(reponse.text).get("searchResult") or {}
    total = recherche.get("total") or 0
    resultats = [_resultat_depuis_entree(e) for e in recherche.get("data") or []]
    return resultats, int(total)


def _suffixe_telechargement(chemin_pdf: str) -> str:
    """Chemin Adala validé par segments — la traversée se juge segment par
    segment (« .. » en tant que segment), pas en sous-chaîne : des noms de
    fichiers arabes contiennent légitimement des points consécutifs."""
    segments = chemin_pdf.split("/")
    if (
        len(segments) < 3
        or segments[0] != "uploads"
        or any(segment in ("", "..", ".") for segment in segments[1:])
        or ":" in chemin_pdf
        or "\\" in chemin_pdf
    ):
        raise ValueError(f"chemin Adala inattendu : {chemin_pdf!r}")
    return quote(chemin_pdf[len("uploads/") :])


async def telecharger_pdf(client: httpx.AsyncClient, resultat: ResultatAdala) -> bytes:
    reponse = await client.get("/api/uploads/" + _suffixe_telechargement(resultat.chemin_pdf))
    reponse.raise_for_status()
    contenu = reponse.content
    if len(contenu) > TAILLE_MAX_OCTETS or not contenu.startswith(b"%PDF-"):
        raise ValueError(f"reponse inattendue pour {resultat.chemin_pdf}")
    return contenu


async def executer_collecte(
    bus: BusRabbitMQ,
    stockage: StockageBrut,
    theme: int,
    limite: int = 200,
    dry_run: bool = False,
    etat: EtatCollecte | None = None,
) -> dict:
    """Parcourt un thème (borné par ``limite``) et ingère chaque document.

    En dry-run : recense sans rien télécharger ni ingérer. Retourne le
    manifeste de session (aussa conservé en base hors dry-run).
    """
    suivi = etat if etat is not None else ETAT
    suivi.en_cours = True
    suivi.theme = theme
    suivi.dry_run = dry_run
    suivi.debut = datetime.now(UTC).isoformat(timespec="seconds")
    suivi.statuts = {}
    vus: list[ResultatAdala] = []
    try:
        async with _client() as client:
            page = 1
            while len(vus) < limite:
                resultats, total = await chercher_par_theme(client, theme, page)
                if not resultats:
                    break
                vus.extend(choisir_version(resultats))
                suivi.noter(
                    f"page {page} : {len(resultats)} résultats ({len(vus)} documents retenus / {total})"
                )
                page += 1
                await asyncio.sleep(DELAI_SECONDES)
            vus = choisir_version(vus)[:limite]
            for resultat in vus:
                if dry_run:
                    suivi.statuts["vus"] = suivi.statuts.get("vus", 0) + 1
                    continue
                try:
                    contenu = await telecharger_pdf(client, resultat)
                    depot = DepotDocument(
                        reference=resultat.reference,
                        titre=resultat.titre,
                        langue_indiquee=resultat.langue_indiquee,
                        source="adala",
                    )
                    bilan = await ingest_document(contenu, depot, bus, stockage)
                    suivi.statuts[bilan.statut] = suivi.statuts.get(bilan.statut, 0) + 1
                    suivi.noter(f"{bilan.statut} : {resultat.reference} {resultat.titre[:50]}")
                except Exception as erreur:  # noqa: BLE001 — consigné, la collecte poursuit
                    suivi.statuts["echec"] = suivi.statuts.get("echec", 0) + 1
                    suivi.noter(f"echec : {resultat.reference} — {str(erreur)[:80]}")
                await asyncio.sleep(DELAI_SECONDES)
    finally:
        suivi.en_cours = False
        suivi.fin = datetime.now(UTC).isoformat(timespec="seconds")

    manifeste = base.ManifesteCollecte(
        source="adala",
        methode=f"recherche par theme {theme} (dry_run={dry_run}) + telechargement /api/uploads",
        agent_utilisateur=AGENT_UTILISATEUR,
        date_debut=datetime.fromisoformat(suivi.debut or ""),
        date_fin=datetime.fromisoformat(suivi.fin or ""),
        volume=dict(suivi.statuts),
        entrees=[
            {
                "reference": r.reference,
                "titre": r.titre,
                "chemin_pdf": r.chemin_pdf,
                "date_publication": r.date_publication,
            }
            for r in vus
        ],
    )
    if not dry_run and (suivi.statuts.get("insere") or suivi.statuts.get("insere_quarantaine")):
        await base.enregistrer_manifestes([manifeste])
    return manifeste.model_dump()
