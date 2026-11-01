"""Tests du connecteur Adala — pages factices, aucun accès réseau."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.connecteurs import adala


def _page_recherche(resultats: list[dict], total: int | None = None) -> str:
    page_props = {
        "searchResult": {"total": total if total is not None else len(resultats), "data": resultats},
        "themesResult": [{"key": 7, "name": "المادة المدنية", "count": total or 0}],
    }
    charge = {"props": {"pageProps": page_props}}
    return (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(charge, ensure_ascii=False)
        + "</script></html>"
    )


def _entree(law_number: str, nom: str, chemin: str, date: str = "2020-01-01") -> dict:
    return {
        "type": "PDF",
        "language": "FR",
        "name": nom,
        "path": chemin,
        "fileMeta": {"lawNumber": law_number, "gregorianDate": date},
    }


def test_parsing_depuis_page_facticielle():
    html = _page_recherche(
        [
            _entree("1-16-99", "Baux commerciaux", "uploads/2024/baux.pdf"),
            _entree("", "Texte sans numéro", "uploads/2024/sans.pdf"),
        ],
        total=2,
    )
    page_props = adala._page_props(html)
    assert page_props["searchResult"]["total"] == 2


def test_chercher_par_theme_via_transport_factice():
    html = _page_recherche([_entree("1-11-03", "Consommation", "uploads/a.pdf")], total=1)

    def transport(requete: httpx.Request) -> httpx.Response:
        assert requete.url.params["themes"] == "7"
        return httpx.Response(200, text=html)

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport),
            base_url="https://adala.justice.gov.ma",
        ) as client:
            resultats, total = await adala.chercher_par_theme(client, 7, 1)
        assert total == 1
        assert resultats[0].reference == "1-11-03"
        assert resultats[0].url_source == "https://adala.justice.gov.ma/api/uploads/a.pdf"

    asyncio.run(scenario())


def test_choisir_version_prefere_la_consolidee():
    resultats = [
        adala.ResultatAdala("1-96-83", "Code de commerce", "uploads/2024/original.pdf", date_publication="2024-01-01"),
        adala.ResultatAdala("1-96-83", "Code de commerce - version consolidée 06 2026", "uploads/2026/consolidée.pdf", date_publication="2026-06-01"),
    ]
    choisis = adala.choisir_version(resultats)
    assert len(choisis) == 1
    assert "consolidée" in choisis[0].titre


def test_choisir_version_prefere_la_plus_recente_sans_consolidee():
    resultats = [
        adala.ResultatAdala("1-18-19", "Loi", "uploads/2020.pdf", date_publication="2020-05-01"),
        adala.ResultatAdala("1-18-19", "Loi", "uploads/2024.pdf", date_publication="2024-03-01"),
    ]
    choisis = adala.choisir_version(resultats)
    assert len(choisis) == 1
    assert choisis[0].chemin_pdf == "uploads/2024.pdf"


def test_telechargement_refuse_chemin_malveillant():
    chemins_refuses = (
        "../etc/passwd",
        "uploads/../secret.pdf",
        "uploads//x.pdf",
        "C:/x.pdf",
        "uploads\\x.pdf",
    )

    async def scenario(chemin: str) -> None:
        resultat = adala.ResultatAdala("x", "x", chemin)
        async with httpx.AsyncClient(base_url="https://adala.justice.gov.ma") as client:
            with pytest.raises(ValueError):
                await adala.telecharger_pdf(client, resultat)

    for chemin in chemins_refuses:
        asyncio.run(scenario(chemin))


def test_telechargement_accepte_points_legitimes():
    """Des noms de fichiers contenant des points consécutifs (fréquent dans
    les noms arabes tronqués) ne sont pas de la traversée de répertoire."""
    resultat = adala.ResultatAdala("x", "x", "uploads/2024/09/ظهير..بتنفيذ.pdf")

    async def scenario() -> str:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"%PDF-1.7")),
            base_url="https://adala.justice.gov.ma",
        ) as client:
            return (await adala.telecharger_pdf(client, resultat)).decode()

    assert asyncio.run(scenario()) == "%PDF-1.7"


def test_telechargement_exige_un_vrai_pdf():
    resultat = adala.ResultatAdala("1", "Loi", "uploads/page.html")

    def transport(requete: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>pas un pdf</html>")

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport),
            base_url="https://adala.justice.gov.ma",
        ) as client:
            with pytest.raises(ValueError):
                await adala.telecharger_pdf(client, resultat)

    asyncio.run(scenario())


def test_reference_de_repli_unique_sans_numero():
    """Deux textes sans numéro de loi : références distinctes (empreinte du
    chemin), jamais en collision ; un doublon exact de chemin garde la même."""
    premier = adala._resultat_depuis_entree({"path": "uploads/a.pdf", "name": "A", "fileMeta": {}})
    second = adala._resultat_depuis_entree({"path": "uploads/b.pdf", "name": "B", "fileMeta": {}})
    doublon = adala._resultat_depuis_entree({"path": "uploads/a.pdf", "name": "A (copie)", "fileMeta": {}})
    assert premier.reference.startswith("adala-")
    assert premier.reference != second.reference
    assert premier.reference == doublon.reference
    # le choix de version regroupe par référence : les sans-numéro ne se
    # cannonnisent plus, seuls les doublons exacts sont dédupliqués
    choisis = adala.choisir_version([premier, second, doublon])
    assert {r.reference for r in choisis} == {premier.reference, second.reference}


def test_collecte_dry_run_sans_ingestion(monkeypatch):
    html = _page_recherche([_entree("1-11-03", "Consommation", "uploads/a.pdf")], total=1)

    def transport(requete: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    def fabrique() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(transport),
            base_url="https://adala.justice.gov.ma",
        )

    monkeypatch.setattr(adala, "_client", fabrique)

    async def scenario() -> dict:
        etat = adala.EtatCollecte()
        return await adala.executer_collecte(
            None, None, theme=7, limite=5, dry_run=True, etat=etat  # type: ignore[arg-type]
        )

    manifeste = asyncio.run(scenario())
    assert manifeste["volume"] == {"vus": 1}
    assert manifeste["entrees"][0]["reference"] == "1-11-03"
