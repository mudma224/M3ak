"""Service whatsapp — sans base de données en V1.

Le service whatsapp ne possède pas encore de données propres (voir
AGENTS.md, architecture). Ce module est conservé pour l'uniformité des
squelettes : ses fonctions sont volontairement inertes, et aucune
dépendance de base de données n'est installée dans l'image.
"""

from __future__ import annotations


async def initialiser_base(url: str) -> None:
    """Sans objet : le service whatsapp ne possède pas de base en V1."""
    raise NotImplementedError("le service whatsapp ne possede pas de base en V1")


async def verifier_base() -> bool:
    return False


async def fermer_base() -> None:
    return None
