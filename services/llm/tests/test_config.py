"""Tests de configuration — le mapping environnement doit être vérifié
(régression : LLM_FOURNISSEUR au lieu de LLM_FOURNISSEUR_DEFAUT)."""

from __future__ import annotations

from app import config


def test_fournisseur_defaut_lit_son_environnement(monkeypatch):
    monkeypatch.setenv("LLM_FOURNISSEUR_DEFAUT", "local")
    config.get_settings.cache_clear()
    try:
        assert config.get_settings().fournisseur_defaut == "local"
    finally:
        config.get_settings.cache_clear()


def test_valeurs_par_defaut():
    config.get_settings.cache_clear()
    try:
        settings = config.get_settings()
        assert settings.fournisseur_defaut == "zai"
        assert settings.zai_modele == "glm-5.3-flash"
        assert settings.local_concurrence == 1
        assert settings.quota_requetes_minute > 0
    finally:
        config.get_settings.cache_clear()
