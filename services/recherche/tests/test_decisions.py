"""Tests de l'endpoint de volumétrie des décisions de justice."""

from __future__ import annotations

ENTETE_JETON = {"X-Internal-Token": "jeton-test"}


def test_compte_decisions_sans_base_503(client):
    """Sans base configurée, le service refuse honnêtement (503)."""
    reponse = client.get("/internal/decisions/compte", headers=ENTETE_JETON)
    assert reponse.status_code == 503
    assert "base non configuree" in reponse.json()["detail"]


def test_compte_decisions_protege_par_jeton(client):
    reponse = client.get("/internal/decisions/compte")
    assert reponse.status_code == 401
