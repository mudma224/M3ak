"""Accès exclusif à LA base du service recherche.

Principe database-per-service : ce module est le seul point d'entrée vers
la base ; aucun autre service ne connaît ces identifiants.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    desc,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.dialects.postgresql import insert as insert_pg
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from .texte import normaliser

if TYPE_CHECKING:
    from m3ak_contracts import DecisionAIndexer, FicheAIndexer, SegmentArticle

# Dimensions des embeddings (BGE-M3). Changer de modèle exige une migration
# de ces colonnes et un backfill complet.
DIMENSIONS_EMBEDDING = 1024

_metadata = MetaData()
_table_service_meta = Table(
    "service_meta",
    _metadata,
    Column("cle", String, primary_key=True),
    Column("valeur", String, nullable=False),
)
_table_segments = Table(
    "segments",
    _metadata,
    Column("segment_id", String, primary_key=True),
    Column("document_id", String, nullable=False, index=True),
    Column("articles", JSON, nullable=False),
    Column("langue", String(8), nullable=False),
    Column("contexte", JSON, nullable=False),
    Column("texte", Text, nullable=False),
    Column("tsv", TSVECTOR),
    Column("embedding", Vector(DIMENSIONS_EMBEDDING)),
    Column("insere_le", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
_table_fiches = Table(
    "fiches",
    _metadata,
    Column("fiche_id", String, primary_key=True),
    Column("document_id", String, nullable=False, index=True),
    Column("titre", Text, nullable=False),
    Column("langue", String(8), nullable=False),
    Column("thematique_id", String),
    Column("administration", Text),
    Column("texte", Text, nullable=False),
    Column("tsv", TSVECTOR),
    Column("embedding", Vector(DIMENSIONS_EMBEDDING)),
    Column("insere_le", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
_table_decisions = Table(
    "decisions",
    _metadata,
    Column("decision_id", String, primary_key=True),
    Column("document_id", String, nullable=False, index=True),
    Column("titre", Text, nullable=False),
    Column("langue", String(8), nullable=False),
    Column("thematiques", JSON),
    Column("juridiction", Text),
    Column("ville", Text),
    Column("type_decision", Text),
    Column("numero_decision", Text),
    Column("numero_dossier", Text),
    Column("date_decision", String(10)),
    Column("resume_francais", Text),
    Column("resume_arabe", Text),
    Column("texte_integral_arabe", Text),
    Column("texte_integral_francais", Text),
    Column("insere_le", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
_table_passages = Table(
    "passages",
    _metadata,
    Column("passage_id", String, primary_key=True),
    Column("decision_id", String, nullable=False, index=True),
    Column("document_id", String, nullable=False, index=True),
    Column("titre", Text, nullable=False),
    Column("langue", String(8), nullable=False),
    Column("position", Integer, nullable=False),
    Column("type_contenu", String(16), nullable=False),
    Column("texte", Text, nullable=False),
    Column("contexte", JSON, nullable=False),
    Column("tsv", TSVECTOR),
    Column("embedding", Vector(DIMENSIONS_EMBEDDING)),
    Column("insere_le", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
_table_passages_absents = Table(
    "passages_absents",
    _metadata,
    # Décisions sans aucun contenu textuel : marqueur pour que le backfill
    # ne les reconsidère pas à chaque exécution.
    Column("decision_id", String, primary_key=True),
    Column("note_le", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# Une famille d'unité d'indexation = une table = une source du corpus.
_FAMILLES = {
    "segment": (_table_segments, "segment_id"),
    "fiche": (_table_fiches, "fiche_id"),
    "passage": (_table_passages, "passage_id"),
}

_engine: AsyncEngine | None = None


def _migrer(conn) -> None:
    """Crée les tables absentes (passages, passages_absents) — idempotent.

    Exécutée via run_sync : `conn` est une connexion synchrone (celle
    fournie par SQLAlchemy à la fonction déléguée), create_all y est donc
    appelé directement. Les migrations ponctuelles du schéma (colonnes
    tsv/embedding des tables existantes) vivent dans `app/migrations/*.sql` :
    elles sont appliquées une fois via psql (runbook §20), jamais assemblées
    dans le code.
    """
    _metadata.create_all(conn)


async def initialiser_base(url: str) -> None:
    """Crée le moteur, applique les migrations et les tables du service."""
    global _engine
    _engine = create_async_engine(url, pool_pre_ping=True)
    async with _engine.begin() as conn:
        await conn.run_sync(_migrer)


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


async def upserter_segments(segments: list[SegmentArticle]) -> tuple[list[str], int]:
    """Insère les segments de façon idempotente (clé = segment_id).

    Livraison at-least-once : rejouer un message ne crée jamais de doublon —
    un segment déjà présent est ignoré. Retourne (identifiants des segments
    réellement insérés, déjà présents) — la liste sert à la vectorisation
    immédiate des seules unités nouvelles. Le contenu d'un segment n'est
    jamais modifié en place : une nouvelle version d'un texte produira un
    autre document_id (autre version de l'identifiant stable), donc
    d'autres segment_id.
    """
    if _engine is None:
        raise RuntimeError("base du service recherche non initialisee")
    if not segments:
        return [], 0

    lignes_segments = [
        {
            "segment_id": segment.segment_id,
            "document_id": segment.document_id,
            "articles": list(segment.articles),
            "langue": segment.langue.value,
            "contexte": segment.contexte.model_dump(),
            "texte": segment.texte,
            "tsv": func.to_tsvector("french", normaliser(segment.texte)),
        }
        for segment in segments
    ]

    def _executer(session) -> list[str]:
        statement = (
            insert_pg(_table_segments)
            .values(lignes_segments)
            .on_conflict_do_nothing(index_elements=["segment_id"])
            .returning(_table_segments.c.segment_id)
        )
        return list(session.execute(statement).scalars())

    async with _engine.begin() as conn:
        inseres = await conn.run_sync(_executer)
    return inseres, len(segments) - len(inseres)


async def compter_segments() -> tuple[int, int]:
    """Nombre de segments indexés et de documents distincts."""
    if _engine is None:
        raise RuntimeError("base du service recherche non initialisee")

    def _compter(session) -> tuple[int, int]:
        total = session.scalar(select(func.count()).select_from(_table_segments)) or 0
        distincts = select(_table_segments.c.document_id).distinct().subquery()
        documents = session.scalar(select(func.count()).select_from(distincts)) or 0
        return int(total), int(documents)

    async with _engine.connect() as conn:
        return await conn.run_sync(_compter)


async def upserter_fiches(fiches: list[FicheAIndexer]) -> tuple[list[str], int]:
    """Insère les fiches de procédures de façon idempotente (clé = fiche_id).

    Livraison at-least-once : rejouer un message ne crée jamais de doublon.
    Gestion séparée des segments d'articles : les fiches vivent dans leur
    propre table et leur propre file, chaque famille évolue sans toucher
    l'autre. Retourne (identifiants des fiches réellement insérées, déjà
    présentes) — la liste sert à la vectorisation immédiate.
    """
    if _engine is None:
        raise RuntimeError("base du service recherche non initialisee")
    if not fiches:
        return [], 0

    lignes_fiches = [
        {
            "fiche_id": fiche.fiche_id,
            "document_id": fiche.document_id,
            "titre": fiche.titre,
            "langue": fiche.langue.value,
            "thematique_id": fiche.thematique_id,
            "administration": fiche.administration,
            "texte": fiche.texte,
            "tsv": func.to_tsvector("french", normaliser(fiche.texte)),
        }
        for fiche in fiches
    ]

    def _executer(session) -> list[str]:
        statement = (
            insert_pg(_table_fiches)
            .values(lignes_fiches)
            .on_conflict_do_nothing(index_elements=["fiche_id"])
            .returning(_table_fiches.c.fiche_id)
        )
        return list(session.execute(statement).scalars())

    async with _engine.begin() as conn:
        inseres = await conn.run_sync(_executer)
    return inseres, len(fiches) - len(inseres)


async def compter_fiches() -> tuple[int, int]:
    """Nombre de fiches indexées et de documents distincts."""
    if _engine is None:
        raise RuntimeError("base du service recherche non initialisee")

    def _compter(session) -> tuple[int, int]:
        total = session.scalar(select(func.count()).select_from(_table_fiches)) or 0
        distincts = select(_table_fiches.c.document_id).distinct().subquery()
        documents = session.scalar(select(func.count()).select_from(distincts)) or 0
        return int(total), int(documents)

    async with _engine.connect() as conn:
        return await conn.run_sync(_compter)


async def upserter_decisions(decisions: list[DecisionAIndexer]) -> tuple[int, int]:
    """Insère les décisions de justice de façon idempotente
    (clé = decision_id).

    Livraison at-least-once : rejouer un message ne crée jamais de doublon.
    Gestion séparée des segments et des fiches : chaque famille évolue sans
    toucher à l'autre. Retourne (insérés, déjà présents).
    """
    if _engine is None:
        raise RuntimeError("base du service recherche non initialisee")
    if not decisions:
        return 0, 0

    lignes_decisions = [
        {
            "decision_id": decision.decision_id,
            "document_id": decision.document_id,
            "titre": decision.titre,
            "langue": decision.langue.value,
            "thematiques": list(decision.thematiques),
            "juridiction": decision.juridiction,
            "ville": decision.ville,
            "type_decision": decision.type_decision,
            "numero_decision": decision.numero_decision,
            "numero_dossier": decision.numero_dossier,
            "date_decision": decision.date_decision,
            "resume_francais": decision.resume_francais,
            "resume_arabe": decision.resume_arabe,
            "texte_integral_arabe": decision.texte_integral_arabe,
            "texte_integral_francais": decision.texte_integral_francais,
        }
        for decision in decisions
    ]

    def _executer(session) -> list[str]:
        statement = (
            insert_pg(_table_decisions)
            .values(lignes_decisions)
            .on_conflict_do_nothing(index_elements=["decision_id"])
            .returning(_table_decisions.c.decision_id)
        )
        return list(session.execute(statement).scalars())

    async with _engine.begin() as conn:
        inseres = await conn.run_sync(_executer)
    return len(inseres), len(decisions) - len(inseres)


async def compter_decisions() -> tuple[int, int]:
    """Nombre de décisions indexées et de documents distincts."""
    if _engine is None:
        raise RuntimeError("base du service recherche non initialisee")

    def _compter(session) -> tuple[int, int]:
        total = session.scalar(select(func.count()).select_from(_table_decisions)) or 0
        distincts = select(_table_decisions.c.document_id).distinct().subquery()
        documents = session.scalar(select(func.count()).select_from(distincts)) or 0
        return int(total), int(documents)

    async with _engine.connect() as conn:
        return await conn.run_sync(_compter)


async def inserer_passages(passages: list) -> int:
    """Insère les passages d'une décision (idempotent, clé = passage_id).

    Le tsv est rempli ici ; l'embedding est laissé à NULL — calculé par la
    même chaîne que le backfill (compléter_embeddings), que TEI soit
    disponible ou non au moment de l'indexation.
    """
    if _engine is None:
        raise RuntimeError("base du service recherche non initialisee")
    if not passages:
        return 0
    lignes = []
    for passage in passages:
        thematiques = " ".join((passage.contexte or {}).get("thematiques") or [])
        lignes.append(
            {
                "passage_id": passage.passage_id,
                "decision_id": passage.decision_id,
                "document_id": passage.document_id,
                "titre": passage.titre,
                "langue": passage.langue,
                "position": passage.position,
                "type_contenu": passage.type_contenu,
                "texte": passage.texte,
                "contexte": passage.contexte,
                # Les thématiques de la décision entrent dans le plein texte :
                # les slugs (procedure-civile…) portent des mots-vedettes utiles.
                "tsv": func.to_tsvector(
                    "french", normaliser(f"{passage.texte} {thematiques}")
                ),
            }
        )

    def _executer(session) -> list[str]:
        statement = (
            insert_pg(_table_passages)
            .values(lignes)
            .on_conflict_do_nothing(index_elements=["passage_id"])
            .returning(_table_passages.c.passage_id)
        )
        return list(session.execute(statement).scalars())

    async with _engine.begin() as conn:
        inseres = await conn.run_sync(_executer)
    return len(inseres)


async def compter_passages() -> int:
    if _engine is None:
        raise RuntimeError("base du service recherche non initialisee")

    def _compter(session) -> int:
        return int(session.scalar(select(func.count()).select_from(_table_passages)) or 0)

    async with _engine.connect() as conn:
        return await conn.run_sync(_compter)


# ───────────────────────── Recherche hybride (phase 2) ─────────────────────────


def _normaliser_resultat(famille: str, mapping) -> dict:
    """Restitution commune des trois familles : le titre d'un segment vit
    dans son contexte (texte_titre), les fiches portent leur thématique et
    leur administration, les passages leur contexte de décision."""
    if famille == "segment":
        contexte = dict(mapping["contexte"] or {})
        return {
            "unite_id": mapping["unite_id"],
            "document_id": mapping["document_id"],
            "titre": str(contexte.get("texte_titre") or ""),
            "texte": mapping["texte"],
            "langue": mapping["langue"],
            "contexte": contexte,
        }
    if famille == "fiche":
        return {
            "unite_id": mapping["unite_id"],
            "document_id": mapping["document_id"],
            "titre": mapping["titre"],
            "texte": mapping["texte"],
            "langue": mapping["langue"],
            "contexte": {
                "thematique_id": mapping["thematique_id"],
                "administration": mapping["administration"],
            },
        }
    return {
        "unite_id": mapping["unite_id"],
        "document_id": mapping["document_id"],
        "titre": mapping["titre"],
        "texte": mapping["texte"],
        "langue": mapping["langue"],
        "contexte": dict(mapping["contexte"] or {}),
    }


def _selection_restitution(table, cle: str, famille: str):
    base = select(
        table.c[cle].label("unite_id"),
        table.c.document_id,
        table.c.texte,
        table.c.langue,
    )
    if famille == "segment":
        return base.add_columns(table.c.contexte)
    if famille == "fiche":
        return base.add_columns(table.c.titre, table.c.thematique_id, table.c.administration)
    return base.add_columns(table.c.titre, table.c.contexte)


async def chercher_plein_texte(
    famille: str, question_normalisee: str, limite: int
) -> list[dict]:
    """Unités les plus proches en plein texte (ts_rank), toutes langues.

    La question est déjà normalisée (arabe unifié) : plainto_tsquery('french', …)
    stemmatise le français et laisse les tokens arabes intacts.
    """
    if _engine is None:
        raise RuntimeError("base du service recherche non initialisee")
    table, cle = _FAMILLES[famille]
    requete = func.plainto_tsquery("french", question_normalisee)
    rang = func.ts_rank(table.c.tsv, requete).label("rang")

    def _executer(session) -> list[dict]:
        statement = (
            _selection_restitution(table, cle, famille)
            .add_columns(rang)
            .where(table.c.tsv.op("@@")(requete))
            .order_by(desc("rang"))
            .limit(limite)
        )
        return [_normaliser_resultat(famille, ligne._mapping) for ligne in session.execute(statement)]

    async with _engine.connect() as conn:
        return await conn.run_sync(_executer)


async def chercher_vectoriel(famille: str, vecteur: list[float], limite: int) -> list[dict]:
    """Unités les plus proches du vecteur de la question (distance cosinus).

    Les unités sans embedding (backfill en cours) sont exclues.
    """
    if _engine is None:
        raise RuntimeError("base du service recherche non initialisee")
    table, cle = _FAMILLES[famille]

    def _executer(session) -> list[dict]:
        statement = (
            _selection_restitution(table, cle, famille)
            .where(table.c.embedding != None)
            .order_by(table.c.embedding.cosine_distance(vecteur))
            .limit(limite)
        )
        # Recherche approximative HNSW : sans ce réglage, pgvector explore
        # ef_search=40 candidats par défaut — la requête LIMIT 100 ne
        # rendait que ~40 résultats tronqués et les vrais plus proches
        # voisins pouvaient manquer (constaté en étape 6 : un attendu au
        # rang 1 exact absent du résultat du service). Deux fois la limite
        # est la recommandation usuelle.
        session.execute(text("SET hnsw.ef_search = 200"))
        return [_normaliser_resultat(famille, ligne._mapping) for ligne in session.execute(statement)]

    async with _engine.connect() as conn:
        return await conn.run_sync(_executer)


async def lire_textes(famille: str, unite_ids: list[str]) -> list[dict]:
    """Textes des unités données (id, texte) — vectorisation ciblée des
    unités fraîchement insérées par les consommateurs."""
    if _engine is None:
        raise RuntimeError("base du service recherche non initialisee")
    if not unite_ids:
        return []
    table, cle = _FAMILLES[famille]

    def _executer(session) -> list[dict]:
        statement = select(table.c[cle].label("unite_id"), table.c.texte).where(
            table.c[cle].in_(unite_ids)
        )
        return [dict(ligne._mapping) for ligne in session.execute(statement)]

    async with _engine.connect() as conn:
        return await conn.run_sync(_executer)


# ─────────────────────── Backfill des embeddings (phase 2) ───────────────────────
# Le backfill complète, par lots, les unités indexées avant que le serveur
# d'embeddings ne soit disponible. Idempotent : seules les unités sans
# embedding sont traitées ; re-lancer ne recompte rien.


async def compter_sans_embedding() -> dict[str, int]:
    if _engine is None:
        raise RuntimeError("base du service recherche non initialisee")

    def _executer(session) -> dict[str, int]:
        return {
            famille: int(
                session.scalar(
                    select(func.count())
                    .select_from(table)
                    .where(table.c.embedding == None)
                )
                or 0
            )
            for famille, (table, _) in _FAMILLES.items()
        }

    async with _engine.connect() as conn:
        return await conn.run_sync(_executer)


async def lot_sans_embedding(famille: str, taille: int) -> list[dict]:
    """Un lot d'unités (id, texte) sans embedding, pour la vectorisation."""
    if _engine is None:
        raise RuntimeError("base du service recherche non initialisee")
    table, cle = _FAMILLES[famille]

    def _executer(session) -> list[dict]:
        statement = (
            select(table.c[cle].label("unite_id"), table.c.texte)
            .where(table.c.embedding == None)
            .limit(taille)
        )
        return [dict(ligne._mapping) for ligne in session.execute(statement)]

    async with _engine.connect() as conn:
        return await conn.run_sync(_executer)


