"""Mises à jour de l'index par le backfill (phase 2).

Séparé de db.py pour la lisibilité : tout ce qui modifie l'index après
coup — remplissage du tsv des unités indexées avant l'étape 2, écriture
des vecteurs. Les requêtes sont des constantes littérales, paramétrées
(%s psycopg) : aucune valeur n'est jamais interpolée dans le SQL.
"""

from __future__ import annotations

from .db import moteur

# Une requête littérale par famille (table et colonne d'identifiant sont
# des constantes du schéma). Le vecteur est passé en chaîne "[a,b,…]"
# avec un cast ::vector — psycopg transmet le texte, PostgreSQL le parse.
_REQUETES_EMBEDDING = {
    "segment": "UPDATE segments SET embedding = %s::vector WHERE segment_id = %s",
    "fiche": "UPDATE fiches SET embedding = %s::vector WHERE fiche_id = %s",
    "passage": "UPDATE passages SET embedding = %s::vector WHERE passage_id = %s",
}

# Remplissage du tsv des unités indexées avant l'étape 2 : normalisation
# arabe calculée par PostgreSQL — même règles que texte.normaliser()
# (marques supprimées par regexp_replace, remappage par translate).
_MARQUES = "[\u0640\u064B-\u065F\u0670\u06D6-\u06ED]"
_FORMES = "\u0623\u0625\u0622\u0671\u0649\u0624\u0626\u0629"
_UNIFIEES = "\u0627\u0627\u0627\u0627\u064A\u0648\u064A\u0647"

_REQUETES_TSV = {
    "segment": (
        "UPDATE segments SET tsv = to_tsvector('french', "
        "translate(regexp_replace(texte, %s, '', 'g'), %s, %s)) "
        "WHERE tsv IS NULL"
    ),
    "fiche": (
        "UPDATE fiches SET tsv = to_tsvector('french', "
        "translate(regexp_replace(texte, %s, '', 'g'), %s, %s)) "
        "WHERE tsv IS NULL"
    ),
    # Les passages intègrent les thématiques de leur décision (slugs JSON
    # déquotés) — mêmes règles de normalisation que côté insertion.
    "passage": (
        "UPDATE passages SET tsv = to_tsvector('french', "
        "translate(regexp_replace(texte || ' ' || "
        "replace(coalesce(contexte->>'thematiques', ''), '\"', ' '), %s, '', 'g'), %s, %s)) "
        "WHERE tsv IS NULL"
    ),
}


def _vecteur_en_chaine(vecteur: list[float]) -> str:
    """Chaîne littérale vector — précision fp32 (7 chiffres significatifs)."""
    return "[" + ",".join(f"{v:.7g}" for v in vecteur) + "]"


async def remplir_tsv(famille: str) -> int:
    """Remplit le tsv des unités qui n'en ont pas (idempotent).

    Les unités fraîchement indexées reçoivent leur tsv à l'upsert ; celles
    de la phase 1 le reçoivent ici, en une requête fixe par table.
    """
    async with moteur().begin() as conn:
        result = await conn.exec_driver_sql(
            _REQUETES_TSV[famille], (_MARQUES, _FORMES, _UNIFIEES)
        )
        return result.rowcount


async def appliquer_embeddings(famille: str, lignes: list[dict]) -> None:
    """Écrit les vecteurs sur les unités du lot (executemany).

    Chaque ligne porte unite_id et embedding (vecteur).
    """
    if not lignes:
        return
    parametres = [
        (_vecteur_en_chaine(ligne["embedding"]), ligne["unite_id"]) for ligne in lignes
    ]

    async with moteur().begin() as conn:
        await conn.exec_driver_sql(_REQUETES_EMBEDDING[famille], parametres)
