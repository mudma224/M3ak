"""Passages des décisions de justice (phase 2, décision actée D3).

Une décision est bilingue (résumé français, texte intégral arabe) et trop
longue pour un seul vecteur : son texte intégral est découpé en fenêtres
d'environ 700 mots avec recouvrement, et son résumé français forme un
passage propre. Chaque passage porte le contexte de sa décision — la
recherche s'effectue sur les passages, la citation restitue toujours la
décision entière (via document_id et décision_id).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from m3ak_contracts import DecisionAIndexer

FENETRE_MOTS = 700
RECOUVREMENT_MOTS = 120
SEUIL_FRAGMENT = 150  # un fragment final plus court est fusionné au précédent


@dataclass(frozen=True)
class Passage:
    """Un passage indexable d'une décision — l'unité de recherche vectorielle
    et plein texte de la jurisprudence."""

    passage_id: str
    decision_id: str
    document_id: str
    titre: str
    langue: str
    position: int
    type_contenu: str  # "resume" | "texte"
    texte: str
    contexte: dict


def decouper_en_fenetres(texte: str) -> list[str]:
    """Fenêtres de mots avec recouvrement ; le fragment final trop court
    est fusionné au passage précédent plutôt que laissé en passage minuscule."""
    mots = texte.split()
    if not mots:
        return []
    if len(mots) <= FENETRE_MOTS:
        return [" ".join(mots)]
    pas = FENETRE_MOTS - RECOUVREMENT_MOTS
    fenetres = [" ".join(mots[debut : debut + FENETRE_MOTS]) for debut in range(0, len(mots), pas)]
    if len(fenetres) > 1 and len(mots[-SEUIL_FRAGMENT:]) < SEUIL_FRAGMENT:
        dernier = fenetres[-1].split()
        fenetres[-2] = " ".join(
            fenetres[-2].split()[-RECOUVREMENT_MOTS:] + dernier
        )
        fenetres.pop()
    return fenetres


def generer_passages(decision: DecisionAIndexer) -> list[Passage]:
    """Passages d'une décision : résumé français d'abord (langue fr), puis
    fenêtres du texte intégral (langue ar). Une décision sans aucun contenu
    textuel produit une liste vide — elle reste dans sa table pour les
    métadonnées, elle n'est simplement pas recherchable."""
    passages: list[Passage] = []
    base_contexte = {
        "decision_id": decision.decision_id,
        "juridiction": decision.juridiction,
        "ville": decision.ville,
        "type_decision": decision.type_decision,
        "numero_decision": decision.numero_decision,
        "numero_dossier": decision.numero_dossier,
        "date_decision": decision.date_decision,
        "thematiques": list(decision.thematiques),
    }

    def ajouter(texte: str, langue: str, type_contenu: str) -> None:
        if not texte or not texte.strip():
            return
        position = len(passages)
        passages.append(
            Passage(
                passage_id=f"{decision.decision_id}#p{position}",
                decision_id=decision.decision_id,
                document_id=decision.document_id,
                titre=decision.titre,
                langue=langue,
                position=position,
                type_contenu=type_contenu,
                texte=texte,
                contexte={**base_contexte, "type_contenu": type_contenu},
            )
        )

    ajouter(decision.resume_francais, "fr", "resume")
    for fenetre in decouper_en_fenetres(decision.texte_integral_arabe or ""):
        passages.append(
            Passage(
                passage_id=f"{decision.decision_id}#p{len(passages)}",
                decision_id=decision.decision_id,
                document_id=decision.document_id,
                titre=decision.titre,
                langue="ar",
                position=len(passages),
                type_contenu="texte",
                texte=fenetre,
                contexte={**base_contexte, "type_contenu": "texte"},
            )
        )
    if not decision.texte_integral_arabe:
        ajouter(decision.resume_arabe, "ar", "resume")
    return passages
