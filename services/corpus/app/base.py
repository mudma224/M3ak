"""Accès exclusif à LA base du service corpus.

Principe database-per-service : ce module est le seul point d'entrée vers
la base ; aucun autre service ne connaît ces identifiants.

Trois couches (AGENTS.md §5) : la couche brute vit dans le stockage
fichiers (MinIO — voir stockage.py) ; la couche normalisée vit ici
(table ``documents``) ; la couche index vit chez le service recherche,
alimentée par la file ``segments.to_index``. Les manifestes de collecte
sont conservés ici (traçabilité des sessions).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import insert as insert_pg
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

_metadata = MetaData()
_table_service_meta = Table(
    "service_meta",
    _metadata,
    Column("cle", String, primary_key=True),
    Column("valeur", String, nullable=False),
)
_table_documents = Table(
    "documents",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("document_id", String, nullable=False, unique=True),
    Column("empreinte_sha256", String, nullable=False, index=True),
    Column("source", String, nullable=False),
    Column("url_source", Text),
    Column("titre", Text, nullable=False),
    Column("langue", String(8), nullable=False),
    Column("chemin_brut", Text, nullable=False),
    Column("pages", Integer),
    Column("caracteres", Integer),
    Column("nb_articles", Integer),
    Column("score_numerotation", Float),
    Column("classe_qualite", String(1), nullable=False, index=True),
    Column("en_quarantaine", Boolean, nullable=False, default=False),
    Column("avertissements", JSON),
    Column("ingere_le", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
_table_manifestes = Table(
    "manifestes",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("source", String, nullable=False),
    Column("methode", Text, nullable=False),
    Column("agent_utilisateur", Text),
    Column("date_debut", DateTime(timezone=True), nullable=False),
    Column("date_fin", DateTime(timezone=True), nullable=False),
    Column("volume", JSON, nullable=False),
    Column("entrees", JSON),
)

_engine: AsyncEngine | None = None


class DocumentNormalise(BaseModel):
    """Un document de la couche normalisée, prêt à être enregistré."""

    document_id: str
    empreinte_sha256: str
    source: str
    url_source: str | None = None
    titre: str
    langue: str
    chemin_brut: str
    pages: int
    caracteres: int
    nb_articles: int
    score_numerotation: float
    classe_qualite: str = Field(pattern=r"^[ABC]$")
    en_quarantaine: bool = False
    avertissements: list[dict[str, Any]] = Field(default_factory=list)
    date_ingestion: datetime


class ManifesteCollecte(BaseModel):
    """Trace d'une session de collecte (append-only)."""

    source: str
    methode: str
    agent_utilisateur: str | None = None
    date_debut: datetime
    date_fin: datetime
    volume: dict[str, Any]
    entrees: list[dict[str, Any]] = Field(default_factory=list)


async def initialiser_base(url: str) -> None:
    """Crée le moteur et les tables du service (idempotent)."""
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


async def lire_document(document_id: str) -> dict[str, Any] | None:
    """La fiche d'un document par identifiant stable — empreinte, chemin
    brut, source, quarantaine et titre (affiché par la checklist de
    démarche). None si le document_id est inconnu. Requête paramétrée
    (valeur liée, jamais interpolée)."""
    if _engine is None:
        raise RuntimeError("base du service corpus non initialisee")

    statement = text(
        "SELECT empreinte_sha256, chemin_brut, source, en_quarantaine, titre "
        "FROM documents WHERE document_id = :document_id"
    )
    async with _engine.connect() as conn:
        ligne = (
            await conn.execute(statement, {"document_id": document_id})
        ).first()
    if ligne is None:
        return None
    return {
        "empreinte_sha256": ligne.empreinte_sha256,
        "chemin_brut": ligne.chemin_brut,
        "source": ligne.source,
        "en_quarantaine": ligne.en_quarantaine,
        "titre": ligne.titre,
    }


async def enregistrer_documents(fiches: list[DocumentNormalise]) -> int:
    """Insère des fiches de documents normalisés ; idempotent (clé =
    document_id). Retourne le nombre de fiches réellement insérées."""
    if _engine is None:
        raise RuntimeError("base du service corpus non initialisee")

    lignes = [
        {
            "document_id": fiche.document_id,
            "empreinte_sha256": fiche.empreinte_sha256,
            "source": fiche.source,
            "url_source": fiche.url_source,
            "titre": fiche.titre,
            "langue": fiche.langue,
            "chemin_brut": fiche.chemin_brut,
            "pages": fiche.pages,
            "caracteres": fiche.caracteres,
            "nb_articles": fiche.nb_articles,
            "score_numerotation": fiche.score_numerotation,
            "classe_qualite": fiche.classe_qualite,
            "en_quarantaine": fiche.en_quarantaine,
            "avertissements": fiche.avertissements,
            "ingere_le": fiche.date_ingestion,
        }
        for fiche in fiches
    ]

    def _executer(session) -> list[int]:
        statement = (
            insert_pg(_table_documents)
            .values(lignes)
            .on_conflict_do_nothing(index_elements=["document_id"])
            .returning(_table_documents.c.id)
        )
        return list(session.execute(statement).scalars())

    async with _engine.begin() as conn:
        return len(await conn.run_sync(_executer))


async def enregistrer_manifestes(traces: list[ManifesteCollecte]) -> None:
    """Conserve les traces de sessions de collecte (append-only)."""
    if _engine is None:
        raise RuntimeError("base du service corpus non initialisee")

    lignes = [
        {
            "source": trace.source,
            "methode": trace.methode,
            "agent_utilisateur": trace.agent_utilisateur,
            "date_debut": trace.date_debut,
            "date_fin": trace.date_fin,
            "volume": trace.volume,
            "entrees": trace.entrees,
        }
        for trace in traces
    ]

    def _executer(session) -> list[int]:
        statement = (
            insert_pg(_table_manifestes)
            .values(lignes)
            .returning(_table_manifestes.c.id)
        )
        return list(session.execute(statement).scalars())

    async with _engine.begin() as conn:
        await conn.run_sync(_executer)


