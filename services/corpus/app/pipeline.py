"""Pipeline d'ingestion du service corpus (phase 1).

Un document entre en bytes avec ses métadonnées de source, et resort :

    contenu → couche brute (stockage fichier, jamais modifiée)
            → extraction du texte natif (pypdf)
            → segmentation (grammaire validée au jalon B)
            → scoring qualité A/B/C (C = quarantaine, jamais indexée)
            → couche normalisée (table documents)
            → publication des segments vers segments.to_index (si non C)

Idempotence : la clé (identifiant stable) et l'empreinte du contenu
décident — un contenu déjà ingéré n'est jamais retraité, une version
différente d'un document connu est refusée (la gestion multi-versions
appartient à la veille).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from m3ak_contracts import FILE_SEGMENTS_TO_INDEX, ChargeSegmentsAIndexer, envelopper

from . import base
from .messaging import BusRabbitMQ
from .normalisation import cle_document, construire_lot
from .segmentation import classer, segmenter
from .stockage import StockageBrut

logger = logging.getLogger(__name__)


@dataclass
class DepotDocument:
    """Ce qu'il faut savoir d'un document au moment de son dépôt."""

    reference: str  # référence de promulgation (ou identifiant source)
    titre: str
    langue_indiquee: str | None = None  # annonce de la source (ex. Adala « AR »)
    source: str = "adala"
    url_source: str | None = None


def _langue_du_titre(titre: str) -> str | None:
    arabes = sum(1 for c in titre if "\u0600" <= c <= "\u06ff")
    return "ar" if arabes > 2 else None


# La détection du contenu exige des lettres ET une densité par page : les
# en-têtes d'un scan de 200 pages accumulent des centaines de lettres
# latines sans que le document soit français. Mesuré sur le corpus réel :
# vrai texte (classes A/B) ≥ 243 caractères/page, extraction de scan ≤ 60.
SEUIL_LETTRES_DETECTION = 50
SEUIL_DENSITE_DETECTION = 100


def decider_langue(
    texte: str, titre: str, langue_indiquee: str | None, pages: int = 1
) -> str:
    """Langue du document : un titre arabe prime sur tout ; sinon détection
    du contenu si le texte extrait est assez riche (lettres et densité) ;
    sinon l'annonce de la source ; sinon « inconnue » (quarantaine, jamais
    indexée).

    Le titre arabe est le signal le plus fort (cas réel : les lois de
    finances 2006-2009 en PDF à polices arabes cassées — sans table
    ToUnicode, l'extraction produit des codes de glyphes latins parasites
    qui font détecter « fr » à un document entièrement arabe). Un document
    français ne porte jamais de titre arabe sur Adala.
    """
    if _langue_du_titre(titre):
        return "ar"
    arabes = sum(1 for c in texte if "\u0600" <= c <= "\u06ff")
    latines = sum(1 for c in texte if c.isascii() and c.isalpha())
    densite = len(texte) / max(pages, 1)
    fiable = (
        max(arabes, latines) >= SEUIL_LETTRES_DETECTION
        and densite >= SEUIL_DENSITE_DETECTION
    )
    if fiable:
        return "ar" if arabes > latines else "fr"
    if langue_indiquee in ("ar", "fr"):
        return langue_indiquee
    return "inconnue"


@dataclass
class BilanIngestion:
    """Résultat d'une ingestion, retourné à l'appelant."""

    document_id: str
    empreinte_sha256: str
    statut: str  # insere | insere_quarantaine | deja_present | version_differente
    classe_qualite: str | None = None
    nb_articles: int = 0
    nb_messages_publies: int = 0


def extraire_texte_pdf(contenu: bytes) -> tuple[str, int]:
    """Extraction du texte natif ; un PDF illisible donne une chaîne vide
    (le scoring l'enverra en quarantaine, jamais indexé en silence)."""
    from io import BytesIO

    from pypdf import PdfReader

    try:
        lecteur = PdfReader(BytesIO(contenu))
        pages = len(lecteur.pages)
        texte = "\n".join((page.extract_text() or "") for page in lecteur.pages)
    except Exception as erreur:  # noqa: BLE001 — consigné, la quarantaine décide
        logger.warning("extraction pdf impossible : %s", erreur)
        return "", 0
    return texte, pages


async def ingest_document(
    contenu: bytes,
    depot: DepotDocument,
    bus: BusRabbitMQ,
    stockage: StockageBrut,
) -> BilanIngestion:
    """Traite un document de bout en bout (voir docstring du module).

    La langue de l'identifiant stable est DÉTECTÉE du contenu (Adala
    mélange documents arabes et français dans un même thème) :
    ``depot.langue_indiquee`` n'est qu'un indice. La déduplication par
    empreinte se fait avant extraction — une re-collecte ne retraite
    jamais un contenu déjà ingéré.
    """
    empreinte = hashlib.sha256(contenu).hexdigest()

    carte = await base.carte_documents()
    if empreinte in carte.values():
        # Déduplication inter-sources : le même contenu est déjà ingéré
        # sous une autre clé — rien à faire. La clé du bilan est indicative
        # (la langue réelle n'est pas encore détectée) : elle ne passe pas
        # par le contrat Langue, qui ne connaît que ar|fr.
        indice = depot.langue_indiquee if depot.langue_indiquee in ("ar", "fr") else "inconnue"
        return BilanIngestion(
            f"{depot.reference}|{indice}|originale", empreinte, "deja_present"
        )

    # Extraction + segmentation + scoring (CPU : hors boucle d'événements).
    texte, pages = await asyncio.to_thread(extraire_texte_pdf, contenu)
    resultat = await asyncio.to_thread(segmenter, texte)
    classe = classer(resultat, caracteres=len(texte), pages=pages)
    langue = decider_langue(texte, depot.titre, depot.langue_indiquee, pages)
    if langue in ("ar", "fr"):
        document_id = cle_document(depot.reference, langue)
    else:
        # Langue indécidable (quarantaine, jamais indexée) : la clé ne
        # passe pas par le contrat Langue, qui ne connaît que ar|fr.
        document_id = f"{depot.reference}|inconnue|originale"

    empreinte_connue = carte.get(document_id)
    if empreinte_connue == empreinte:
        return BilanIngestion(document_id, empreinte, "deja_present")
    if empreinte_connue is not None:
        logger.warning(
            "version differente refusee : %s (empreinte connue %s…, nouvelle %s…)",
            document_id,
            empreinte_connue[:12],
            empreinte[:12],
        )
        return BilanIngestion(document_id, empreinte, "version_differente")

    # Couche brute — définitive.
    cle_stockage = f"{depot.source}/{empreinte}.pdf"
    chemin_brut = await asyncio.to_thread(stockage.deposer, cle_stockage, contenu)

    fiche = base.DocumentNormalise(
        document_id=document_id,
        empreinte_sha256=empreinte,
        source=depot.source,
        url_source=depot.url_source,
        titre=depot.titre,
        langue=langue,
        chemin_brut=chemin_brut,
        pages=pages,
        caracteres=len(texte),
        nb_articles=len(resultat.articles),
        score_numerotation=resultat.score_numerotation,
        classe_qualite=classe,
        en_quarantaine=classe == "C",
        avertissements=[
            {"ligne": a.ligne, "message": a.message} for a in resultat.avertissements
        ],
        date_ingestion=datetime.now(UTC),
    )
    insere = await base.enregistrer_documents([fiche])

    nb_messages = 0
    if classe != "C" and langue in ("ar", "fr"):
        segmentation_dict = {"langue": langue, "articles": resultat.articles}
        document_dict = {
            "reference_promulgation": depot.reference,
            "intitule": depot.titre,
        }
        for lot in construire_lot(document_dict, segmentation_dict):
            enveloppe = envelopper(
                ChargeSegmentsAIndexer(segments=lot),
                correlation_id=f"ingestion-{empreinte[:12]}",
                emis_par="corpus",
            )
            await bus.publier(FILE_SEGMENTS_TO_INDEX, enveloppe)
            nb_messages += 1

    statut = "insere_quarantaine" if classe == "C" else "insere"
    if not insere:
        statut = "deja_present"
    logger.info(
        "ingestion %s : statut=%s classe=%s articles=%d messages=%d",
        document_id,
        statut,
        classe,
        len(resultat.articles),
        nb_messages,
    )
    return BilanIngestion(
        document_id=document_id,
        empreinte_sha256=empreinte,
        statut=statut,
        classe_qualite=classe,
        nb_articles=len(resultat.articles),
        nb_messages_publies=nb_messages,
    )
