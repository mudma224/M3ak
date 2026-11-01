"""Connecteur Idarati (procedures.idarati.ma) — fiches de procédures
administratives (phase 1).

Parcours par thématique via la recherche paginée du portail citoyen
(paramètre ``thematicId``), puis fiche complète par identifiant. Une fiche
est un document structuré servi par l'API (titre, description, délai,
frais, administrations, formulaires) : sans articles, elle n'emprunte ni
l'extraction PDF ni la segmentation par articles — la collecte la conserve
telle quelle en couche brute (``idarati/<empreinte>.json``) et
l'enregistre en classe A dans la couche normalisée (collection
``procedures``) : le contenu est structuré et complet par construction,
il n'y a rien à scorer. La couche index est alimentée sur la file dédiée
``fiches.to_index`` (contrat ``FicheAIndexer``) — gestion séparée des
segments d'articles.

Les liens vers les fiches originales sont ajoutés par la couche affichage
(après la V1) ; les identifiants sources permettent de les reconstruire.

Règles de collecte (AGENTS.md §3.2) : rythme raisonnable (une requête par
~1,2 s), agent utilisateur identifiable, manifeste de session en base.
L'idempotence (empreinte du JSON) fait qu'une re-collecte ne retraite
jamais une fiche déjà ingérée.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from m3ak_contracts import FILE_FICHES_TO_INDEX, ChargeFichesAIndexer, envelopper

from .. import base
from ..messaging import BusRabbitMQ
from ..normalisation import cle_document
from ..pipeline import decider_langue
from ..stockage import StockageBrut

logger = logging.getLogger(__name__)

HOTE = "procedures.idarati.ma"
AGENT_UTILISATEUR = "M3ak-connecteur/0.1 (projet educatif gratuit; contact via le depot public)"
DELAI_SECONDES = 1.2
TAILLE_PAGE = 200
TAILLE_MAX_OCTETS = 2 * 1024 * 1024
JOURNAL_MAX_LIGNES = 60


@dataclass
class ResultatIdarati:
    """Une fiche telle qu'exposée par la recherche du portail."""

    identifiant: str  # UUID de la fiche — identifiant de la source
    titre: str
    thematique_id: str
    administration: str = ""
    locale: str = ""  # annonce de langue du portail (ar)


@dataclass
class EtatCollecte:
    """État partagé de la dernière collecte (suivi par /statut)."""

    en_cours: bool = False
    thematiques: list[str] | None = None
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
        trust_env=False,
    )


async def thematiques(client: httpx.AsyncClient) -> list[dict]:
    """Thématiques du menu des catégories, aplaties depuis l'arborescence
    catégorie (individus / professionnels) → public → thématique."""
    reponse = await client.get("/api/informational/categories-menu")
    reponse.raise_for_status()
    menu = reponse.json()
    aplati: list[dict] = []
    for categorie in menu or []:
        for sous in categorie.get("subCategories") or []:
            for thematique in sous.get("thematics") or []:
                aplati.append(
                    {
                        "id": thematique.get("id"),
                        "titre": thematique.get("title"),
                        "public": sous.get("title"),
                        "categorie": categorie.get("title"),
                    }
                )
    return aplati


async def chercher_par_thematique(
    client: httpx.AsyncClient, thematique_id: str, page: int
) -> tuple[list[ResultatIdarati], int, bool]:
    """Une page de recherche par thématique (pagination 0-based) + total +
    indicateur « dernière page »."""
    reponse = await client.get(
        "/api/informational/procedures/search",
        params={"title": "", "thematicId": thematique_id, "page": page, "size": TAILLE_PAGE},
    )
    reponse.raise_for_status()
    corps = reponse.json()
    resultats = [
        ResultatIdarati(
            identifiant=entree.get("id") or "",
            titre=(entree.get("title") or "").strip() or "sans-titre",
            thematique_id=entree.get("thematicId") or thematique_id,
            administration=(entree.get("administrationTitle") or "").strip(),
        )
        for entree in corps.get("content") or []
    ]
    return resultats, int(corps.get("totalElements") or 0), bool(corps.get("last"))


async def recuperer_fiche(client: httpx.AsyncClient, resultat: ResultatIdarati) -> bytes:
    """Fiche complète, conservée telle que servie par l'API."""
    reponse = await client.get(f"/api/informational/procedures/{resultat.identifiant}")
    reponse.raise_for_status()
    contenu = reponse.content
    if len(contenu) > TAILLE_MAX_OCTETS:
        raise ValueError(f"fiche trop volumineuse : {resultat.identifiant}")
    detail = json.loads(contenu)
    if not isinstance(detail, dict) or detail.get("id") != resultat.identifiant:
        raise ValueError(f"reponse inattendue pour {resultat.identifiant}")
    return contenu


