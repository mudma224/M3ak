"""Client du serveur d'embeddings (Text Embeddings Inference).

Composant d'infrastructure du service recherche : BGE-M3 y tourne en CPU
(1024 dimensions, multilingue ar/fr, cross-lingue). Toute indisponibilité
est signalée au appelant par une exception — la dégradation gracieuse
(recherche plein texte seul) est décidée au niveau du moteur de recherche,
pas ici.
"""

from __future__ import annotations

import httpx

# Aligné sur --max-client-batch-size du serveur TEI (compose) : le budget
# mémoire du serveur est borné pour la machine de développement.
TAILLE_LOT = 32

# Garde-fou : rester sous les 8192 tokens de BGE-M3 dans tous les cas
# (8 000 caractères ≈ 2,5-3 k tokens). Quelques segments regroupés
# dépassent — ils sont encodés sur leur début ; la citation restitue de
# toute façon le texte entier (principe « chercher fin, restituer entier »).
MAX_CARACTERES = 8_000

# Budget par requête client : ~24 000 caractères ≈ 8-10 k tokens ≈ quelques
# minutes d'inférence CPU — chaque requête reste loin du timeout.
BUDGET_CARACTERES_LOT = 24_000


def decouper_par_budget(textes: list[str]) -> list[list[str]]:
    """Groupes de textes bornés en caractères ET en nombre de séquences.

    Les textes sont tronqués à MAX_CARACTERES ; aucune requête ne dépasse
    TAILLE_LOT séquences ni BUDGET_CARACTERES_LOT caractères.
    """
    groupes: list[list[str]] = []
    courant: list[str] = []
    budget = 0
    for texte in textes:
        tronque = texte[:MAX_CARACTERES]
        if courant and (
            budget + len(tronque) > BUDGET_CARACTERES_LOT or len(courant) >= TAILLE_LOT
        ):
            groupes.append(courant)
            courant, budget = [], 0
        courant.append(tronque)
        budget += len(tronque)
    if courant:
        groupes.append(courant)
    return groupes


class ErreurEmbeddings(RuntimeError):
    """Le serveur d'embeddings ne répond pas ou répond une charge invalide."""


class ClientTEI:
    def __init__(self, url_base: str, timeout: float = 900.0) -> None:
        # Timeout très large : l'inférence CPU est lente sur les lots de
        # textes longs (un lot peut dépasser 5 min) ; le premier appel
        # déclenche en plus l'initialisation des graphes ONNX. /health garde
        # son propre timeout court.
        self._url_base = url_base.rstrip("/")
        self._timeout = timeout

    async def verifier(self) -> bool:
        """Vrai si le serveur répond à /health (utilisé par /ready)."""
        try:
            async with httpx.AsyncClient(
                timeout=3.0, trust_env=False
            ) as client:
                reponse = await client.get(f"{self._url_base}/health")
                return reponse.status_code == 200
        except Exception:
            return False

    async def encoder(self, textes: list[str]) -> list[list[float]]:
        """Vecteurs des textes — lots bornés en caractères et en séquences.

        Retourne autant de vecteurs que de textes fournis, dans l'ordre.
        """
        if not textes:
            return []
        vecteurs: list[list[float]] = []
        async with httpx.AsyncClient(timeout=self._timeout, trust_env=False) as client:
            for groupe in decouper_par_budget(textes):
                reponse = await client.post(
                    f"{self._url_base}/embed",
                    json={"inputs": groupe, "truncate": True},
                )
                if reponse.status_code != 200:
                    raise ErreurEmbeddings(
                        f"serveur d'embeddings : HTTP {reponse.status_code}"
                    )
                try:
                    lot = reponse.json()
                except ValueError as erreur:
                    raise ErreurEmbeddings("serveur d'embeddings : charge invalide") from erreur
                if not isinstance(lot, list) or len(lot) != len(groupe):
                    raise ErreurEmbeddings(
                        "serveur d'embeddings : nombre de vecteurs inattendu"
                    )
                vecteurs.extend(lot)
        return vecteurs
