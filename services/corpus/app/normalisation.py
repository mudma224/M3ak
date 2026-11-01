"""Normalisation des segments segmentés vers le contrat SegmentArticle.

C'est ici que les segments produits par la grammaire de segmentation
(deviennent) les unités publiées au service recherche via la file
``segments.to_index`` : identifiant stable de document, segment_id
idempotent, contexte hiérarchique, langue.

L'identifiant stable documenté (AGENTS.md §5) : numéro officiel + langue +
version. Pour le corpus pilote, la référence de promulgation Adala sert de
numéro officiel ; la version est « originale » tant qu'aucune donnée de
consolidation n'est disponible — une version consolidée produira une autre
clé, donc d'autres segment_id, sans jamais écraser les existants.
"""

from __future__ import annotations

import re

from m3ak_contracts import ContexteHierarchique, IdentifiantStable, Langue, SegmentArticle


def cle_document(reference: str, langue: str = "fr", version: str = "originale") -> str:
    """Clé canonique de l'identifiant stable d'un texte."""
    return IdentifiantStable(
        numero_officiel=reference, langue=Langue(langue), version=version
    ).cle()


def _slug_numero(numero_brut: str) -> str:
    return re.sub(r"\s+", "-", numero_brut.strip())


def construire_segment_article(
    document_id: str,
    texte_titre: str,
    article: dict,
    langue: str,
    position: int,
) -> SegmentArticle:
    """Convertit un segment de la grammaire en SegmentArticle du contrat.

    Le ``segment_id`` porte la position de l'article dans le document :
    certains textes contiennent des numéros répétés (annexes, reprises de
    numérotation partielles — 30 occurrences sur le pilote), et la position
    seule garantit l'unicité. Elle reste déterministe (même document →
    mêmes positions), donc rejouer la publication ne crée jamais de
    doublon ; un texte modifié produira une autre version de l'identifiant
    stable, donc d'autres segment_id.
    """
    contexte_brut = article.get("contexte") or {}
    return SegmentArticle(
        segment_id=f"{document_id}#art-{_slug_numero(article['numero_brut'])}-{position:04d}",
        document_id=document_id,
        articles=[article["numero_brut"]],
        texte=article["texte"],
        langue=Langue(langue),
        contexte=ContexteHierarchique(
            texte_identifiant=document_id,
            texte_titre=texte_titre,
            livre=contexte_brut.get("livre"),
            chapitre=contexte_brut.get("chapitre"),
            section=contexte_brut.get("section"),
        ),
    )


def construire_lot(document: dict, segmentation: dict, taille_lot: int = 100) -> list[list[SegmentArticle]]:
    """Lots de SegmentArticle prêts à être publiés pour un document.

    Le découpage en lots bornés (100 segments) évite les messages
    volumineux sur la file ; chaque lot est autonome (un document entier
    est la réunion de ses lots, l'ordre n'a pas d'importance —
    l'idempotence par segment_id absorbe les rejouements).

    Les segments au texte vide (frontière détectée sans contenu — dernier
    article avant une annexe, table) sont exclus : le contrat exige un
    texte non vide, et un segment vide n'a rien à indexer.
    """
    langue = segmentation.get("langue", "fr")
    document_id = cle_document(document["reference_promulgation"], langue)
    segments = [
        construire_segment_article(document_id, document["intitule"], article, langue, position)
        for position, article in enumerate(
            (a for a in segmentation["articles"] if a.get("texte", "").strip()), start=1
        )
    ]
    return [segments[i : i + taille_lot] for i in range(0, len(segments), taille_lot)] or []
