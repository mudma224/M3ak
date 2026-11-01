"""Schémas des fiches de procédures — l'unité d'indexation des procédures.

Les fiches (Idarati) sont des documents structurés sans articles : elles
circulent sur leur propre file (``fiches.to_index``) et sont indexées à
part des segments d'articles, ce qui laisse chaque famille évoluer sans
toucher à l'autre. Le contenu textuel (``texte``) concatène les champs
humains de la fiche (titre, description, délai, frais, administrations) :
c'est lui que la recherche explorera.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .common import Langue


class FicheAIndexer(BaseModel):
    """Une fiche de procédure prête à l'indexation.

    Construite par le service corpus, publiée au service recherche via la
    file ``fiches.to_index``. L'idempotence côté consommation s'appuie sur
    ``fiche_id`` : rejouer un message ne crée jamais de doublon.
    """

    fiche_id: str = Field(
        ...,
        description="Identifiant unique et stable de la fiche (clé d'idempotence de l'indexation)",
    )
    document_id: str = Field(..., description="Clé canonique du document normalisé parent")
    titre: str = Field(..., min_length=1)
    langue: Langue
    texte: str = Field(..., min_length=1, description="Contenu textuel de la fiche, sans balisage")
    thematique_id: str | None = Field(
        default=None, description="Identifiant de la thématique Idarati, si connue"
    )
    administration: str | None = Field(
        default=None, description="Administration en charge de la procédure"
    )
