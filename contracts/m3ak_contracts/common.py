"""Types communs à toutes les collections du corpus M3ak.

Ce module définit les enums et valeurs partagées par les schémas de
documents, de segments et de messages. Il est volontairement minimal :
toute évolution doit rester rétro-compatible (versionnement SemVer du
paquet + champ ``schema_version`` dans les messages).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Langue(StrEnum):
    """Langues des documents et des échanges (BCP 47 : ar-MA, fr-MA)."""

    ARABE = "ar"
    FRANCAIS = "fr"


class Collection(StrEnum):
    """Collections documentaires — la nature de la source détermine la valeur."""

    TEXTES_NORMATIFS = "textes_normatifs"
    PROCEDURES = "procedures"
    JURISPRUDENCE = "jurisprudence"
    DONNEES_COMPLEMENTAIRES = "donnees_complementaires"
    DOCUMENTS_UTILISATEUR = "documents_utilisateur"


class TypeTexte(StrEnum):
    """Types de textes normatifs marocains."""

    DAHIR = "dahir"
    LOI = "loi"
    LOI_ORGANIQUE = "loi_organique"
    DECRET = "decret"
    ARRETE = "arrete"
    DECISION = "decision"
    CIRCULAIRE = "circulaire"
    CONVENTION_INTERNATIONALE = "convention_internationale"


class StatutJuridique(StrEnum):
    """Statut juridique d'un texte.

    Réservé : la première version du corpus ne le renseigne pas
    (valeur ``INCONNU`` par défaut). Le champ existe pour éviter toute
    refonte du schéma lors de sa future alimentation.
    """

    EN_VIGUEUR = "en_vigueur"
    MODIFIE = "modifie"
    ABROGE = "abroge"
    INCONNU = "inconnu"


class IdentifiantStable(BaseModel):
    """Identifiant stable d'un document.

    Construit à partir du numéro officiel, de la langue et de la version.
    Les variantes linguistiques d'un même texte partagent ``numero_officiel``
    et ``version`` : c'est ce lien qui permet le repli interlingue
    (trouver la version arabe d'un texte français, et inversement).
    """

    numero_officiel: str = Field(
        ..., description="Numéro officiel du texte, ou identifiant source pour les procédures/décisions"
    )
    langue: Langue
    version: str = Field(
        default="originale",
        description="Version du texte : 'originale' ou 'consolidée-AAAA-MM'",
    )

    def cle(self) -> str:
        """Clé canonique de déduplication inter-sources."""
        return f"{self.numero_officiel}|{self.langue.value}|{self.version}"


class EmpreinteContenu(BaseModel):
    """Empreinte d'un contenu, pour détecter toute modification silencieuse."""

    algorithme: str = "sha256"
    valeur: str


class HorodatageCollecte(BaseModel):
    """Dates de référence d'un document dans le corpus."""

    date_publication: date | None = Field(default=None, description="Date de publication ou d'adoption")
    date_recuperation: datetime = Field(..., description="Date de collecte (date de consultation)")
