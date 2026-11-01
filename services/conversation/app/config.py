"""Configuration du service conversation — préfixe d'environnement CONVERSATION_."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Toute la configuration arrive par variables d'environnement préfixées.

    Aucun secret n'est écrit dans le code : les valeurs sensibles vivent
    dans le fichier infra/.env (voir infra/.env.example).
    """

    model_config = SettingsConfigDict(env_prefix="CONVERSATION_", extra="ignore")

    service_name: str = "conversation"
    port: int = 8003
    database_url: str | None = None  # injectée par compose ; None = sans base
    internal_token: str = "dev-jeton-a-remplacer"
    rabbitmq_url: str | None = "amqp://m3ak:m3ak@rabbitmq:5672/%2F"
    log_level: str = "INFO"
    environment: str = "development"
    url_recherche: str = "http://recherche:8002"
    url_llm: str = "http://llm:8005"
    # Seuil d'appel à la génération : en dessous, les passages sont trop
    # lointains pour justifier un appel LLM (refus score_insuffisant sans
    # génération). Au-dessus, la génération est TOUJOURS tentée : soit les
    # passages permettent de répondre (vérification mécanique), soit le
    # modèle produit sa description structurée des passages consultés
    # (SANS_REPONSE, vérifiée avec couverture de citations) — décision du
    # porteur (2026-10-07), qui remplace l'ancien mur de refus à 0,40 :
    # le jugement de réponse possible revient au modèle vu les passages
    # complets, pas au score du reranker (les paires interlingues scorent
    # plus bas à qualité égale).
    seuil_appel_generation: float = 0.375
    nb_sources_prompt: int = 8
    max_caracteres_passage: int = 2000
    # Premier appel à froid (embeddings + rerank 40×800 car) peut dépasser
    # 30 s sur la machine de développement chargée.
    delai_recherche_secondes: float = 60.0
    # Génération consommée en FLUX (pas de timeout de lecture) : la borne
    # couvre la génération locale CPU — Gemma ~5,7 jetons/s, prompt long
    # et réflexion comprises (mesuré : > 3 min sur un prompt de 8 passages).
    delai_llm_secondes: float = 600.0
    # Réflexion + réponse : GLM 5.3 (gros modèle « thinking ») consomme
    # jusqu'à ~2 500 jetons de raisonnement AVANT de rédiger — à 2 560 la
    # réponse était tronquée (mesuré 2026-10-05). 4 096 laisse réflexion et
    # réponse se terminer ; une troncature résiduelle déclenche de toute
    # façon une régénération à budget doublé (orchestrateur).
    max_jetons_generation: int = 4096


@lru_cache
def get_settings() -> Settings:
    return Settings()
