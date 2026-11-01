"""Schémas des documents normalisés M3ak.

Un document normalisé = métadonnées communes (identifiant stable, source,
dates, empreinte) + champs spécifiques à sa collection. La discrimination
s'effectue sur le champ ``collection`` (union discriminée Pydantic).
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from .common import (
    Collection,
    EmpreinteContenu,
    HorodatageCollecte,
    IdentifiantStable,
    Langue,
    StatutJuridique,
    TypeTexte,
)


class MetadonneesCommunes(BaseModel):
    """Champs portés par tout document du corpus, quelle que soit sa collection."""

    identifiant: IdentifiantStable
    collection: Collection
    source: str = Field(..., description="Portail d'origine (adala, sgg, idarati, jurisprudence_ma, data_gov)")
    titre: str
    langue: Langue
    url_source: str | None = Field(
        default=None,
        description="Lien vers l'original — renseigné par la couche affichage (après la V1) ; les identifiants sources permettent de le reconstruire",
    )
    chemin_local: str = Field(..., description="Emplacement du fichier dans la couche brute")
    dates: HorodatageCollecte
    empreinte: EmpreinteContenu
    statut_juridique: StatutJuridique = Field(
        default=StatutJuridique.INCONNU,
        description="Réservé — non renseigné en V1 ; alimenté par le rapprochement BO ultérieur",
    )


class ReferenceBulletinOfficiel(BaseModel):
    """Lien d'un texte avec sa publication au Bulletin officiel."""

    numero: str = Field(..., description="Numéro du Bulletin officiel")
    date_publication: date


class ChampsTexteNormatif(BaseModel):
    """Champs spécifiques à la collection des textes normatifs."""

    type_texte: TypeTexte
    numero_loi: str | None = Field(default=None, description="Numéro de loi lorsque distinct du dahir")
    theme: str | None = None
    reference_bo: ReferenceBulletinOfficiel | None = None
    version_consolidee: bool = Field(
        default=False, description="Vrai si le document est une version consolidée"
    )
    mots_cles: list[str] = Field(default_factory=list)


class ChampsProcedure(BaseModel):
    """Champs spécifiques à la collection des procédures administratives (Idarati)."""

    prix: str | None = Field(default=None, description="Frais indiqués par la fiche (ex. 'gratuit')")
    delai_moyen_jours: int | None = None
    administration_charge: str | None = None
    administrations_receptrices: list[str] = Field(default_factory=list)
    formulaires: list[str] = Field(default_factory=list, description="URLs des formulaires téléchargeables")
    url_procedure: str | None = None


class ChampsDecision(BaseModel):
    """Champs spécifiques à la collection jurisprudentielle."""

    juridiction: str
    ville: str | None = None
    numero_decision: str | None = None
    numero_dossier: str | None = None
    date_decision: date | None = None
    themes: list[str] = Field(default_factory=list)
    mots_cles: list[str] = Field(default_factory=list)
    source_publication: str | None = Field(
        default=None, description="Source de publication de la décision ('non publiée', bulletin, etc.)"
    )
    a_resume_francais: bool = False
    texte_integral_arabe: bool = Field(default=False, description="Vrai si le texte intégral est disponible en arabe")


class ChampsDonneeComplementaire(BaseModel):
    """Champs spécifiques à la collection des données ouvertes."""

    organisme_producteur: str | None = None
    licence: str | None = Field(default=None, description="Licence de réutilisation (ex. ODbL-1.0)")
    date_mise_a_jour: date | None = None


class DocumentTexteNormatif(MetadonneesCommunes):
    collection: Collection = Collection.TEXTES_NORMATIFS
    specifics: ChampsTexteNormatif


class DocumentProcedure(MetadonneesCommunes):
    collection: Collection = Collection.PROCEDURES
    specifics: ChampsProcedure


class DocumentDecision(MetadonneesCommunes):
    collection: Collection = Collection.JURISPRUDENCE
    specifics: ChampsDecision


class DocumentDonneeComplementaire(MetadonneesCommunes):
    collection: Collection = Collection.DONNEES_COMPLEMENTAIRES
    specifics: ChampsDonneeComplementaire


DocumentNormalise = (
    DocumentTexteNormatif
    | DocumentProcedure
    | DocumentDecision
    | DocumentDonneeComplementaire
)
"""Union des documents normalisés. La discrimination se fait par validation
sur chaque sous-type (champ ``collection`` figé par sous-type)."""
