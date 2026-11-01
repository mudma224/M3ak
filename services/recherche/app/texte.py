"""Normalisation du texte pour le plein texte (phase 2).

PostgreSQL n'a pas de stemming arabe natif : la robustesse côté arabe est
apportée ici, applicativement — suppression des diacritiques et du tatweel,
unification des formes d'alef, de yāʾ/alef maqsura et des porteurs de
hamza, assimilation du tāʾ marbūṭa au hāʾ (normalisation ISRI standard).
La transformation est inoffensive sur le texte français : les mêmes
règles sont donc appliquées à tout l'index et à toute question, et une
seule configuration de lexèmes PostgreSQL (`french`) suffit — le stemmer
français laisse les tokens arabes inchangés.
"""

from __future__ import annotations

import re

# Diacritiques (tashkīl, tanwīn), petite alef, marques de récitation,
# tatweel : jamais porteurs de sens pour la recherche.
MARQUES_PATTERN = "[\u0640\u064B-\u065F\u0670\u06D6-\u06ED]"

# Remappage caractère à caractère : formes d'alef, yāʾ/alef maqsura,
# porteurs de hamza, tāʾ marbūṭa -> hāʾ (normalisation ISRI standard).
REMPLACEMENTS_ARABE = (
    ("أ", "ا"),
    ("إ", "ا"),
    ("آ", "ا"),
    ("ٱ", "ا"),
    ("ى", "ي"),
    ("ؤ", "و"),
    ("ئ", "ي"),
    ("ة", "ه"),
)

_MARQUES = re.compile(MARQUES_PATTERN)
_FORMES_UNIFIEES = dict(REMPLACEMENTS_ARABE)


def normaliser(texte: str) -> str:
    """Normalisation arabe — sans effet sur le texte latin.

    La même transformation existe côté SQL (miroir exact : voir
    `_expression_normalisee` de db.py, qui utilise MARQUES_PATTERN et
    REMPLACEMENTS_ARABE) pour que l'index et les requêtes partagent
    exactement les mêmes règles.
    """
    sans_marques = _MARQUES.sub("", texte)
    return "".join(_FORMES_UNIFIEES.get(c, c) for c in sans_marques)
