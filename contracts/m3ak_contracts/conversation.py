"""Contrats de conversation — API publique du service conversation (phase 2, étape 4).

Le service conversation est l'orchestrateur client : sessions, langue par
message, anonymisation avant tout envoi, refus explicite si source
insuffisante (seuil calibré), citations vérifiées mécaniquement,
avertissements contextuels. La plateforme informe et oriente ; elle ne
remplace ni l'avocat, ni le juge, ni l'administration.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

LangueEchange = Literal["ar", "fr"]


class DemandeQuestion(BaseModel):
    """Une question posée en langage naturel (arabe ou français)."""

    question: str = Field(min_length=1, max_length=2000)
    fournisseur: str | None = Field(
        default=None,
        description="Option de test/évaluation : forcer le fournisseur de génération "
        "(zai | google | local) ; None = fournisseur par défaut de la plateforme. "
        "L'interface publique ne l'expose pas.",
    )

    @field_validator("question")
    @classmethod
    def question_non_vide(cls, valeur: str) -> str:
        if not valeur.strip():
            raise ValueError("la question ne doit pas être vide")
        return valeur.strip()


class SourceCitee(BaseModel):
    """Une source retenue — la famille porte la hiérarchie des sources à
    l'affichage : un texte de loi (segment/adala) n'est jamais présenté
    comme équivalent à une décision de justice ou à une procédure."""

    unite_id: str
    famille: Literal["segment", "fiche", "passage"]
    document_id: str
    titre: str
    source: str
    collection: str
    langue: str
    score: float = Field(description="Pertinence croisée 0-1 (reranker)")


class ReponseQuestion(BaseModel):
    """Réponse vérifiée — ou refus explicite, jamais de réponse sans source."""

    session_id: str
    message_id: str
    reponse: str
    langue_question: LangueEchange
    refus: bool = Field(
        default=False,
        description="Vrai si aucune source suffisante ou réponse invérifiable",
    )
    motif_refus: str | None = Field(
        default=None,
        description="aucune_source | score_insuffisant | generation_non_verifiable | "
        "insuffisant_modele | generation_tronquee",
    )
    sources: list[SourceCitee] = Field(
        default_factory=list,
        description="Passages retenus, numérotés comme les [n] de la réponse",
    )
    citations_verifiees: bool = Field(
        default=True,
        description="Vrai si chaque citation verbatim a été retrouvée dans les passages cités",
    )
    citations_rejetees: int = Field(
        default=0, description="Nombre de renvois [n] rejetés (passage inexistant)"
    )
    avertissements: list[str] = Field(
        default_factory=list,
        description="Avertissements contextuels (droits, argent, liberté en jeu)",
    )
    repli_interlingue: bool = Field(
        default=False,
        description="Vrai si les sources retenues sont dans l'autre langue que la question",
    )
    texte_partiel: bool = Field(
        default=False,
        description="Vrai si `reponse` porte un texte vérifié diffusé avant un échec "
        "final (réponse partielle) — faux pour un refus sec, où `reponse` porte le "
        "message de refus standard. L'affichage distingue les deux cas par CE champ, "
        "jamais par le contenu du texte.",
    )
    sans_reponse: bool = Field(
        default=False,
        description="Vrai si `reponse` est la description VÉRIFIÉE des passages "
        "consultés quand aucun ne répond à la question (orientation : ce que les "
        "passages contiennent, puis l'absence de réponse est dite explicitement). "
        "`refus` reste vrai — aucune réponse fiable n'a été produite — mais "
        "`reponse` porte un contenu utile et vérifié, pas un message de refus.",
    )
    score_meilleur: float | None = None
    fournisseur: str | None = None
    modele: str | None = None
    latence_generation_ms: int = 0
    duree_ms: int = 0


class EvenementConversation(BaseModel):
    """Un événement du flux SSE de conversation.

    `statut` : phase en cours (recherche, generation) ; `sources` : les
    sources retenues dès qu'elles sont connues ; `fragment` : morceau de la
    réponse VÉRIFIÉE (aucun texte n'est diffusé avant vérification) ;
    `fin` : la réponse complète et ses métadonnées ; `erreur` : incident.
    """

    type: Literal["statut", "sources", "fragment", "fin", "erreur"]
    statut: str | None = None
    sources: list[SourceCitee] | None = None
    texte: str | None = None
    reponse: ReponseQuestion | None = None
    erreur: str | None = None


class ReponseSession(BaseModel):
    """Identifiant d'une nouvelle session (accès sans compte en V1)."""

    session_id: str
    cree_le: str = Field(description="Horodatage ISO 8601")


class MessageHistorique(BaseModel):
    """Un message restauré — avec ce que l'affichage a besoin pour se
    rendre à l'identique du flux : sources citées (consultation PDF et
    checklist de démarche), motif de refus et marqueurs d'affichage.
    Les identifiants de messages n'y figurent pas : le signalement
    reste un geste du message vivant."""

    role: Literal["user", "assistant"]
    contenu: str
    langue: LangueEchange | None = None
    cree_le: str
    refus: bool = False
    signale: bool = False
    sources: list[SourceCitee] = Field(
        default_factory=list,
        description="Passages cités, dans l'ordre des renvois [n] de la réponse",
    )
    motif_refus: str | None = None
    avertissements: list[str] = Field(default_factory=list)
    repli_interlingue: bool = False
    texte_partiel: bool = False
    sans_reponse: bool = False


class ReponseHistorique(BaseModel):
    session_id: str
    messages: list[MessageHistorique]


class DemandeSignalement(BaseModel):
    """Signalement d'une réponse par l'utilisateur (V1 : motif libre)."""

    motif: str = Field(min_length=1, max_length=1000)


class ReponseSignalement(BaseModel):
    message_id: str
    signale: bool
