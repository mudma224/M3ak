"""Client du reranker (BGE-reranker-v2-m3 via Text Embeddings Inference).

Re-classe un pool de candidats par pertinence croisée question-passage —
le reranker lit la question ET chaque texte (contrairement aux embeddings
qui les encodent séparément) : c'est lui qui rapproche les reformulations
que ni le plein texte ni le vectoriel n'ont su classer. Scores sigmoïdes
(0-1) : le seuil de refus de la couche conversation se calibre dessus.
Toute indisponibilité est convertie en exception — le moteur décide de la
dégradation (ordre d'entrelacement conservé).
"""

from __future__ import annotations

import httpx

# Le scoring croise question et texte : tronquer les candidats borne la
# latence CPU (le texte complet reste stocké et restitué — la citation
# ne dépend pas de cette troncature). 800 caractères ≈ 250-350 tokens :
# 20 candidats ≈ 8-9 s sur 4 cœurs (calibrage mesuré, étape 6 affinera).
CARACTERES_SCORING = 800


class ErreurRerank(RuntimeError):
    """Le reranker ne répond pas ou répond une charge invalide."""


class ClientReranker:
    def __init__(self, url_base: str, timeout: float = 120.0) -> None:
        self._url_base = url_base.rstrip("/")
        self._timeout = timeout

    async def verifier(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
                reponse = await client.get(f"{self._url_base}/health")
                return reponse.status_code == 200
        except Exception:
            return False

    async def reclasser(self, question: str, textes: list[str]) -> list[tuple[int, float]]:
        """Scores de pertinence (index, score), triés du plus pertinent.

        Retourne une paire par texte fourni, dans l'ordre de pertinence
        décroissante.
        """
        if not textes:
            return []
        async with httpx.AsyncClient(timeout=self._timeout, trust_env=False) as client:
            reponse = await client.post(
                f"{self._url_base}/rerank",
                json={
                    "query": question,
                    "texts": [t[:CARACTERES_SCORING] for t in textes],
                    "raw_scores": False,
                },
            )
        if reponse.status_code != 200:
            raise ErreurRerank(f"reranker : HTTP {reponse.status_code}")
        try:
            resultats = reponse.json()
        except ValueError as erreur:
            raise ErreurRerank("reranker : charge invalide") from erreur
        if not isinstance(resultats, list) or len(resultats) != len(textes):
            raise ErreurRerank("reranker : nombre de résultats inattendu")
        paires = [(int(r["index"]), float(r["score"])) for r in resultats]
        return sorted(paires, key=lambda paire: paire[1], reverse=True)


def appliquer_reclassement(candidats: list[dict], paires: list[tuple[int, float]]) -> list[dict]:
    """Réordonne les candidats selon les paires (index, score) du reranker —
    la fonction pure de l'étape 2 (testée sans infrastructure)."""
    reordonnes: list[dict] = []
    for index, score in paires:
        if 0 <= index < len(candidats):
            unite = dict(candidats[index])
            unite["score"] = score
            reordonnes.append(unite)
    return reordonnes
