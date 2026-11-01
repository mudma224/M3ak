"""Fixtures de test du service whatsapp — sans infrastructure (unitaires)."""

from __future__ import annotations

import os

# Environnement de test AVANT tout import de l'application.
os.environ.setdefault("WHATSAPP_INTERNAL_TOKEN", "jeton-test")
os.environ.setdefault("WHATSAPP_DATABASE_URL", "")

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client() -> TestClient:
    # Sans gestionnaire de contexte : la durée de vie (base, courtier)
    # n'est PAS exécutée — tests purement unitaires.
    return TestClient(create_app())
