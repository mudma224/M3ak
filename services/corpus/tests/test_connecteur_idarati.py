"""Tests du connecteur Idarati — réponses factices, aucun accès réseau."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from m3ak_contracts import MessageFichesAIndexer

from app import base
from app.connecteurs import idarati
from app.stockage import StockageDossier


class BusFactice:
    def __init__(self) -> None:
        self.publiees: list[tuple[str, object]] = []

    async def publier(self, cle_routage: str, enveloppe) -> None:
        self.publiees.append((cle_routage, enveloppe))


def _menu() -> list[dict]:
    return [
        {
            "id": "cat-1",
            "locale": "ar",
            "title": "الأفراد",
            "subCategories": [
                {
                    "id": "sous-1",
                    "title": "المغاربة المقيمون",
                    "thematics": [
                        {"id": "them-1", "locale": "ar", "title": "الأسرة والحالة المدنية"},
                        {"id": "them-2", "locale": "ar", "title": "التعليم والتكوين"},
                    ],
                }
            ],
        }
    ]


def _fiche(identifiant: str, titre: str, description: str) -> dict:
    return {
        "id": identifiant,
        "locale": "ar",
        "title": titre,
        "description": description,
        "delay": "على الفور",
        "price": "بالمجان",
        "hasComplementStep": False,
        "thematicId": "them-1",
        "administrationInCharge": {"id": "adm-1", "title": "وزارة العدل"},
        "receivingAdministrations": [{"id": "adm-2", "title": "قنصليات المملكة"}],
        "downloadableForms": [],
    }


def _recherche(fiches: list[dict], total: int, derniere: bool = True) -> dict:
    return {"content": fiches, "totalElements": total, "last": derniere}


def test_thematiques_aplatit_le_menu():
    def transport(requete: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_menu())

    async def scenario() -> list[dict]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport),
            base_url=f"https://{idarati.HOTE}",
        ) as client:
            return await idarati.thematiques(client)

    aplati = asyncio.run(scenario())
    assert [t["id"] for t in aplati] == ["them-1", "them-2"]
    assert aplati[0]["public"] == "المغاربة المقيمون"
    assert aplati[0]["categorie"] == "الأفراد"


def test_recherche_par_thematique_transmet_les_parametres():
    def transport(requete: httpx.Request) -> httpx.Response:
        assert requete.url.params["thematicId"] == "them-1"
        assert requete.url.params["size"] == "200"
        assert requete.url.params["page"] == "0"
        return httpx.Response(
            200,
            json=_recherche(
                [{"id": "f-1", "title": "إيداع عقد الزواج", "thematicId": "them-1"}], 1
            ),
        )

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport),
            base_url=f"https://{idarati.HOTE}",
        ) as client:
            return await idarati.chercher_par_thematique(client, "them-1", 0)

    resultats, total, derniere = asyncio.run(scenario())
    assert total == 1 and derniere is True
    assert resultats[0].identifiant == "f-1"


def test_recuperer_fiche_exige_le_meme_identifiant():
    def transport(requete: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_fiche("autre-id", "t", "d"))

    async def scenario() -> None:
        resultat = idarati.ResultatIdarati("f-1", "titre", "them-1")
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport),
            base_url=f"https://{idarati.HOTE}",
        ) as client:
            with pytest.raises(ValueError):
                await idarati.recuperer_fiche(client, resultat)

    asyncio.run(scenario())


@pytest.fixture()
def preparations(monkeypatch, tmp_path):
    """Base factice + stockage dossier — même mécanique que test_pipeline."""
    enregistrees: list[base.DocumentNormalise] = []

    async def carte_documents() -> dict[str, str]:
        return {f.document_id: f.empreinte_sha256 for f in enregistrees}

    async def enregistrer_documents(fiches: list[base.DocumentNormalise]) -> int:
        enregistrees.extend(fiches)
        return len(fiches)

    monkeypatch.setattr(base, "carte_documents", carte_documents)
    monkeypatch.setattr(base, "enregistrer_documents", enregistrer_documents)
    return {"enregistrees": enregistrees, "stockage": StockageDossier(tmp_path / "brut")}


def test_ingestion_fiche_classe_a_langue_ar(preparations):
    contenu = json.dumps(_fiche("f-1", "إيداع عقد الزواج المدني", "تسجيل الزواج لدى المصالح"), ensure_ascii=False).encode()
    resultat = idarati.ResultatIdarati("f-1", "إيداع عقد الزواج", "them-1", locale="ar")
    bus = BusFactice()
    statut = asyncio.run(idarati.ingester_fiche(contenu, resultat, bus, preparations["stockage"]))
    assert statut == "insere"
    fiche = preparations["enregistrees"][0]
    assert fiche.document_id.endswith("|ar|originale")
    assert fiche.classe_qualite == "A" and not fiche.en_quarantaine
    assert fiche.nb_articles == 0 and fiche.pages == 1
    assert "idarati/" in fiche.chemin_brut and fiche.chemin_brut.endswith(".json")
    # le JSON brut stocké est identique au contenu reçu
    cle = f"idarati/{fiche.empreinte_sha256}.json"
    assert preparations["stockage"].lire(cle) == contenu
    # publication sur la file dédiée des fiches, une fiche par message
    assert len(bus.publiees) == 1
    cle_routage, enveloppe = bus.publiees[0]
    assert cle_routage == "fiches.to_index"
    assert isinstance(enveloppe, MessageFichesAIndexer)
    fiche_publiee = enveloppe.charge.fiches[0]
    assert fiche_publiee.fiche_id == fiche.document_id
    assert fiche_publiee.langue.value == "ar"
    assert fiche_publiee.thematique_id == "them-1"
    assert "الزواج" in fiche_publiee.texte


def test_re_ingestion_et_doublon_inter_sources(preparations):
    contenu = json.dumps(_fiche("f-1", "إيداع عقد الزواج المدني", "تسجيل الزواج لدى المصالح"), ensure_ascii=False).encode()
    resultat = idarati.ResultatIdarati("f-1", "إيداع عقد الزواج", "them-1", locale="ar")
    bus = BusFactice()
    assert asyncio.run(idarati.ingester_fiche(contenu, resultat, bus, preparations["stockage"])) == "insere"
    # même contenu re-collecté → déjà présent, aucune republication
    assert asyncio.run(idarati.ingester_fiche(contenu, resultat, bus, preparations["stockage"])) == "deja_present"
    # même contenu sous un autre identifiant (autre public) → déjà présent
    autre = idarati.ResultatIdarati("f-2", "إيداع عقد الزواج", "them-2", locale="ar")
    assert asyncio.run(idarati.ingester_fiche(contenu, autre, bus, preparations["stockage"])) == "deja_present"
    assert len(bus.publiees) == 1


def test_version_differente_refusee(preparations):
    premier = json.dumps(_fiche("f-1", "titre", "description d'origine"), ensure_ascii=False).encode()
    resultat = idarati.ResultatIdarati("f-1", "titre", "them-1", locale="ar")
    bus = BusFactice()
    assert asyncio.run(idarati.ingester_fiche(premier, resultat, bus, preparations["stockage"])) == "insere"
    modifie = json.dumps(_fiche("f-1", "titre", "description modifiée"), ensure_ascii=False).encode()
    assert asyncio.run(idarati.ingester_fiche(modifie, resultat, bus, preparations["stockage"])) == "version_differente"
    assert len(bus.publiees) == 1


def test_collecte_dry_run_sans_ingestion(monkeypatch):
    def transport(requete: httpx.Request) -> httpx.Response:
        chemin = requete.url.path
        if chemin.endswith("/search"):
            return httpx.Response(
                200,
                json=_recherche(
                    [{"id": "f-1", "title": "إيداع عقد الزواج", "thematicId": "them-1"}], 1
                ),
            )
        return httpx.Response(200, json=_fiche("f-1", "t", "d"))

    def fabrique() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(transport),
            base_url=f"https://{idarati.HOTE}",
        )

    monkeypatch.setattr(idarati, "_client", fabrique)

    async def scenario() -> dict:
        etat = idarati.EtatCollecte()
        return await idarati.executer_collecte(
            None, None, ["them-1"], limite=5, dry_run=True, etat=etat  # type: ignore[arg-type]
        )

    manifeste = asyncio.run(scenario())
    assert manifeste["volume"] == {"vus": 1}
    assert manifeste["entrees"][0]["reference"] == "f-1"
    assert manifeste["source"] == "idarati"


def test_collecte_ingere_et_consigne_le_manifeste(monkeypatch, preparations):
    def transport(requete: httpx.Request) -> httpx.Response:
        chemin = requete.url.path
        if chemin.endswith("/search"):
            return httpx.Response(
                200,
                json=_recherche(
                    [{"id": "f-1", "title": "إيداع عقد الزواج", "thematicId": "them-1"}], 1
                ),
            )
        return httpx.Response(200, json=_fiche("f-1", "إيداع عقد الزواج", "تسجيل الزواج"))

    def fabrique() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(transport),
            base_url=f"https://{idarati.HOTE}",
        )

    monkeypatch.setattr(idarati, "_client", fabrique)
    manifestes: list[base.ManifesteCollecte] = []

    async def enregistrer_manifestes(traces: list[base.ManifesteCollecte]) -> None:
        manifestes.extend(traces)

    monkeypatch.setattr(base, "enregistrer_manifestes", enregistrer_manifestes)

    async def scenario() -> dict:
        etat = idarati.EtatCollecte()
        return await idarati.executer_collecte(
            BusFactice(), preparations["stockage"], ["them-1"], limite=5, dry_run=False, etat=etat
        )

    manifeste = asyncio.run(scenario())
    assert manifeste["volume"] == {"insere": 1}
    assert len(manifestes) == 1
    assert len(preparations["enregistrees"]) == 1
