"""Schémas des décisions de justice — l'unité d'indexation de la
jurisprudence.

Une décision (Jurisprudence.ma) est un document bilingue : résumé en
français, texte intégral en arabe (parfois les deux langues). Elle
circule sur sa propre file (``decisions.to_index``), à part des segments
d'articles et des fiches de procédures, avec les deux langues portées
par des champs distincts — c'est le matériau du repli interlingue.
Les contenus arrivent débarrassés de leur balisage HTML : la couche
index ne conserve que du texte.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .common import Langue


class DecisionAIndexer(BaseModel):
    """Une décision de justice prête à l'indexation.

    Construite par le service corpus, publiée au service recherche via la
    file ``decisions.to_index``. L'idempotence côté consommation s'appuie
    sur ``decision_id`` : rejouer un message ne crée jamais de doublon.
    """

    decision_id: str = Field(
        ...,
        description="Identifiant unique et stable de la décision (clé d'idempotence de l'indexation)",
    )
    document_id: str = Field(..., description="Clé canonique du document normalisé parent")
    titre: str = Field(..., min_length=1)
    langue: Langue = Field(..., description="Langue du texte intégral (ar), sinon le résumé (fr)")
    thematiques: list[str] = Field(
        default_factory=list, description="Slugs des thématiques de la source"
    )
    juridiction: str | None = None
    ville: str | None = None
    type_decision: str | None = None
    numero_decision: str | None = None
    numero_dossier: str | None = None
    date_decision: str | None = Field(
        default=None, description="Date de la décision au format ISO (AAAA-MM-JJ), si connue"
    )
    resume_francais: str | None = None
    resume_arabe: str | None = None
    texte_integral_arabe: str | None = None
    texte_integral_francais: str | None = None
