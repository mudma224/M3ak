"""Contrats HTTP internes — DTO partagés entre services et futurs clients."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ReponseSante(BaseModel):
    """Réponse de /health : le processus est vivant. Toujours ouvert (sans jeton)."""

    status: str = Field(default="ok")
    service: str
    version: str


class EtatDependance(BaseModel):
    nom: str
    etat: str = Field(description="ok | degrade | sans_objet")


class ReponsePret(BaseModel):
    """Réponse de /ready : le service peut traiter du trafic. Protégé par jeton interne."""

    status: str = Field(description="ok | degrade | sans_base")
    service: str
    dependances: list[EtatDependance]


class ReponseEcho(BaseModel):
    """Écho interne : sert à vérifier la propagation du correlation ID."""

    service: str
    correlation_id: str


class ErreurCommune(BaseModel):
    """Corps d'erreur normalisé de toutes les API M3ak."""

    detail: str
    correlation_id: str | None = None
