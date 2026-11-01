"""Tests du tableau de bord de volumétrie et de qualité (phase 1)."""

from __future__ import annotations

from types import SimpleNamespace

from app import base, main


def test_volumes_index_degradation_gracieuse(monkeypatch):
    """Service index injoignable : les volumes passent à None, la page
    reste produite (timeout court, jamais d'exception propagée)."""
    import asyncio

    parametres = SimpleNamespace(
        internal_token="jeton", index_service_url="http://127.0.0.1:1"
    )
    resultats = asyncio.run(main._volumes_index(parametres))
    assert resultats == {"segments": None, "fiches": None, "decisions": None}


def test_page_rendue_avec_volumes_indisponibles():
    page = main._rendre_tableau_de_bord(
        {"total_documents": 2, "total_articles": 10, "en_quarantaine": 1,
         "par_classe": {"A": 1, "B": 0, "C": 1}},
        [
            {"source": "adala", "langue": "ar", "classe": "A",
             "en_quarantaine": False, "documents": 1, "articles": 10},
            {"source": "idarati", "langue": "ar", "classe": "C",
             "en_quarantaine": True, "documents": 1, "articles": 0},
        ],
        [{"id": 7, "source": "idarati", "methode": "x", "date_debut": "2026-09-26T09:00:00",
          "volume": {"insere": 1}}],
        {"segments": None, "fiches": 1034, "decisions": 4396},
    )
    assert "M3ak — tableau de bord du corpus" in page
    assert "<b>2</b> documents" in page
    assert "adala" in page and "idarati" in page
    assert "indisponible" in page  # dégradation gracieuse affichée
    assert "1034" in page and "4396" in page
    # la fiche en quarantaine apparaît bien dans la répartition des classes
    assert "C : 1" in page


def test_page_echappe_le_contenu_source():
    page = main._rendre_tableau_de_bord(
        {"total_documents": 0, "total_articles": 0, "en_quarantaine": 0,
         "par_classe": {"A": 0, "B": 0, "C": 0}},
        [{"source": "<script>x</script>", "langue": "ar", "classe": "A",
          "en_quarantaine": False, "documents": 0, "articles": 0}],
        [],
        {},
    )
    assert "<script>x</script>" not in page
    assert "&lt;script&gt;" in page


def test_endpoint_tableau_de_bord(client, monkeypatch):
    """Endpoint complet avec base et index factices."""
    from app import main as module_main

    parametres = SimpleNamespace(
        database_url="postgresql://factice", internal_token="jeton",
        index_service_url="http://index:8002",
    )
    monkeypatch.setattr(module_main, "get_settings", lambda: parametres)

    async def stats_documents():
        return {"total_documents": 1, "par_classe": {"A": 1, "B": 0, "C": 0},
                "en_quarantaine": 0, "total_articles": 5}

    async def repartition_documents():
        return [{"source": "adala", "langue": "fr", "classe": "A",
                 "en_quarantaine": False, "documents": 1, "articles": 5}]

    async def dernieres_collectes(limite: int = 5):
        return [{"id": 1, "source": "adala", "methode": "m",
                 "date_debut": "2026-09-18T18:46:41", "volume": {"insere": 1}}]

    async def volumes_index(parametres_):
        return {"segments": 100, "fiches": None, "decisions": None}

    monkeypatch.setattr(base, "stats_documents", stats_documents)
    monkeypatch.setattr(base, "repartition_documents", repartition_documents)
    monkeypatch.setattr(base, "dernieres_collectes", dernieres_collectes)
    monkeypatch.setattr(module_main, "_volumes_index", volumes_index)

    reponse = client.get("/tableau-de-bord")
    assert reponse.status_code == 200
    assert "text/html" in reponse.headers["content-type"]
    assert "<b>1</b> documents" in reponse.text
    assert "100" in reponse.text
