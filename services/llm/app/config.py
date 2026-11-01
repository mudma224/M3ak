"""Configuration du service llm — préfixe d'environnement LLM_."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Toute la configuration arrive par variables d'environnement préfixées.

    Aucun secret n'est écrit dans le code : les valeurs sensibles vivent
    dans le fichier infra/.env (voir infra/.env.example).
    """

    model_config = SettingsConfigDict(env_prefix="LLM_", extra="ignore")

    service_name: str = "llm"
    port: int = 8005
    database_url: str | None = None  # injectée par compose ; None = sans base
    internal_token: str = "dev-jeton-a-remplacer"
    rabbitmq_url: str | None = "amqp://m3ak:m3ak@rabbitmq:5672/%2F"
    log_level: str = "INFO"
    environment: str = "development"

    # ── Fournisseurs de génération (décision actée : commutables par config) ──
    # Trois instances du même client OpenAI-compatible : Z.ai (GLM), Google
    # AI Studio (couche OpenAI de Gemini) et serveur local (LM Studio en
    # dev ; Ollama/llama.cpp à l'hébergement). Basculer = changer
    # LLM_FOURNISSEUR_DEFAUT — la clé vit dans infra/.env, jamais en code.
    # Abonnement GLM Coding Plan : LLM_ZAI_URL pointe alors vers
    # https://api.z.ai/api/coding/paas/v4 (sinon l'API PaaS v4 standard).
    fournisseur_defaut: str = "zai"
    zai_url: str = "https://api.z.ai/api/paas/v4"
    zai_api_key: str | None = None
    zai_modele: str = "glm-5.3-flash"
    google_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    google_api_key: str | None = None
    google_modele: str = "gemini-2.5-flash"
    # Serveur local compatible OpenAI. Modèle vide = auto-détection
    # (premier de /models).
    local_url: str = "http://host.docker.internal:1234/v1"
    local_api_key: str | None = None
    local_modele: str = ""
    local_concurrence: int = 1  # LM Studio traite une génération à la fois
    # Gap de lecture maximal vers le fournisseur. En local sur CPU, le
    # traitement du prompt (2 900 jetons mesurés) précède le premier
    # morceau de plusieurs minutes : 120 s coupait la génération avant
    # même la réflexion (client déconnecté côté LM Studio).
    delai_fournisseur_secondes: float = 300.0

    # ── Quota global plateforme (fenêtres glissantes) ──
    redis_url: str | None = None
    quota_requetes_minute: int = 20
    quota_jetons_jour: int = 500_000
    attente_max_secondes: float = 120.0
    pas_attente_secondes: float = 5.0



@lru_cache
def get_settings() -> Settings:
    return Settings()
