"""Tests unitaires communs : santé, corrélation, jeton interne."""

from __future__ import annotations

ENTETE_JETON = {"X-Internal-Token": "jeton-test"}


def test_sante_ok(client):
    reponse = client.get("/health")
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["status"] == "ok"
    assert corps["service"] == "conversation"


def test_correlation_fourni_est_renvoye(client):
    reponse = client.get("/health", headers={"X-Correlation-ID": "abc-123"})
    assert reponse.headers["X-Correlation-ID"] == "abc-123"


def test_correlation_genere_si_absent(client):
    reponse = client.get("/health")
    assert reponse.headers.get("X-Correlation-ID")


def test_echo_sans_jeton_refuse(client):
    assert client.get("/internal/echo").status_code == 401


def test_pret_sans_jeton_refuse(client):
    assert client.get("/ready").status_code == 401


def test_echo_avec_jeton_et_correlation(client):
    reponse = client.get(
        "/internal/echo", headers={**ENTETE_JETON, "X-Correlation-ID": "corr-42"}
    )
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["service"] == "conversation"
    assert corps["correlation_id"] == "corr-42"


def test_erreur_porte_le_correlation_id(client):
    reponse = client.get("/internal/echo", headers={"X-Correlation-ID": "corr-err"})
    assert reponse.status_code == 401
    assert reponse.json()["correlation_id"] == "corr-err"
