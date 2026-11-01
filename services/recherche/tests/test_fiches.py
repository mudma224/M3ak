"""Tests de l'endpoint de volumétrie des fiches de procédures."""

from __future__ import annotations

ENTETE_JETON = {"X-Internal-Token": "jeton-test"}


def test_compte_fiches_sans_base_503(client):
    """Sans base configurée, le service refuse honnêtement (503)."""
    reponse = client.get("/internal/fiches/compte", headers=ENTETE_JETON)
    assert reponse.status_code == 503
    assert "base non configuree" in reponse.json()["detail"]


def test_compte_fiches_protege_par_jeton(client):
    reponse = client.get("/internal/fiches/compte")
    assert reponse.status_code == 401
