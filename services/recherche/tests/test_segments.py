"""Tests de l'endpoint de volumétrie de l'index des segments."""

from __future__ import annotations

ENTETE_JETON = {"X-Internal-Token": "jeton-test"}


def test_compte_sans_base_503(client):
    """Sans base configurée, le service refuse honnêtement (503)."""
    reponse = client.get("/internal/segments/compte", headers=ENTETE_JETON)
    assert reponse.status_code == 503
    assert "base non configuree" in reponse.json()["detail"]


def test_compte_protege_par_jeton(client):
    reponse = client.get("/internal/segments/compte")
    assert reponse.status_code == 401