async def carte_documents() -> dict[str, str]:
    """Carte document_id → empreinte : statut d'ingestion et
    déduplication inter-sources (volumétrie phase 1 : lecture complète
    des deux colonnes, agrégation en mémoire)."""
    if _engine is None:
        raise RuntimeError("base du service corpus non initialisee")

    def _lire(session) -> dict[str, str]:
        lignes = session.execute(
            select(_table_documents.c.document_id, _table_documents.c.empreinte_sha256)
        )
        return {document_id: empreinte for document_id, empreinte in lignes}

    async with _engine.connect() as conn:
        return await conn.run_sync(_lire)


async def stats_documents() -> dict[str, Any]:
    """Volumétrie et qualité — base du tableau de bord de la phase 1."""
    if _engine is None:
        raise RuntimeError("base du service corpus non initialisee")

    def _calculer(session) -> dict[str, Any]:
        # Matérialiser les lignes : un Result SQL ne se consomme qu'une fois.
        lignes = list(
            session.execute(
                select(
                    _table_documents.c.classe_qualite,
                    _table_documents.c.en_quarantaine,
                    _table_documents.c.nb_articles,
                )
            )
        )
        classes = [str(classe) for classe, _, _ in lignes]
        articles = sum(nb or 0 for _, _, nb in lignes)
        return {
            "total_documents": len(classes),
            "par_classe": {c: classes.count(c) for c in ("A", "B", "C")},
            "en_quarantaine": sum(1 for _, quarantaine, _ in lignes if quarantaine),
            "total_articles": articles,
        }

    async with _engine.connect() as conn:
        return await conn.run_sync(_calculer)


async def documents_pour_classement() -> list[dict[str, str]]:
    """Documents PDF (empreinte, langue, classe, chemin brut) pour la vue
    de classement — revue manuelle de la grammaire de segmentation ; les
    documents non PDF (fiches de procédures JSON…) n'y participent pas."""
    if _engine is None:
        raise RuntimeError("base du service corpus non initialisee")

    def _lire(session) -> list[dict[str, str]]:
        lignes = session.execute(
            select(
                _table_documents.c.empreinte_sha256,
                _table_documents.c.langue,
                _table_documents.c.classe_qualite,
                _table_documents.c.chemin_brut,
            ).where(_table_documents.c.chemin_brut.like("%.pdf"))
        )
        return [
            {
                "empreinte": empreinte,
                "langue": langue,
                "classe": classe,
                "chemin_brut": chemin_brut,
            }
            for empreinte, langue, classe, chemin_brut in lignes
        ]

    async with _engine.connect() as conn:
        return await conn.run_sync(_lire)


async def repartition_documents() -> dict[str, Any]:
    """Répartition du corpus par source, langue et classe de qualité —
    l'agrégation du tableau de bord de la phase 1."""
    if _engine is None:
        raise RuntimeError("base du service corpus non initialisee")

    def _lire(session) -> list[dict[str, Any]]:
        colonnes = (
            _table_documents.c.source,
            _table_documents.c.langue,
            _table_documents.c.classe_qualite,
            _table_documents.c.en_quarantaine,
        )
        lignes = list(
            session.execute(
                select(
                    *colonnes,
                    func.count(),
                    func.coalesce(func.sum(_table_documents.c.nb_articles), 0),
                ).group_by(*colonnes)
            )
        )
        return [
            {
                "source": source,
                "langue": langue,
                "classe": classe,
                "en_quarantaine": quarantaine,
                "documents": nombre,
                "articles": articles,
            }
            for source, langue, classe, quarantaine, nombre, articles in lignes
        ]

    async with _engine.connect() as conn:
        return await conn.run_sync(_lire)


async def dernieres_collectes(limite: int = 5) -> list[dict[str, Any]]:
    """Les dernières sessions de collecte (traçabilité des manifestes)."""
    if _engine is None:
        raise RuntimeError("base du service corpus non initialisee")

    def _lire(session) -> list[dict[str, Any]]:
        lignes = session.execute(
            select(
                _table_manifestes.c.id,
                _table_manifestes.c.source,
                _table_manifestes.c.methode,
                _table_manifestes.c.date_debut,
                _table_manifestes.c.volume,
            )
            .order_by(_table_manifestes.c.id.desc())
            .limit(limite)
        )
        return [
            {
                "id": identifiant,
                "source": source,
                "methode": methode,
                "date_debut": debut.isoformat(timespec="seconds") if debut else None,
                "volume": volume,
            }
            for identifiant, source, methode, debut, volume in lignes
        ]

    async with _engine.connect() as conn:
        return await conn.run_sync(_lire)


async def references_par_source(source: str) -> dict[str, datetime | None]:
    """Référence source (première composante de l'identifiant stable) →
    date d'ingestion la plus récente — la base du diff de la veille."""
    if _engine is None:
        raise RuntimeError("base du service corpus non initialisee")
    reference = func.split_part(_table_documents.c.document_id, "|", 1)

    def _lire(session) -> dict[str, datetime | None]:
        lignes = session.execute(
            select(reference, func.max(_table_documents.c.ingere_le))
            .where(_table_documents.c.source == source)
            .group_by(reference)
        )
        return {ref: ingestion for ref, ingestion in lignes}

    async with _engine.connect() as conn:
        return await conn.run_sync(_lire)


async def fermer_base() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
