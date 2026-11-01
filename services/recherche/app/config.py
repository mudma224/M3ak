"""Configuration du service recherche — préfixe d'environnement RECHERCHE_."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Toute la configuration arrive par variables d'environnement préfixées.

    Aucun secret n'est écrit dans le code : les valeurs sensibles vivent
    dans le fichier infra/.env (voir infra/.env.example).
    """

    model_config = SettingsConfigDict(env_prefix="RECHERCHE_", extra="ignore")

    service_name: str = "recherche"
    port: int = 8002
    database_url: str | None = None  # injectée par compose ; None = sans base
    internal_token: str = "dev-jeton-a-remplacer"
    rabbitmq_url: str | None = "amqp://m3ak:m3ak@rabbitmq:5672/%2F"
    tei_url: str = "http://tei-embeddings:80"  # serveur d'embeddings (infrastructure)
    reranker_url: str = "http://tei-reranker:80"  # reranker croisé (infrastructure)
    redis_url: str | None = None  # cache des recherches ; None = sans cache
    log_level: str = "INFO"
    environment: str = "development"



@lru_cache
def get_settings() -> Settings:
    return Settings()
