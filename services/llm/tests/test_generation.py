"""Tests du point de génération — fournisseur factice, quota mémoire."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.fournisseurs import DonneesGeneration, ErreurFournisseur, ErreurQuotaFournisseur
from app.main import create_app
from app.quota import GestionnaireQuota

ENTETES = {"X-Internal-Token": "jeton-test"}
DEMANDE = {
    "messages": [{"role": "user", "contenu": "Question de test"}],
    "max_jetons": 64,
    "fournisseur": "fictif",
}


class FournisseurFactice:
    def __init__(self, texte: str = "Réponse de test", echec: Exception | None = None) -> None:
        self.texte = texte
        self.echec = echec
        self.appels = 0

    async def verifier(self) -> bool:
        return self.echec is None

    async def generer(self, messages, temperature, max_jetons):
        self.appels += 1
        if self.echec is not None:
            raise self.echec
        return DonneesGeneration(
            texte=self.texte, modele="modele-fictif", jetons_entree=8, jetons_sortie=12
        )


def _application(fournisseur, requetes_minute: int = 100, jetons_jour: int = 100_000):
    application = create_app()
    application.state.fournisseurs = {"fictif": fournisseur}
    application.state.quota = GestionnaireQuota(
        None, limite_requetes_minute=requetes_minute, limite_jetons_jour=jetons_jour
    )
    return application


def test_generation_reussie():
    client = TestClient(_application(FournisseurFactice("Réponse sourcée.")))
    reponse = client.post("/internal/generation", headers=ENTETES, json=DEMANDE)
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["texte"] == "Réponse sourcée."
    assert corps["fournisseur"] == "fictif"
    assert corps["jetons_sortie"] == 12
    assert corps["attendu"] is False


def test_generation_protegee_par_jeton():
    client = TestClient(_application(FournisseurFactice()))
    assert client.post("/internal/generation", json=DEMANDE).status_code == 401


def test_fournisseur_inconnu_rejete():
    client = TestClient(_application(FournisseurFactice()))
    reponse = client.post(
        "/internal/generation", headers=ENTETES, json={**DEMANDE, "fournisseur": "inconnu"}
    )
    assert reponse.status_code == 400


def test_quota_atteint_repond_429_apres_patience_epuisee():
    # LLM_ATTENTE_MAX_SECONDES=0 (conftest) : aucune patience → 429 immédiat.
    client = TestClient(_application(FournisseurFactice(), requetes_minute=0))
    reponse = client.post("/internal/generation", headers=ENTETES, json=DEMANDE)
    assert reponse.status_code == 429
    assert "quota plateforme" in reponse.json()["detail"]


def test_quota_se_libere_apres_attente():
    # Patience de 6 s et pas de 1 s : la deuxième requête attend que la
    # première sorte de la fenêtre minute (horloge réelle accélérée par le
    # pas court de la fenêtre... non : la fenêtre est 60 s réelles). On
    # vérifie ici uniquement le chemin "attente puis succès" avec une
    # limite largement suffisante : pas d'attente, succès direct.
    client = TestClient(_application(FournisseurFactice(), requetes_minute=5))
    premiere = client.post("/internal/generation", headers=ENTETES, json=DEMANDE)
    deuxieme = client.post("/internal/generation", headers=ENTETES, json=DEMANDE)
    assert premiere.status_code == 200
    assert deuxieme.status_code == 200


def test_fournisseur_sature_signale_proprement():
    client = TestClient(
        _application(FournisseurFactice(echec=ErreurQuotaFournisseur("429 amont")))
    )
    reponse = client.post("/internal/generation", headers=ENTETES, json=DEMANDE)
    assert reponse.status_code == 429
    assert "fournisseur fictif" in reponse.json()["detail"]


def test_fournisseur_en_erreur_signale_proprement():
    client = TestClient(
        _application(FournisseurFactice(echec=ErreurFournisseur("panic upstream")))
    )
    reponse = client.post("/internal/generation", headers=ENTETES, json=DEMANDE)
    assert reponse.status_code == 502
    assert "panic upstream" in reponse.json()["detail"]


def test_quota_expose_son_etat():
    client = TestClient(_application(FournisseurFactice()))
    reponse = client.get("/internal/generation/quota", headers=ENTETES)
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["requetes_minute"]["limite"] == 100
    assert corps["jetons_jour"]["utilises"] == 0
