"""Accès exclusif à LA base du service conversation.

Principe database-per-service : ce module est le seul point d'entrée vers
la base ; aucun autre service ne connaît ces identifiants. Trois données
propriétaires : sessions, messages (avec leurs métadonnées de traçabilité
RAG) et signalements (portés par le message signalé).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    MetaData,
    String,
    Table,
    Text,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

_metadata = MetaData()
_table_sessions = Table(
    "sessions",
    _metadata,
    Column("id", String, primary_key=True),
    Column("cree_le", DateTime(timezone=True), nullable=False),
)
_table_messages = Table(
    "messages",
    _metadata,
    Column("id", String, primary_key=True),
    Column("session_id", String, ForeignKey("sessions.id"), nullable=False),
    Column("role", String(12), nullable=False),  # user | assistant
    Column("contenu", Text, nullable=False),
    Column("langue", String(2)),
    Column("cree_le", DateTime(timezone=True), nullable=False),
    Column("refus", Boolean, nullable=False, default=False),
    Column("metadonnees", JSON),
    Column("signale", Boolean, nullable=False, default=False),
    Column("motif_signalement", String(1000)),
)

_engine: AsyncEngine | None = None


async def initialiser_base(url: str) -> None:
    """Crée le moteur et les tables du service (schéma autoportant)."""
    global _engine
    _engine = create_async_engine(url, pool_pre_ping=True)
    async with _engine.begin() as conn:
        await conn.run_sync(_metadata.create_all)


async def verifier_base() -> bool:
    """Vrai si la base répond (utilisé par /ready)."""
    if _engine is None:
        return False
    try:
        async with _engine.connect() as conn:
            await conn.run_sync(lambda session: session.scalar(select(1)))
        return True
    except Exception:
        return False


async def fermer_base() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


def _maintenant() -> datetime:
    return datetime.now(UTC)


async def creer_session() -> str:
    """Ouvre une session (accès sans compte en V1) et retourne son identifiant."""
    identifiant = uuid.uuid4().hex
    async with _engine.begin() as conn:
        await conn.execute(
            insert(_table_sessions).values(id=identifiant, cree_le=_maintenant())
        )
    return identifiant


async def session_existe(session_id: str) -> bool:
    async with _engine.connect() as conn:
        ligne = await conn.execute(
            select(_table_sessions.c.id).where(_table_sessions.c.id == session_id)
        )
        return ligne.first() is not None


async def supprimer_session(session_id: str) -> bool:
    """Supprime une session et ses messages (droit à l'effacement) ;
    retourne faux si la session est inconnue."""
    async with _engine.begin() as conn:
        await conn.execute(
            delete(_table_messages).where(
                _table_messages.c.session_id == session_id
            )
        )
        resultat = await conn.execute(
            delete(_table_sessions).where(_table_sessions.c.id == session_id)
        )
        return resultat.rowcount > 0


async def ajouter_message(
    session_id: str,
    role: str,
    contenu: str,
    langue: str | None,
    refus: bool = False,
    metadonnees: dict[str, Any] | None = None,
) -> str:
    """Persiste un message et retourne son identifiant."""
    identifiant = uuid.uuid4().hex
    async with _engine.begin() as conn:
        await conn.execute(
            insert(_table_messages).values(
                id=identifiant,
                session_id=session_id,
                role=role,
                contenu=contenu,
                langue=langue,
                cree_le=_maintenant(),
                refus=refus,
                metadonnees=metadonnees,
            )
        )
    return identifiant


async def historique(session_id: str, limite: int = 100) -> list[dict]:
    """Messages de la session, du plus ancien au plus récent — métadonnées
    comprises (sources citées, motif de refus, marqueurs d'affichage)."""
    async with _engine.connect() as conn:
        lignes = await conn.execute(
            select(
                _table_messages.c.role,
                _table_messages.c.contenu,
                _table_messages.c.langue,
                _table_messages.c.cree_le,
                _table_messages.c.refus,
                _table_messages.c.signale,
                _table_messages.c.metadonnees,
            )
            .where(_table_messages.c.session_id == session_id)
            .order_by(_table_messages.c.cree_le)
            .limit(limite)
        )
        return [
            {
                "role": ligne.role,
                "contenu": ligne.contenu,
                "langue": ligne.langue,
                "cree_le": ligne.cree_le.isoformat(),
                "refus": ligne.refus,
                "signale": ligne.signale,
                "metadonnees": ligne.metadonnees,
            }
            for ligne in lignes
        ]


async def signaler(message_id: str, motif: str) -> bool:
    """Marque un message comme signalé ; retourne faux si message inconnu."""
    async with _engine.begin() as conn:
        resultat = await conn.execute(
            update(_table_messages)
            .where(_table_messages.c.id == message_id)
            .values(signale=True, motif_signalement=motif[:1000])
        )
        return resultat.rowcount > 0
