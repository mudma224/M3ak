"""Contrats de recherche — API interne du service recherche (phase 2).

La recherche hybride (plein texte + vectoriel) restitue des unités
d'indexation portant leur nature : segment d'article (textes normatifs),
fiche de procédure, passage de décision (jurisprudence). La source et la
collection sont dérivées de la famille — chaque table de l'index est
alimentée par une source unique (segments : Adala ; fiches : Idarati ;
passages : Jurisprudence.ma) — pour que la couche conversation applique
la hiérarchie des sources à la présentation.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .common import Langue

FAMILLES_UNITE = ("segment", "fiche", "passage")

# Une famille d'unité = une source du corpus (à revoir si une table reçoit
# une seconde source, ex. Bulletin officiel vers segments).
SOURCES_PAR_FAMILLE = {
    "segment": ("adala", "textes_normatifs"),
    "fiche": ("idarati", "procedures"),
    "passage": ("jurisprudence_ma", "jurisprudence"),
}

FamilleUnite = Literal["segment", "fiche", "passage"]


class RequeteRecherche(BaseModel):
    """Question posée à l'index.

    Aucun filtrage par langue : l'embedding est cross-lingue (BGE-M3) et le
    plein texte interroge les deux langues ; la stratégie de langue (travail
    dans la langue du message, repli interlingue) relève de la couche
    conversation, qui décide avec les langues renvoyées par résultat.
    """

    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=10, ge=1, le=50)
    familles: list[str] | None = Field(
        default=None,
        description="Restreindre la recherche à certaines familles d'unités ; None = toutes",
    )

    @field_validator("question")
    @classmethod
    def question_non_vide(cls, valeur: str) -> str:
        if not valeur.strip():
            raise ValueError("la question ne doit pas être vide")
        return valeur


class ResultatRecherche(BaseModel):
    """Une unité d'indexation restituée, avec son contexte de restitution."""

    unite_id: str = Field(description="segment_id, fiche_id ou passage_id")
    famille: FamilleUnite
    document_id: str = Field(
        description="Clé canonique du document parent — la citation restitue le document entier",
    )
    titre: str
    texte: str
    langue: Langue
    source: str = Field(description="adala, idarati ou jurisprudence_ma (hiérarchie des sources)")
    collection: str
    contexte: dict = Field(
        default_factory=dict,
        description="Héritage du document (livre/chapitre/section, décision, thématique…)",
    )
    score: float = Field(
        description="Score de fusion (RRF) — comparables entre eux, seuil calibré en phase 2",
    )


class ReponseRecherche(BaseModel):
    resultats: list[ResultatRecherche]
    mode: Literal["hybride", "texte"] = Field(
        description="hybride (plein texte + vectoriel) ou texte (dégradation : serveur d'embeddings indisponible)",
    )
    rerank: bool = Field(
        default=False,
        description="Vrai si le pool a été re-classé par le reranker (sinon ordre d'entrelacement) ; les scores portent alors la pertinence croisée question-passage (0-1) — le seuil de refus se calibre dessus",
    )
    cache: bool = Field(default=False, description="Résultat servi depuis le cache Redis")
    duree_ms: int = 0
