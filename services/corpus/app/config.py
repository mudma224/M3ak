"""Configuration du service corpus — préfixe d'environnement CORPUS_."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Toute la configuration arrive par variables d'environnement préfixées.

    Aucun secret n'est écrit dans le code : les valeurs sensibles vivent
    dans le fichier infra/.env (voir infra/.env.example).
    """

    model_config = SettingsConfigDict(env_prefix="CORPUS_", extra="ignore")

    service_name: str = "corpus"
    port: int = 8001
    database_url: str | None = None  # injectée par compose ; None = sans base
    internal_token: str = "dev-jeton-a-remplacer"
    rabbitmq_url: str | None = "amqp://m3ak:m3ak@rabbitmq:5672/%2F"
    log_level: str = "INFO"
    environment: str = "development"

    # Couche brute : minio (production) ou dossier (tests/développement).
    storage_backend: str = "dossier"
    storage_dossier: str = "stockage_brut"
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "m3ak-corpus-brut"

    # API interne du service index (tableau de bord : volumes d'indexation).
    index_service_url: str = "http://recherche:8002"



@lru_cache
def get_settings() -> Settings:
    return Settings()
