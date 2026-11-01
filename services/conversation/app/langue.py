"""Langue de travail — la langue du dernier message de l'utilisateur (acté).

Aucune langue par défaut n'existe dans les sources ; côté échanges, la
détection est locale et simple : part d'écriture arabe dans les lettres
du message. Un message sans lettres exploitables retombe sur le français
(langue de travail de repli, BCP 47 : fr-MA).
"""

from __future__ import annotations

import re

_ARABE = re.compile(r"[\u0600-\u06FF]")


def detecter_langue(texte: str) -> str:
    """« ar » si les lettres arabes forment au moins 30 % des lettres, « fr » sinon."""
    lettres = [c for c in texte if c.isalpha()]
    if not lettres:
        return "fr"
    arabes = sum(1 for c in lettres if _ARABE.match(c))
    return "ar" if arabes / len(lettres) >= 0.3 else "fr"