async def recuperer_pieces(client: httpx.AsyncClient, identifiant: str) -> bytes | None:
    """Pièces requises de la fiche (endpoint ``procedures/{id}/documents``,
    groupées par sections du type « الوثائق المطلوبة »), conservées telles
    que servies par l'API. None si l'endpoint répond 404 (fiche sans
    pièces) ; les autres erreurs HTTP lèvent — la collecte consigne et
    poursuit, l'outil de ré-enrichissement repassera."""
    reponse = await client.get(
        f"/api/informational/procedures/{identifiant}/documents"
    )
    if reponse.status_code == 404:
        return None
    reponse.raise_for_status()
    contenu = reponse.content
    if len(contenu) > TAILLE_MAX_OCTETS:
        raise ValueError(f"pieces trop volumineuses : {identifiant}")
    json.loads(contenu)  # validité JSON avant stockage
    return contenu


def _texte_detectable(detail: dict) -> str:
    """Champs textuels humains de la fiche — détection de langue sans les
    identifiants techniques (UUID, horodatages)."""
    morceaux = [str(detail.get(cle) or "") for cle in
                ("title", "description", "delay", "delayFix", "price",
                 "orientationRule", "validityDurationInDays")]
    for cle in ("administrationInCharge", "receivingAdministrations", "deliveringAdministrations"):
        bloc = detail.get(cle)
        if isinstance(bloc, dict):
            morceaux.append(str(bloc.get("title") or ""))
        elif isinstance(bloc, list):
            morceaux.extend(str(b.get("title") or "") for b in bloc if isinstance(b, dict))
    return "\n".join(m for m in morceaux if m)


async def ingester_fiche(
    contenu: bytes,
    resultat: ResultatIdarati,
    bus: BusRabbitMQ,
    stockage: StockageBrut,
    pieces: bytes | None = None,
) -> str:
    """Couche brute (JSON) + couche normalisée (collection procedures,
    classe A) + publication sur la file dédiée ``fiches.to_index``.
    Idempotence : même empreinte n'importe sous quelle clé → déjà présent ;
    contenu différent sous la même clé → refusé (la veille gère l'évolution).
    `pieces` : réponse brute de ``procedures/{id}/documents`` (None si la
    fiche n'en a pas) — stockée à côté de la fiche sous
    ``idarati/{empreinte}.pieces.json``, clé dérivable de ``chemin_brut``
    sans toucher au schéma de la couche normalisée."""
    empreinte = hashlib.sha256(contenu).hexdigest()
    carte = await base.carte_documents()
    if empreinte in carte.values():
        return "deja_present"

    detail = json.loads(contenu)
    texte = _texte_detectable(detail)
    langue = decider_langue(texte, resultat.titre, resultat.locale or None, pages=1)
    if langue in ("ar", "fr"):
        document_id = cle_document(resultat.identifiant, langue)
    else:
        # Langue indécidable : la clé ne passe pas par le contrat Langue.
        document_id = f"{resultat.identifiant}|inconnue|originale"

    empreinte_connue = carte.get(document_id)
    if empreinte_connue == empreinte:
        return "deja_present"
    if empreinte_connue is not None:
        logger.warning(
            "version differente refusee : %s (connue %s…, nouvelle %s…)",
            document_id, empreinte_connue[:12], empreinte[:12],
        )
        return "version_differente"

    chemin_brut = await asyncio.to_thread(
        stockage.deposer, f"idarati/{empreinte}.json", contenu
    )
    if pieces is not None:
        # Clé dérivée de l'empreinte de la fiche : l'endpoint de lecture
        # retrouve les pièces depuis chemin_brut, sans colonne dédiée.
        await asyncio.to_thread(
            stockage.deposer, f"idarati/{empreinte}.pieces.json", pieces
        )
    fiche = base.DocumentNormalise(
        document_id=document_id,
        empreinte_sha256=empreinte,
        source="idarati",
        titre=resultat.titre,
        langue=langue,
        chemin_brut=chemin_brut,
        pages=1,
        caracteres=len(texte),
        nb_articles=0,
        score_numerotation=0.0,
        classe_qualite="A",
        en_quarantaine=False,
        avertissements=[],
        date_ingestion=datetime.now(UTC),
    )
    insere = await base.enregistrer_documents([fiche])

    nb_messages = 0
    if insere and langue in ("ar", "fr"):
        enveloppe = envelopper(
            ChargeFichesAIndexer(
                fiches=[
                    {
                        "fiche_id": document_id,
                        "document_id": document_id,
                        "titre": resultat.titre,
                        "langue": langue,
                        "texte": texte,
                        "thematique_id": resultat.thematique_id,
                        "administration": resultat.administration or None,
                    }
                ]
            ),
            correlation_id=f"ingestion-fiche-{empreinte[:12]}",
            emis_par="corpus",
        )
        await bus.publier(FILE_FICHES_TO_INDEX, enveloppe)
        nb_messages = 1

    logger.info(
        "ingestion fiche %s : langue=%s caracteres=%d inseres=%d messages=%d",
        document_id, langue, len(texte), insere, nb_messages,
    )
    return "insere" if insere else "deja_present"


