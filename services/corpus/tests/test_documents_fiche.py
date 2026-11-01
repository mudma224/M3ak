"""Consultation de la fiche Idarati STRUCTURÉE + ses pièces requises.

Même discipline que le PDF : jeton interne, seul le proxy du frontend
convoque l'endpoint, quarantaine et sources hors Idarati exclues. Les
pièces vivent sous la clé dérivée ``.pieces.json`` — leur absence
(fiches collectées avant l'enrichissement) n'est pas une erreur.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app import base
from app.main import create_app

ENTETES = {"X-Internal-Token": "jeton-test"}

FICHE_BRUTE = {
    "id": "e0dbe9d5-0000-0000-0000-000000000000",
    "title": "الاعتراف الإداري",
    "price": "بالمجان",
    "delay": "60 يوما",
}
PIECES_BRUTES = [
    {
        "title": "البطاقة الوطنية للتعريف",
        "section": {"sort": 0, "title": "الوثائق المطلوبة"},
        "sort": 1,
    }
]


class StockageFactice:
    """Renvoie le contenu attendu par clé ; une clé absente lève."""

    def __init__(self, objets: dict[str, bytes]):
        self.objets = objets
        self.cles: list[str] = []

    def lire(self, cle: str) -> bytes:
        self.cles.append(cle)
        if cle not in self.objets:
            raise FileNotFoundError(cle)
        return self.objets[cle]


def _client(monkeypatch, fiche, stockage) -> TestClient:
    async def lire_document(document_id: str):
        return fiche

    monkeypatch.setattr(base, "lire_document", lire_document)
    application = create_app()
    application.state.stockage = stockage
    return TestClient(application)


def test_fiche_et_pieces_serties(monkeypatch):
    fiche = {
        "titre": "الاعتراف الإداري",
        "empreinte_sha256": "a" * 64,
        "chemin_brut": "minio://m3ak-corpus-brut/idarati/" + "a" * 64 + ".json",
        "source": "idarati",
        "en_quarantaine": False,
    }
    stockage = StockageFactice(
        {
            "idarati/" + "a" * 64 + ".json": json.dumps(FICHE_BRUTE).encode(),
            "idarati/" + "a" * 64 + ".pieces.json": json.dumps(PIECES_BRUTES).encode(),
        }
    )
    client = _client(monkeypatch, fiche, stockage)
    reponse = client.get("/internal/documents/doc-1/fiche", headers=ENTETES)
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["titre"] == "الاعتراف الإداري"
    assert corps["fiche"]["title"] == "الاعتراف الإداري"
    assert corps["pieces"][0]["section"]["title"] == "الوثائق المطلوبة"
    # La clé des pièces est dérivée de celle de la fiche.
    assert stockage.cles == [
        "idarati/" + "a" * 64 + ".json",
        "idarati/" + "a" * 64 + ".pieces.json",
    ]


def test_fiche_sans_pieces_collectees_n_est_pas_une_erreur(monkeypatch):
    fiche = {
        "titre": "fiche-sans-pieces",
        "empreinte_sha256": "b" * 64,
        "chemin_brut": "minio://m3ak-corpus-brut/idarati/" + "b" * 64 + ".json",
        "source": "idarati",
        "en_quarantaine": False,
    }
    stockage = StockageFactice(
        {"idarati/" + "b" * 64 + ".json": json.dumps(FICHE_BRUTE).encode()}
    )
    client = _client(monkeypatch, fiche, stockage)
    reponse = client.get("/internal/documents/doc-1/fiche", headers=ENTETES)
    assert reponse.status_code == 200
    assert reponse.json()["pieces"] is None


def test_fiche_protegee_par_jeton(monkeypatch):
    client = _client(monkeypatch, None, StockageFactice({}))
    assert client.get("/internal/documents/doc-1/fiche").status_code == 401


def test_fiche_inconnue_hors_idarati_ou_quarantaine(monkeypatch):
    client = _client(monkeypatch, None, StockageFactice({}))
    assert client.get("/internal/documents/inconnu/fiche", headers=ENTETES).status_code == 404

    pdf = {
        "empreinte_sha256": "c" * 64,
        "chemin_brut": "minio://m3ak-corpus-brut/adala/x.pdf",
        "source": "adala",
        "en_quarantaine": False,
    }
    client = _client(monkeypatch, pdf, StockageFactice({}))
    assert client.get("/internal/documents/doc-1/fiche", headers=ENTETES).status_code == 404

    quarantaine = {
        "empreinte_sha256": "d" * 64,
        "chemin_brut": "idarati/" + "d" * 64 + ".json",
        "source": "idarati",
        "en_quarantaine": True,
    }
    client = _client(monkeypatch, quarantaine, StockageFactice({}))
    assert client.get("/internal/documents/doc-1/fiche", headers=ENTETES).status_code == 404
