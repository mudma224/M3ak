"""Quota global plateforme (phase 2, décision actée).

Deux fenêtres glissantes — requêtes/minute et jetons/jour — dans Redis
(ZSET horodatés, purge des entrées expirées). Sans Redis (tests), un
stockage mémoire équivalent prend le relais. Quand le quota est atteint,
la file d'attente du service fait patienter la requête au lieu de
l'échouer ; l'horloge est injectable pour des tests déterministes.
"""

from __future__ import annotations

import time
import uuid

SECONDES_MINUTE = 60
SECONDES_JOUR = 86_400


class GestionnaireQuota:
    def __init__(
        self,
        redis_url: str | None,
        limite_requetes_minute: int,
        limite_jetons_jour: int,
        horloge=time.time,
    ) -> None:
        self.limite_requetes_minute = limite_requetes_minute
        self.limite_jetons_jour = limite_jetons_jour
        self._horloge = horloge
        self._client = None
        self._requetes_memoire: list[float] = []
        self._jetons_memoire: list[tuple[float, int]] = []
        if redis_url:
            try:
                import redis.asyncio as redis_async

                # decode_responses : les membres ZSET arrivent en str, pas en
                # octets (sinon la somme des jetons échoue en production).
                self._client = redis_async.from_url(
                    redis_url, socket_timeout=2.0, decode_responses=True
                )
            except Exception:  # pragma: no cover — dépendance absente ou URL invalide
                self._client = None

    async def verifier(self, jetons_estimes: int) -> tuple[bool, float]:
        """(autorisé, attente estimée en secondes) — n'enregistre rien."""
        maintenant = self._horloge()
        requetes = await self._compter_requetes(maintenant)
        if requetes >= self.limite_requetes_minute:
            return False, self._attente_requetes(maintenant)
        jetons = await self._sommer_jetons(maintenant)
        if jetons + jetons_estimes > self.limite_jetons_jour:
            return False, self._attente_jetons(maintenant)
        return True, 0.0

    async def consommer(self, jetons: int) -> None:
        """Enregistre une requête et sa consommation de jetons."""
        maintenant = self._horloge()
        if self._client is not None:
            await self._client.zremrangebyscore(
                "m3ak:llm:requetes", 0, maintenant - SECONDES_MINUTE
            )
            await self._client.zadd(
                "m3ak:llm:requetes", {uuid.uuid4().hex: maintenant}
            )
            await self._client.zremrangebyscore(
                "m3ak:llm:jetons", 0, maintenant - SECONDES_JOUR
            )
            await self._client.zadd(
                "m3ak:llm:jetons", {f"{jetons}:{uuid.uuid4().hex}": maintenant}
            )
            return
        self._requetes_memoire = [
            t for t in self._requetes_memoire if t > maintenant - SECONDES_MINUTE
        ]
        self._requetes_memoire.append(maintenant)
        self._jetons_memoire = [
            (t, j) for (t, j) in self._jetons_memoire if t > maintenant - SECONDES_JOUR
        ]
        self._jetons_memoire.append((maintenant, jetons))

    async def etat(self) -> dict:
        """État courant des deux fenêtres — exposé par /generation/quota."""
        maintenant = self._horloge()
        return {
            "requetes_minute": {
                "limite": self.limite_requetes_minute,
                "utilisees": await self._compter_requetes(maintenant),
            },
            "jetons_jour": {
                "limite": self.limite_jetons_jour,
                "utilises": await self._sommer_jetons(maintenant),
            },
        }

    async def fermer(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

    # ── Internes — Redis, ou mémoire si le courtier de cache est absent ──

    async def _compter_requetes(self, maintenant: float) -> int:
        if self._client is not None:
            await self._client.zremrangebyscore(
                "m3ak:llm:requetes", 0, maintenant - SECONDES_MINUTE
            )
            return int(await self._client.zcard("m3ak:llm:requetes") or 0)
        return len(
            [t for t in self._requetes_memoire if t > maintenant - SECONDES_MINUTE]
        )

    async def _sommer_jetons(self, maintenant: float) -> int:
        if self._client is not None:
            await self._client.zremrangebyscore(
                "m3ak:llm:jetons", 0, maintenant - SECONDES_JOUR
            )
            entrees = await self._client.zrange("m3ak:llm:jetons", 0, -1)
            return sum(int(str(m).split(":", 1)[0]) for m in entrees)
        return sum(
            j for (t, j) in self._jetons_memoire if t > maintenant - SECONDES_JOUR
        )

    def _attente_requetes(self, maintenant: float) -> float:
        if self._client is not None:
            return 0.0  # l'estimation fine via Redis est inutile : le pas d'attente sonde
        if not self._requetes_memoire:
            return 0.0
        plus_ancienne = min(self._requetes_memoire)
        return max(0.0, plus_ancienne + SECONDES_MINUTE - maintenant)

    def _attente_jetons(self, maintenant: float) -> float:
        if self._client is not None:
            return 0.0
        if not self._jetons_memoire:
            return 0.0
        plus_ancien = min(t for (t, _) in self._jetons_memoire)
        return max(0.0, plus_ancien + SECONDES_JOUR - maintenant)
