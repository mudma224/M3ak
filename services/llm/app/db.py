"""Accès exclusif à LA base du service llm.

Principe database-per-service : ce module est le seul point d'entrée vers
la base ; aucun autre service ne connaît ces identifiants.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    select,
)
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

_metadata = MetaData()
_table_service_meta = Table(
    "service_meta",
    _metadata,
    Column("cle", String, primary_key=True),
    Column("valeur", String, nullable=False),
)
_table_consommations = Table(
    "consommations",
    _metadata,
    # Une ligne par génération — la traçabilité de la consommation du quota
    # (fournisseur, modèle, jetons) et le matériau du monitoring (phase 5).
    Column("id", String, primary_key=True),
    Column("cree_le", DateTime(timezone=True), nullable=False),
    Column("fournisseur", String(16), nullable=False),
    Column("modele", String, nullable=False),
    Column("jetons_entree", Integer),
    Column("jetons_sortie", Integer),
    Column("latence_ms", Integer, nullable=False),
    Column("attendu", Boolean, nullable=False),
    Column("correlation_id", String),
)

_engine: AsyncEngine | None = None


async def initialiser_base(url: str) -> None:
    """Crée le moteur et les tables du service (idempotent)."""
    global _engine
    _engine = create_async_engine(url, pool_pre_ping=True)
    async with _engine.begin() as conn:
        await conn.run_sync(_metadata.create_all)


async def journaliser_consommation(
    fournisseur: str,
    modele: str,
    jetons_entree: int | None,
    jetons_sortie: int | None,
    latence_ms: int,
    attendu: bool,
    correlation_id: str | None,
) -> None:
    """Trace une génération — best effort : la base est un journal, pas une
    dépendance du chemin de génération."""
    if _engine is None:
        return
    from datetime import UTC, datetime

    from sqlalchemy import insert

    try:
        async with _engine.begin() as conn:
            await conn.execute(
                insert(_table_consommations).values(
                    id=uuid.uuid4().hex,
                    cree_le=datetime.now(UTC),
                    fournisseur=fournisseur,
                    modele=modele,
                    jetons_entree=jetons_entree,
                    jetons_sortie=jetons_sortie,
                    latence_ms=latence_ms,
                    attendu=attendu,
                    correlation_id=correlation_id,
                )
            )
    except Exception:
        pass


async def compter_consommations() -> int:
    """Nombre de générations tracées — indicateur de volumétrie."""
    if _engine is None:
        return 0
    try:
        async with _engine.connect() as conn:
            from sqlalchemy import func

            def _compter(session):
                return session.scalar(
                    select(func.count()).select_from(_table_consommations)
                )

            return int(await conn.run_sync(_compter) or 0)
    except Exception:
        return 0


async def verifier_base() -> bool:
    """Vrai si la base répond (utilisé par /ready).

    La connexion + une requête scalaire passent par run_sync (API publique
    SQLAlchemy) : cela valide à la fois la joignabilité et l'authenticité
    des identifiants du service.
    """
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
