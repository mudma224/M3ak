"""Consultation de la couche brute par identifiant stable (interface web).

Le navigateur ne voit jamais le stockage : l'endpoint est sous jeton
interne et seul le proxy du frontend le convoque.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import base
from app.main import create_app

ENTETES = {"X-Internal-Token": "jeton-test"}


class StockageFactice:
    def __init__(self, contenu: bytes = b"%PDF-1.4 test", echec: bool = False):
        self.contenu = contenu
        self.echec = echec
        self.cles: list[str] = []

    def lire(self, cle: str) -> bytes:
        self.cles.append(cle)
        if self.echec:
            raise FileNotFoundError(cle)
        return self.contenu


def _client(monkeypatch, fiche, stockage) -> TestClient:
    async def lire_document(document_id: str):
        return fiche

    monkeypatch.setattr(base, "lire_document", lire_document)
    application = create_app()
    application.state.stockage = stockage
    return TestClient(application)


def test_pdf_par_document_id(monkeypatch):
    fiche = {
        "empreinte_sha256": "a" * 64,
        "chemin_brut": "minio://m3ak-corpus-brut/adala/1-04-22-fr.pdf",
        "source": "adala",
        "en_quarantaine": False,
    }
    stockage = StockageFactice()
    client = _client(monkeypatch, fiche, stockage)
    reponse = client.get("/internal/documents/doc-1/pdf", headers=ENTETES)
    assert reponse.status_code == 200
    assert reponse.headers["content-type"] == "application/pdf"
    assert reponse.content.startswith(b"%PDF")
    # L'URI minio:// du chemin stocké a été réduite à la clé seule.
    assert stockage.cles == ["adala/1-04-22-fr.pdf"]


def test_pdf_protege_par_jeton(monkeypatch):
    client = _client(monkeypatch, None, StockageFactice())
    assert client.get("/internal/documents/doc-1/pdf").status_code == 401


def test_pdf_document_inconnu(monkeypatch):
    client = _client(monkeypatch, None, StockageFactice())
    reponse = client.get("/internal/documents/inconnu/pdf", headers=ENTETES)
    assert reponse.status_code == 404


def test_pdf_refuse_quarantaine_et_non_pdf(monkeypatch):
    quarantaine = {
        "empreinte_sha256": "a" * 64,
        "chemin_brut": "adala/x.pdf",
        "source": "adala",
        "en_quarantaine": True,
    }
    client = _client(monkeypatch, quarantaine, StockageFactice())
    assert client.get("/internal/documents/doc-1/pdf", headers=ENTETES).status_code == 404

    fiche_json = {
        "empreinte_sha256": "b" * 64,
        "chemin_brut": "idarati/abc.json",
        "source": "idarati",
        "en_quarantaine": False,
    }
    client = _client(monkeypatch, fiche_json, StockageFactice())
    assert client.get("/internal/documents/doc-2/pdf", headers=ENTETES).status_code == 404


def test_pdf_brut_introuvable(monkeypatch):
    fiche = {
        "empreinte_sha256": "c" * 64,
        "chemin_brut": "adala/manquant.pdf",
        "source": "adala",
        "en_quarantaine": False,
    }
    client = _client(monkeypatch, fiche, StockageFactice(echec=True))
    reponse = client.get("/internal/documents/doc-3/pdf", headers=ENTETES)
    assert reponse.status_code == 404
