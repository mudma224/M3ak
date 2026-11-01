"""Accès exclusif à LA base du service documents.

Principe database-per-service : ce module est le seul point d'entrée vers
la base ; aucun autre service ne connaît ces identifiants.
"""

from __future__ import annotations

from sqlalchemy import Column, MetaData, String, Table, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

_metadata = MetaData()
_table_service_meta = Table(
    "service_meta",
    _metadata,
    Column("cle", String, primary_key=True),
    Column("valeur", String, nullable=False),
)

_engine: AsyncEngine | None = None


async def initialiser_base(url: str) -> None:
    """Crée le moteur et la table méta minimale du service."""
    global _engine
    _engine = create_async_engine(url, pool_pre_ping=True)
    async with _engine.begin() as conn:
        await conn.run_sync(_metadata.create_all)


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
