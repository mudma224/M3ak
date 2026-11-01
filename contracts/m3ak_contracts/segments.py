"""Schémas des segments d'articles — l'unité d'indexation du corpus M3ak.

Principe « chercher fin, restituer entier » : la recherche s'effectue sur
les petits segments, la citation restitue toujours l'article complet
(récupéré depuis l'identifiant du document et les numéros d'articles).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .common import Langue


class ContexteHierarchique(BaseModel):
    """Héritage structurel porté par chaque segment.

    Indispensable à la recherche thématique : un étudiant demande « le délai
    de congé du bail commercial », jamais « l'article 664 du DOC ».
    """

    texte_identifiant: str = Field(..., description="Clé canonique de l'identifiant stable du texte")
    texte_titre: str
    livre: str | None = None
    chapitre: str | None = None
    section: str | None = None


class SegmentArticle(BaseModel):
    """Un segment d'article prêt à l'indexation.

    Construit par le service corpus, publié au service recherche via la file
    ``segments.to_index``. L'idempotence côté consommation s'appuie sur
    ``segment_id`` : rejouer un message ne crée jamais de doublon.
    """

    segment_id: str = Field(
        ...,
        description="Identifiant unique et stable du segment (clé d'idempotence de l'indexation)",
    )
    document_id: str = Field(..., description="Clé canonique du document normalisé parent")
    articles: list[str] = Field(
        ...,
        min_length=1,
        description="Numéros d'articles contenus (plusieurs si articles courts regroupés)",
    )
    alineas: str | None = Field(
        default=None,
        description="Plage d'alinéas couverte pour un article long sous-découpé (ex. '1-4')",
    )
    texte: str = Field(..., min_length=1, description="Contenu texte du segment, sans balisage")
    langue: Langue
    contexte: ContexteHierarchique
    variantes_linguistiques: list[str] = Field(
        default_factory=list,
        description="Clés canoniques des variantes AR/FR du même texte (repli interlingue)",
    )
