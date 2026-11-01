"""Enveloppes de messages RabbitMQ — contrats de la communication asynchrone.

Tout message circulant entre les services M3ak est une enveloppe versionnée
portant un identifiant de message, un identifiant de corrélation et
l'émetteur. Les charges utiles sont discriminées par ``type_message``.

Sémantique garantie par l'infrastructure : livraison at-least-once,
consommateurs idempotents, dead-letter exchange sur chaque file de travail.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from .decisions import DecisionAIndexer
from .fiches import FicheAIndexer
from .segments import SegmentArticle

SCHEMA_VERSION = "1.0"

# --- Files de travail (déclarées par l'infrastructure de messagerie) ---

FILE_SEGMENTS_TO_INDEX = "segments.to_index"
FILE_FICHES_TO_INDEX = "fiches.to_index"
FILE_DECISIONS_TO_INDEX = "decisions.to_index"
FILE_OCR_JOBS = "ocr.jobs"


class EnveloppeMessage(BaseModel):
    """Enveloppe commune de tous les messages inter-services."""

    schema_version: str = SCHEMA_VERSION
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str = Field(..., description="Identifiant de corrélation propagé de bout en bout")
    emis_le: datetime = Field(default_factory=lambda: datetime.now(UTC))
    emis_par: str = Field(..., description="Nom du service émetteur")
    type_message: str = Field(..., description="Clé de discrimination de la charge utile")


class ChargeSegmentsAIndexer(BaseModel):
    """Lot de segments publiés par corpus à destination de recherche."""

    type_message: str = "segments_a_indexer"
    segments: list[SegmentArticle] = Field(..., min_length=1)


class MessageSegmentsAIndexer(EnveloppeMessage):
    type_message: str = "segments_a_indexer"
    charge: ChargeSegmentsAIndexer


class ChargeFichesAIndexer(BaseModel):
    """Lot de fiches de procédures publié par corpus à destination de
    recherche — file dédiée, gestion séparée des segments d'articles."""

    type_message: str = "fiches_a_indexer"
    fiches: list[FicheAIndexer] = Field(..., min_length=1)


class MessageFichesAIndexer(EnveloppeMessage):
    type_message: str = "fiches_a_indexer"
    charge: ChargeFichesAIndexer


class ChargeDecisionsAIndexer(BaseModel):
    """Lot de décisions de justice publié par corpus à destination de
    recherche — file dédiée, gestion séparée des segments et des fiches."""

    type_message: str = "decisions_a_indexer"
    decisions: list[DecisionAIndexer] = Field(..., min_length=1)


class MessageDecisionsAIndexer(EnveloppeMessage):
    type_message: str = "decisions_a_indexer"
    charge: ChargeDecisionsAIndexer


class ChargeOcrJob(BaseModel):
    """Demande de traitement OCR d'un document utilisateur (phase 4)."""

    type_message: str = "ocr_job"
    document_utilisateur_id: str
    url_stockage_ephemere: str
    langue_probable: str | None = None


class MessageOcrJob(EnveloppeMessage):
    type_message: str = "ocr_job"
    charge: ChargeOcrJob


def envelopper(charge: BaseModel, correlation_id: str, emis_par: str) -> EnveloppeMessage:
    """Construit l'enveloppe correspondant à une charge utile.

    Chaque type concret d'enveloppe porte la même valeur de ``type_message``
    que sa charge : c'est ce qui permet de reconstruire la bonne classe
    côté consommation.
    """
    type_message = getattr(charge, "type_message", "inconnu")
    classes = {
        "segments_a_indexer": MessageSegmentsAIndexer,
        "fiches_a_indexer": MessageFichesAIndexer,
        "decisions_a_indexer": MessageDecisionsAIndexer,
        "ocr_job": MessageOcrJob,
    }
    classe = classes.get(type_message)
    if classe is None:
        raise ValueError(f"Type de message non supporte : {type_message}")
    return classe(
        correlation_id=correlation_id,
        emis_par=emis_par,
        charge=charge,
    )
