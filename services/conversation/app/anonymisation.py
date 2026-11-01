"""Anonymisation avant tout envoi (contrainte §7.2) — placeholders, jamais de
re-substitution.

Le détecteur V1 est heuristique et volontairement conservateur : formules
de présentation et titres suivis d'un nom, en français et en arabe. La
structure est prête à recevoir un vrai NER local en phase 5 sans changer
les appels. Les noms des parties ne figurent jamais dans les réponses
générées : le jeton [PERSONNE_n] remplace le nom dès la recherche et la
génération, et le prompt interdit de le restituer.
"""

from __future__ import annotations

import re

_MOTIFS: tuple[re.Pattern, ...] = (
    # Formules de présentation (français : insensible à la casse)
    re.compile(r"\b(?:je m['’]appelle|je suis)\s+([A-ZÀ-Þ][\wÀ-ÿ'-]*)", re.IGNORECASE),
    re.compile(r"\b(?:اسمي|أنا)\s+([\u0600-\u06FF]{2,})"),
    # Titres suivis d'un nom
    re.compile(r"\b(?:M\.|Mme|Mr|Monsieur|Madame)\s+([A-ZÀ-Þ][\wÀ-ÿ'-]*)", re.IGNORECASE),
    re.compile(r"\b(?:السيد|السيدة|الأستاذ|الأستاذة)\s+([\u0600-\u06FF]{2,})"),
)

_JETON = "[PERSONNE_{n}]"


def anonymiser(texte: str) -> tuple[str, int]:
    """Retourne (texte anonymisé, nombre de noms masqués) — sans re-substitution."""
    compteur = 0

    def remplacer(_match: re.Match) -> str:
        nonlocal compteur
        compteur += 1
        return _JETON.format(n=compteur)

    resultat = texte
    for motif in _MOTIFS:
        resultat = motif.sub(remplacer, resultat)
    return resultat, compteur
