"""Cache Redis des recherches (phase 2, décision actée D5 — sobriété).

Clé = empreinte de la question normalisée + paramètres de la requête ;
TTL court (1 h). Toute indisponibilité de Redis est ignorée : le cache
est une optimisation, jamais une dépendance — la recherche s'exécute
sans lui, en mode dégradé silencieux.
"""

from __future__ import annotations

import hashlib
import json
import logging

logger = logging.getLogger(__name__)

TTL_SECONDES = 3600


class CacheRecherche:
    def __init__(self, url: str | None) -> None:
        self._client = None
        if url:
            try:
                import redis.asyncio as redis_async

                self._client = redis_async.from_url(url, socket_timeout=1.0)
            except Exception as erreur:  # pragma: no cover — dépendance absente ou URL invalide
                logger.warning("cache Redis indisponible : %s", erreur)
                self._client = None

    @staticmethod
    def cle(question_normalisee: str, top_k: int, familles: tuple[str, ...] = ()) -> str:
        """La clé porte TOUS les paramètres qui changent le résultat :
        question normalisée, top_k et familles filtrées (sinon deux requêtes
        différentes partageaient la même entrée — résultat faux servi en silence)."""
        empreinte = hashlib.sha256(
            f"{question_normalisee}\x1f{top_k}\x1f{','.join(sorted(familles))}".encode()
        ).hexdigest()
        # v2 : les entrées v1 portaient des listes vectorielles tronquées
        # (HNSW ef_search par défaut, corrigé en étape 6) — on les invalide
        # plutôt que de servir des résultats amputés.
        return f"m3ak:recherche:v2:{empreinte}"

    async def obtenir(self, cle: str) -> dict | None:
        if self._client is None:
            return None
        try:
            brut = await self._client.get(cle)
            return json.loads(brut) if brut else None
        except Exception:
            return None

    async def stocker(self, cle: str, reponse: dict) -> None:
        if self._client is None:
            return
        try:
            await self._client.set(cle, json.dumps(reponse, ensure_ascii=False), ex=TTL_SECONDES)
        except Exception:
            logger.warning("cache Redis : écriture ignorée (indisponible)")

    async def fermer(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as erreur:
                logger.debug("fermeture du cache Redis ignorée : %s", erreur)
            self._client = None
