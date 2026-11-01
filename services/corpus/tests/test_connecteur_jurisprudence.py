"""Tests du connecteur Jurisprudence.ma — réponses factices, aucun accès réseau."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from m3ak_contracts import MessageDecisionsAIndexer

from app import base
from app.connecteurs import jurisprudence
from app.stockage import StockageDossier


class BusFactice:
    def __init__(self) -> None:
        self.publiees: list[tuple[str, object]] = []

    async def publier(self, cle_routage: str, enveloppe) -> None:
        self.publiees.append((cle_routage, enveloppe))


def _decision(id: int, slugs: list[str], resume: str = "", texte_ar: str = "") -> dict:
    classes = [f"mbp_decision_categories-{s}" for s in slugs]
    return {
        "id": id,
        "title": {"rendered": "Élections du conseil de l&#8217;ordre"},
        "modified": "2026-09-16T16:20:29",
        "class_list": classes + [f"post-{id}", "mbp_decision"],
        "acf": {
            "juridiction": "Cour d'appel",
            "ville": "Rabat",
            "type_de_decision": "Arrêt",
            "numero_de_decision": "353",
            "numero_de_dossier": "82/679",
            "date_de_decision": "19830131",
            "themes": 4340,
            "sous_theme": 4406,
            "resume_en_francais": f"<p style=\"text-align: justify;\">{resume}</p>" if resume else "",
            "resume_en_arabe": "",
            "version_arabe_de_la_decision": f"<div dir=\"rtl\">{texte_ar}</div>" if texte_ar else "",
            "version_francaise_de_la_decisin": "",
        },
    }


def _transport(decisions_par_page: list[list[dict]], details: dict[int, dict]):
    """Transport factice : parcours paginé terminé par 400 + détails par id."""

    def transport(requete: httpx.Request) -> httpx.Response:
        chemin = requete.url.path
        if chemin.endswith("/mbp_decision"):
            page = int(requete.url.params["page"])
            if page <= len(decisions_par_page):
                return httpx.Response(200, json=decisions_par_page[page - 1])
            return httpx.Response(400, json={"code": "rest_post_invalid_page_number"})
        if match := __import__("re").fullmatch(r"/wp-json/wp/v2/mbp_decision/(\d+)", chemin):
            identifiant = int(match.group(1))
            if identifiant in details:
                return httpx.Response(200, json=details[identifiant])
        return httpx.Response(404, json={"code": "introuvable"})

    return transport


def test_parcours_s_arrete_sur_400():
    def fabrique() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(
                _transport([[_decision(1, ["civil"])], [_decision(2, ["civil"])]], {})
            ),
            base_url=f"https://{jurisprudence.HOTE}",
        )

    async def scenario() -> list[dict]:
        etat = jurisprudence.EtatCollecte()
        async with fabrique() as client:
            return await jurisprudence.parcourir_references(client, etat)

    references = asyncio.run(scenario())
    assert [r["id"] for r in references] == [1, 2]


def test_selection_par_slugs_avec_limites():
    references = [
        {"id": 1, "class_list": ["mbp_decision_categories-procedure-civile"]},
        {"id": 2, "class_list": ["mbp_decision_categories-procedure-civile"]},
        {"id": 3, "class_list": ["mbp_decision_categories-civil"]},
        {"id": 4, "class_list": ["mbp_decision_categories-procedure-civile", "mbp_decision_categories-civil"]},
    ]
    # borne 2 sur procedure-civile, civil intégral ; l'id 4 (deux slugs visés)
    # n'est retenu qu'une fois (par civil, parcouru après la borne p.civile)
    choisies = jurisprudence.selectionner(references, ["procedure-civile", "civil"], {"procedure-civile": 2})
    assert choisies == [1, 2, 3, 4]
    choisies = jurisprudence.selectionner(references, ["procedure-civile"], {"procedure-civile": 1})
    assert choisies == [1]


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


def test_ingestion_decision_bilingue(preparations):
    detail = _decision(83650, ["profession-davocat"], resume="Le recours en annulation", texte_ar="إن حق الطعن")
    contenu = json.dumps(detail, ensure_ascii=False).encode()
    bus = BusFactice()
    statut = asyncio.run(jurisprudence.ingester_decision(contenu, bus, preparations["stockage"]))
    assert statut == "insere"
    fiche = preparations["enregistrees"][0]
    # la langue du document est celle du texte intégral (ar)
    assert fiche.document_id == "jp-83650|ar|originale"
    assert fiche.source == "jurisprudence_ma" and fiche.classe_qualite == "A"
    assert "jurisprudence/" in fiche.chemin_brut and fiche.chemin_brut.endswith(".json")
    assert preparations["stockage"].lire(f"jurisprudence/{fiche.empreinte_sha256}.json") == contenu
    # publication sur la file dédiée, langues portées par des champs distincts
    cle_routage, enveloppe = bus.publiees[0]
    assert cle_routage == "decisions.to_index"
    assert isinstance(enveloppe, MessageDecisionsAIndexer)
    decision = enveloppe.charge.decisions[0]
    assert decision.decision_id == "jp-83650|ar|originale"
    assert decision.resume_francais == "Le recours en annulation"
    assert decision.texte_integral_arabe == "إن حق الطعن"
    assert decision.thematiques == ["profession-davocat"]
    assert decision.date_decision == "1983-01-31"
    assert decision.juridiction == "Cour d'appel"


def test_ingestion_decision_sans_texte_arabe(preparations):
    detail = _decision(83651, ["civil"], resume="Résumé français seul")
    contenu = json.dumps(detail, ensure_ascii=False).encode()
    bus = BusFactice()
    statut = asyncio.run(jurisprudence.ingester_decision(contenu, bus, preparations["stockage"]))
    assert statut == "insere"
    assert preparations["enregistrees"][0].document_id == "jp-83651|fr|originale"


def test_texte_assaini_sans_caracteres_de_controle():
    """Les caractères de contrôle (NUL en tête) sont retirés du texte
    publié — PostgreSQL refuse les octets NUL dans les champs texte."""
    detail = _decision(83652, ["civil"], resume="R\u00e9sum\u00e9 a\x00b avec contr\u00f4le", texte_ar="\u0646\u0635\x00\u062b\u0627\u0628\u062a")
    charge = jurisprudence.construire_charge(detail)
    assert "\x00" not in (charge["resume_francais"] or "")
    assert "\x00" not in (charge["texte_integral_arabe"] or "")
    assert charge["resume_francais"] == "Résumé ab avec contrôle"
    assert charge["texte_integral_arabe"] == "نصثابت"


def test_re_ingestion_et_version_differente(preparations):
    detail = _decision(83650, ["civil"], resume="A", texte_ar="نص")
    contenu = json.dumps(detail, ensure_ascii=False).encode()
    bus = BusFactice()
    assert asyncio.run(jurisprudence.ingester_decision(contenu, bus, preparations["stockage"])) == "insere"
    assert asyncio.run(jurisprudence.ingester_decision(contenu, bus, preparations["stockage"])) == "deja_present"
    detail["acf"]["resume_en_francais"] = "<p>Modifié</p>"
    modifie = json.dumps(detail, ensure_ascii=False).encode()
    assert asyncio.run(jurisprudence.ingester_decision(modifie, bus, preparations["stockage"])) == "version_differente"
    assert len(bus.publiees) == 1


def test_collecte_dry_run(monkeypatch, preparations):
    page = [_decision(1, ["civil"]), _decision(2, ["baux"]), _decision(3, ["civil"])]
    def fabrique() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(_transport([page], {})),
            base_url=f"https://{jurisprudence.HOTE}",
        )

    monkeypatch.setattr(jurisprudence, "_client", fabrique)

    async def scenario() -> dict:
        etat = jurisprudence.EtatCollecte()
        return await jurisprudence.executer_collecte(
            None, None, ["civil"], {"civil": 1}, True, etat  # type: ignore[arg-type]
        )

    manifeste = asyncio.run(scenario())
    assert manifeste["volume"] == {"vus": 1}


def test_collecte_complete_ingere_et_consigne(monkeypatch, preparations):
    page = [_decision(1, ["civil"]), _decision(2, ["baux"]), _decision(3, ["civil"])]
    details = {
        1: _decision(1, ["civil"], resume="Résumé un", texte_ar="نص أول"),
        3: _decision(3, ["civil"], resume="Résumé trois", texte_ar="نص ثالث"),
    }

    def fabrique() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(_transport([page], details)),
            base_url=f"https://{jurisprudence.HOTE}",
        )

    monkeypatch.setattr(jurisprudence, "_client", fabrique)
    manifestes: list[base.ManifesteCollecte] = []

    async def enregistrer_manifestes(traces: list[base.ManifesteCollecte]) -> None:
        manifestes.extend(traces)

    monkeypatch.setattr(base, "enregistrer_manifestes", enregistrer_manifestes)

    async def scenario() -> dict:
        etat = jurisprudence.EtatCollecte()
        return await jurisprudence.executer_collecte(
            BusFactice(), preparations["stockage"], ["civil"], {}, False, etat
        )

    manifeste = asyncio.run(scenario())
    assert manifeste["volume"] == {"insere": 2}
    assert len(manifestes) == 1
    assert len(preparations["enregistrees"]) == 2
