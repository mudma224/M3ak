"""Authentification des appels internes par jeton partagé.

Le réseau Docker isole déjà les services entre eux ; le jeton empêche
l'erreur humaine (un appel oublié, un environnement mal configuré).
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from .config import get_settings


async def exiger_jeton_interne(
    x_internal_token: str | None = Header(default=None),
) -> str:
    attendu = get_settings().internal_token
    if not x_internal_token or not secrets.compare_digest(x_internal_token, attendu):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Jeton interne manquant ou invalide",
        )
    return x_internal_token