async def executer_collecte(
    bus: BusRabbitMQ,
    stockage: StockageBrut,
    thematique_ids: list[str],
    limite: int = 1000,
    dry_run: bool = False,
    etat: EtatCollecte | None = None,
) -> dict:
    """Parcourt chaque thématique (bornée par ``limite`` fiches chacune) et
    ingère chaque fiche. En dry-run : recense sans rien télécharger ni
    ingérer. Retourne le manifeste de session (aussi conservé en base hors
    dry-run)."""
    suivi = etat if etat is not None else ETAT
    suivi.en_cours = True
    suivi.thematiques = list(thematique_ids)
    suivi.dry_run = dry_run
    suivi.debut = datetime.now(UTC).isoformat(timespec="seconds")
    suivi.statuts = {}
    vus: list[ResultatIdarati] = []
    try:
        async with _client() as client:
            for thematique_id in thematique_ids:
                page = 0
                vus_theme = 0
                while True:
                    resultats, total, derniere = await chercher_par_thematique(
                        client, thematique_id, page
                    )
                    if not resultats:
                        break
                    retenus = resultats[: max(limite - vus_theme, 0)]
                    vus.extend(retenus)
                    vus_theme += len(retenus)
                    suivi.noter(
                        f"thematique {thematique_id[:8]} page {page} : "
                        f"{len(resultats)} fiches ({vus_theme}/{total})"
                    )
                    if derniere or vus_theme >= limite:
                        break
                    page += 1
                    await asyncio.sleep(DELAI_SECONDES)
                await asyncio.sleep(DELAI_SECONDES)

            for resultat in vus:
                if dry_run:
                    suivi.statuts["vus"] = suivi.statuts.get("vus", 0) + 1
                    continue
                try:
                    contenu = await recuperer_fiche(client, resultat)
                    pieces = await recuperer_pieces(client, resultat.identifiant)
                    statut = await ingester_fiche(
                        contenu, resultat, bus, stockage, pieces=pieces
                    )
                    suivi.statuts[statut] = suivi.statuts.get(statut, 0) + 1
                    suivi.noter(f"{statut} : {resultat.identifiant[:8]} {resultat.titre[:50]}")
                except Exception as erreur:  # noqa: BLE001 — consigné, la collecte poursuit
                    suivi.statuts["echec"] = suivi.statuts.get("echec", 0) + 1
                    suivi.noter(f"echec : {resultat.identifiant[:8]} — {str(erreur)[:80]}")
                await asyncio.sleep(DELAI_SECONDES)
    finally:
        suivi.en_cours = False
        suivi.fin = datetime.now(UTC).isoformat(timespec="seconds")

    manifeste = base.ManifesteCollecte(
        source="idarati",
        methode=(
            f"recherche par thematique ({len(thematique_ids)} ids, dry_run={dry_run}) "
            "+ fiche complete /api/informational/procedures/{id}"
        ),
        agent_utilisateur=AGENT_UTILISATEUR,
        date_debut=datetime.fromisoformat(suivi.debut or ""),
        date_fin=datetime.fromisoformat(suivi.fin or ""),
        volume=dict(suivi.statuts),
        entrees=[
            {
                "reference": r.identifiant,
                "titre": r.titre,
                "thematique_id": r.thematique_id,
            }
            for r in vus
        ],
    )
    if not dry_run and (suivi.statuts.get("insere") or suivi.statuts.get("insere_quarantaine")):
        await base.enregistrer_manifestes([manifeste])
    return manifeste.model_dump()