def moteur() -> AsyncEngine:
    """Le moteur de connexion (pour les mises à jour du module maintenance)."""
    if _engine is None:
        raise RuntimeError("base du service recherche non initialisee")
    return _engine


def table_et_cle(famille: str) -> tuple[Table, str]:
    """Table et colonne d'identifiant d'une famille d'unité d'indexation."""
    return _FAMILLES[famille]


async def decisions_sans_passages(limite: int) -> list[dict]:
    """Décisions dont les passages n'ont pas encore été générés.

    Les décisions sans aucun contenu textuel sont marquées dans
    passages_absents : elles ne reviennent jamais dans cette requête.
    """
    if _engine is None:
        raise RuntimeError("base du service recherche non initialisee")
    def _executer(session) -> list[dict]:
        # Le volume est minuscule (4 397 décisions) : lecture des
        # identifiants déjà traités, puis filtrage en Python — plus
        # lisible qu'une sous-requête anti-join.
        traites = set(session.scalars(select(_table_passages.c.decision_id))) | set(
            session.scalars(select(_table_passages_absents.c.decision_id))
        )
        lignes = session.execute(
            select(
                _table_decisions.c.decision_id,
                _table_decisions.c.document_id,
                _table_decisions.c.titre,
                _table_decisions.c.langue,
                _table_decisions.c.thematiques,
                _table_decisions.c.juridiction,
                _table_decisions.c.ville,
                _table_decisions.c.type_decision,
                _table_decisions.c.numero_decision,
                _table_decisions.c.numero_dossier,
                _table_decisions.c.date_decision,
                _table_decisions.c.resume_francais,
                _table_decisions.c.resume_arabe,
                _table_decisions.c.texte_integral_arabe,
                _table_decisions.c.texte_integral_francais,
            ).order_by(_table_decisions.c.decision_id)
        ).all()
        return [
            dict(ligne._mapping)
            for ligne in lignes
            if ligne.decision_id not in traites
        ][:limite]

    async with _engine.connect() as conn:
        return await conn.run_sync(_executer)


