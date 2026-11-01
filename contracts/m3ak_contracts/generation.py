"""Contrats de génération — API interne du service llm (phase 2, étapes 3 et 3bis).

Le service llm est une brique interne « messages entrants → texte généré »,
derrière des fournisseurs commutables (Z.ai — GLM, Google AI Studio,
serveur local compatible OpenAI), avec quota global et file d'attente.
La génération est disponible en un seul bloc (`ReponseGeneration`) ou en
flux de fragments (SSE, `EvenementGeneration`) pour la perception de
latence. L'orchestration (citations, langue, anonymisation) relève de la
couche conversation.
"""

from typing import Literal

from pydantic import BaseModel, Field


class MessageGeneration(BaseModel):
    """Un message du fil envoyé au fournisseur."""

    role: Literal["system", "user", "assistant"]
    contenu: str = Field(min_length=1, max_length=64_000)


class DemandeGeneration(BaseModel):
    """Demande de génération — température 0 par défaut (harnais reproductible)."""

    messages: list[MessageGeneration] = Field(min_length=1)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_jetons: int = Field(default=1024, ge=16, le=8192)
    fournisseur: str | None = Field(
        default=None,
        description="None = fournisseur par défaut de la configuration ; le service valide contre ses fournisseurs configurés",
    )


class ReponseGeneration(BaseModel):
    """Texte généré, avec la traçabilité de consommation du quota."""

    texte: str
    fournisseur: str
    modele: str
    jetons_entree: int | None = None
    jetons_sortie: int | None = None
    latence_ms: int = 0
    attendu: bool = Field(
        default=False,
        description="Vrai si la requête a patienté dans la file (quota atteint)",
    )
    attente_secondes: float = 0.0
    complet: bool | None = Field(
        default=None,
        description="Vrai si le fournisseur a terminé naturellement ; faux si le budget "
        "de jetons a tronqué la réponse ; None si le fournisseur ne le signale pas",
    )


class EvenementGeneration(BaseModel):
    """Un événement du flux SSE de génération (étape 3bis).

    Séquence nominale : un seul `debut` en tête (fournisseur et modèle
    résolus), puis des `fragment` au fil de la production, puis un `fin`
    (usage final, latence, attente). Un `erreur` remplace le reste du flux
    si le fournisseur tombe après ouverture — le statut HTTP est déjà
    parti (200), l'incident voyage donc dans le flux.
    """

    type: Literal["debut", "fragment", "fin", "erreur"]
    texte: str | None = Field(
        default=None, description="fragment : le morceau de texte produit"
    )
    fournisseur: str | None = Field(
        default=None, description="debut : fournisseur effectivement utilisé"
    )
    modele: str | None = Field(
        default=None, description="debut : modèle résolu côté fournisseur"
    )
    jetons_entree: int | None = Field(
        default=None, description="fin : jetons du prompt (estimés si le fournisseur ne les rapporte pas)"
    )
    jetons_sortie: int | None = Field(
        default=None, description="fin : jetons produits (estimés si le fournisseur ne les rapporte pas)"
    )
    latence_ms: int | None = Field(
        default=None, description="fin : durée de la génération elle-même"
    )
    attendu: bool | None = Field(
        default=None, description="fin : vrai si la requête a patienté dans la file"
    )
    attente_secondes: float | None = Field(
        default=None, description="fin : temps passé en file d'attente"
    )
    erreur: str | None = Field(
        default=None, description="erreur : description de l'incident"
    )
    complet: bool | None = Field(
        default=None,
        description="fin : vrai si le fournisseur a terminé naturellement, faux si le "
        "budget de jetons a tronqué la réponse, None s'il ne le signale pas",
    )
