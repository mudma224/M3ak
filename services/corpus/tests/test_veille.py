"""Tests de la première veille — scans factices, aucun accès réseau."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import httpx

from app import base
from app.connecteurs import adala, idarati, jurisprudence
from app.veille import EtatVeille, _decisions_modifiees, differ, executer_veille


def _page_adala(resultats: list[dict], total: int) -> str:
    charge = {"props": {"pageProps": {"searchResult": {"total": total, "data": resultats}}}}
    return (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(charge, ensure_ascii=False)
        + "</script></html>"
    )


def _entree_adala(reference: str) -> dict:
    return {
        "type": "PDF", "language": "AR", "name": f"Texte {reference}",
        "path": f"uploads/{reference}.pdf", "fileMeta": {"lawNumber": reference},
    }


def _transports(monkeypatch) -> None:
    """Injecte des clients factices pour les trois connecteurs."""

    def client_adala() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200, text=_page_adala([_entree_adala("A"), _entree_adala("B")], 2)
                )
            ),
            base_url=f"https://{adala.HOTE}",
        )

    def client_idarati() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200,
                    json={"content": [{"id": "f-1", "title": "Fiche", "thematicId": "t"}],
                          "totalElements": 1, "last": True},
                )
            ),
            base_url=f"https://{idarati.HOTE}",
        )

    def client_jurisprudence() -> httpx.AsyncClient:
        decisions = [
            {"id": 1, "modified": "2026-09-20T10:00:00"},
            {"id": 2, "modified": "2026-09-26T13:00:00"},
            {"id": 4, "modified": "2026-09-26T13:00:00"},
        ]

        def gerer(requete: httpx.Request) -> httpx.Response:
            page = int(requete.url.params["page"])
            if page == 1:
                return httpx.Response(200, json=decisions)
            return httpx.Response(400, json={"code": "rest_post_invalid_page_number"})

        return httpx.AsyncClient(
            transport=httpx.MockTransport(gerer), base_url=f"https://{jurisprudence.HOTE}"
        )

    monkeypatch.setattr(adala, "_client", client_adala)
    monkeypatch.setattr(idarati, "_client", client_idarati)
    monkeypatch.setattr(jurisprudence, "_client", client_jurisprudence)


def test_differ_nouveaux_disparus():
    diff = differ({"A", "C"}, {"A", "B"})
    assert diff["nouveaux"] == ["B"]
    assert diff["disparus"] == ["C"]
    assert diff["connus"] == 1


def test_decisions_modifiees_compare_les_horodatages():
    ingestion = {
        "jp-1": datetime(2026, 9, 26, 12, 0, tzinfo=UTC),
        "jp-2": datetime(2026, 9, 26, 12, 0, tzinfo=UTC),
    }
    source = {
        "jp-1": "2026-09-20T10:00:00",  # anterieure a l'ingestion : inchangée
        "jp-2": "2026-09-26T13:00:00",  # posterieure : modifiée
        "jp-3": "",                      # sans date : ignoree
    }
    assert _decisions_modifiees(source, ingestion) == ["jp-2"]


def test_veille_produit_le_rapport_et_le_manifeste(monkeypatch):
    _transports(monkeypatch)
    monkeypatch.setattr(
        base, "references_par_source",
        _fabrique_references({
            "adala": {"A": datetime(2026, 9, 20, tzinfo=UTC),
                      "C": datetime(2026, 9, 20, tzinfo=UTC)},
            "idarati": {"f-1": datetime(2026, 9, 26, 10, 0, tzinfo=UTC)},
            "jurisprudence_ma": {
                "jp-1": datetime(2026, 9, 26, 12, 0, tzinfo=UTC),
                "jp-2": datetime(2026, 9, 26, 12, 0, tzinfo=UTC),
            },
        }),
    )
    manifestes: list[base.ManifesteCollecte] = []

    async def enregistrer_manifestes(traces):
        manifestes.extend(traces)

    monkeypatch.setattr(base, "enregistrer_manifestes", enregistrer_manifestes)

    async def scenario() -> dict:
        return await executer_veille(EtatVeille())

    rapport = asyncio.run(scenario())
    assert rapport["adala"] == {"listes": 2, "nouveaux": 1, "disparus": 1, "connus": 1}
    assert rapport["idarati"] == {"listes": 1, "nouveaux": 0, "disparus": 0, "connus": 1}
    assert rapport["jurisprudence"] == {
        "listes": 3, "nouveaux": 1, "disparus": 0, "connus": 2, "modifies": 1,
    }
    assert len(manifestes) == 1
    assert manifestes[0].source == "veille"
    types = {(e["source"], e["type"]) for e in manifestes[0].entrees}
    assert ("adala", "nouveau") in types and ("adala", "disparu") in types
    assert ("jurisprudence", "modifie") in types and ("jurisprudence", "nouveau") in types


def _fabrique_references(donnees: dict[str, dict]):
    async def references_par_source(source: str):
        return donnees[source]

    return references_par_source