async def marquer_sans_passages(decision_id: str) -> None:
    if _engine is None:
        raise RuntimeError("base du service recherche non initialisee")

    def _executer(session) -> None:
        statement = (
            insert_pg(_table_passages_absents)
            .values([{"decision_id": decision_id}])
            .on_conflict_do_nothing(index_elements=["decision_id"])
        )
        session.execute(statement)

    async with _engine.begin() as conn:
        await conn.run_sync(_executer)


def _index_hnsw(table: Table, colonne: str, nom: str) -> Index:
    """Index HNSW (distance cosinus) construit par l'API typée de
    SQLAlchemy — aucun SQL de construction d'index dans le code."""
    return Index(
        nom,
        table.c[colonne],
        postgresql_using="hnsw",
        postgresql_ops={colonne: "vector_cosine_ops"},
        postgresql_with={"m": 16, "ef_construction": 64},
    )


_INDEXES_HNSW = (
    _index_hnsw(_table_segments, "embedding", "ix_segments_hnsw"),
    _index_hnsw(_table_fiches, "embedding", "ix_fiches_hnsw"),
    _index_hnsw(_table_passages, "embedding", "ix_passages_hnsw"),
)


async def supprimer_index_hnsw() -> None:
    """Supprime les index HNSW avant un (re)backfill : l'entretien d'un index
    pendant l'insertion de milliers de vecteurs ralentirait le remplissage."""
    if _engine is None:
        raise RuntimeError("base du service recherche non initialisee")
    async with _engine.begin() as conn:
        for index in _INDEXES_HNSW:
            await conn.run_sync(partial(_executer_drop_index, index))


async def creer_index_hnsw() -> None:
    """(Re)crée les index HNSW (distance cosinus) — appelé à la fin d'un
    backfill réussi. Idempotent."""
    if _engine is None:
        raise RuntimeError("base du service recherche non initialisee")
    async with _engine.begin() as conn:
        for index in _INDEXES_HNSW:
            await conn.run_sync(partial(_executer_creation_index, index))


def _executer_drop_index(index: Index, conn) -> None:
    index.drop(conn, checkfirst=True)


def _executer_creation_index(index: Index, conn) -> None:
    index.create(conn, checkfirst=True)


async def fermer_base() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
