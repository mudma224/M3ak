"""Tests du pipeline d'ingestion (phase 1) — base et bus factices."""

from __future__ import annotations

import asyncio

import pytest

from app import base, pipeline
from app.pipeline import DepotDocument, ingest_document
from app.stockage import StockageDossier

TEXTE_AVEC_ARTICLES = (
    "ROYAUME DU MAROC\nLoi de test\nArticle premier\n"
    + "Premier article de la loi de test. " * 8
    + "\n"
    + "".join(
        f"Article {n}\nTexte de l'article {n} de la loi de test. " * 6 + "\n"
        for n in range(2, 12)
    )
)


class BusFactice:
    def __init__(self) -> None:
        self.publiees: list[tuple[str, object]] = []

    async def publier(self, cle_routage: str, enveloppe) -> None:
        self.publiees.append((cle_routage, enveloppe))


@pytest.fixture()
def preparations(monkeypatch, tmp_path):
    """Base factice + stockage dossier + bus capturant les publications."""
    enregistrees: list[base.DocumentNormalise] = []

    async def carte_documents() -> dict[str, str]:
        return {fiche.document_id: fiche.empreinte_sha256 for fiche in enregistrees}

    async def enregistrer_documents(fiches: list[base.DocumentNormalise]) -> int:
        enregistrees.extend(fiches)
        return len(fiches)

    monkeypatch.setattr(base, "carte_documents", carte_documents)
    monkeypatch.setattr(base, "enregistrer_documents", enregistrer_documents)
    return {"enregistrees": enregistrees, "stockage": StockageDossier(tmp_path / "brut")}


def _ingester(preparations, bus, contenu: bytes, reference: str, titre: str):
    return asyncio.run(
        ingest_document(
            contenu,
            DepotDocument(reference=reference, titre=titre),
            bus,
            preparations["stockage"],
        )
    )


def test_ingestion_classe_a_publie_les_segments(preparations, monkeypatch):
    monkeypatch.setattr(pipeline, "extraire_texte_pdf", lambda contenu: (TEXTE_AVEC_ARTICLES, 3))
    bus = BusFactice()
    bilan = _ingester(preparations, bus, b"contenu-pdf", "1-00-1", "Loi de test")
    assert bilan.statut == "insere"
    assert bilan.classe_qualite == "A"
    assert bilan.nb_articles == 11
    assert len(bus.publiees) == 1
    cle_routage, enveloppe = bus.publiees[0]
    assert cle_routage == "segments.to_index"
    assert enveloppe.emis_par == "corpus"
    assert len(enveloppe.charge.segments) == 11
    assert enveloppe.charge.segments[0].segment_id.startswith("1-00-1|fr|originale#art-")


def test_ingestion_pdf_illisible_part_en_quarantaine(preparations):
    bus = BusFactice()
    bilan = _ingester(preparations, bus, b"pas-un-pdf", "2-00-2", "Texte illisible")
    assert bilan.statut == "insere_quarantaine"
    assert bilan.classe_qualite == "C"
    assert bilan.nb_messages_publies == 0
    assert bus.publiees == []
    assert preparations["enregistrees"][0].en_quarantaine is True


def test_ingestion_idempotente(preparations, monkeypatch):
    monkeypatch.setattr(pipeline, "extraire_texte_pdf", lambda contenu: (TEXTE_AVEC_ARTICLES, 3))
    bus = BusFactice()
    premier = _ingester(preparations, bus, b"contenu-pdf", "1-00-1", "Loi de test")
    second = _ingester(preparations, bus, b"contenu-pdf", "1-00-1", "Loi de test")
    assert premier.statut == "insere"
    assert second.statut == "deja_present"
    assert second.nb_messages_publies == 0
    assert len(bus.publiees) == 1  # pas de nouvelle publication


def test_version_differente_refusee(preparations, monkeypatch):
    monkeypatch.setattr(pipeline, "extraire_texte_pdf", lambda contenu: (TEXTE_AVEC_ARTICLES, 3))
    bus = BusFactice()
    _ingester(preparations, bus, b"contenu-pdf", "1-00-1", "Loi de test")
    bilan = _ingester(preparations, bus, b"contenu-modifie", "1-00-1", "Loi de test")
    assert bilan.statut == "version_differente"
    assert len(bus.publiees) == 1
    assert len(preparations["enregistrees"]) == 1


def test_deduplication_inter_sources(preparations, monkeypatch):
    """Même contenu, autre clé (autre référence) : rien à faire."""
    monkeypatch.setattr(pipeline, "extraire_texte_pdf", lambda contenu: (TEXTE_AVEC_ARTICLES, 3))
    bus = BusFactice()
    _ingester(preparations, bus, b"contenu-pdf", "1-00-1", "Loi")
    bilan = _ingester(preparations, bus, b"contenu-pdf", "9-99-9", "Autre source")
    assert bilan.statut == "deja_present"
    assert len(preparations["enregistrees"]) == 1


def test_langue_detectee_gouverne_l_identifiant(preparations, monkeypatch):
    """Un texte arabe déclaré « fr » au dépôt : la clé d'identifiant stable
    porte la langue détectée du contenu (ar), pas la déclarée."""
    texte_arabe = "المملكة المغربية\nالمادة ١\nنص عربي أول للمقال.\nالمادة ٢\nنص عربي ثانٍ للمقال.\n"
    monkeypatch.setattr(
        pipeline, "extraire_texte_pdf", lambda contenu: (texte_arabe * 20, 2)
    )
    bus = BusFactice()
    depot = DepotDocument(reference="1-22-1", titre="نص عربي", langue_indiquee="fr")
    bilan = asyncio.run(ingest_document(b"contenu", depot, bus, preparations["stockage"]))
    assert bilan.document_id.endswith("|ar|originale")
    if bilan.nb_messages_publies:
        assert bus.publiees[0][1].charge.segments[0].langue.value == "ar"


def test_langue_indecidable_retombe_sur_l_indication_puis_le_titre(preparations):
    """Scan arabe dont l'extraction ne donne que des folios latins : la
    détection est indécidable → titre arabe, puis indication de la source,
    puis « inconnue » — jamais un « fr » par défaut mensonger."""
    from app.pipeline import decider_langue

    assert decider_langue("123 456 789", "قرار وزاري", "ar") == "ar"
    assert decider_langue("123 456 789", "قرار وزاري", None) == "ar"
    assert decider_langue("123 456 789", "Note circulaire", None) == "inconnue"
    assert decider_langue("Texte latin assez long pour decider clairement. " * 3, "x", "ar") == "fr"


def test_titre_arabe_prime_sur_une_detection_aveuglee():
    """Régression des lois de finances 2006-2009 : PDF à polices arabes
    cassées — l'extraction délivre des codes de glyphes latins parasites
    (0 lettre arabe, des « lettres » latines par milliers, densité de vrai
    texte) qui font détecter « fr ». Le titre arabe est le seul signal
    honnête : il prime sur la détection ET sur l'annonce « fr » d'Adala."""
    from app.pipeline import decider_langue

    glyphes_parasites = "R:¹ ÏRĕ »R8 a=R==5dC± E\n" * 9000
    assert decider_langue(glyphes_parasites, "قانون المالية لسنة 2007", "fr", pages=178) == "ar"
    # un titre non arabe ne change rien : la détection reste maîtresse
    assert decider_langue(glyphes_parasites, "Loi de finances", "fr", pages=178) == "fr"


def test_detection_exige_une_densite_de_vraie_page():
    """Les en-têtes d'un long scan accumulent des centaines de lettres
    latines : sans garde de densité par page, un scan arabe de 200 pages
    serait « détecté » français. Mesuré : vrai texte ≥ 243 car./page,
    junk de scan ≤ 60 — le seuil 100 sépare les deux régimes. Titre non
    arabe et sans indication pour isoler la garde elle-même."""
    from app.pipeline import decider_langue

    entetes = "ROYAUME DU MAROC\nMINISTERE DE LA JUSTICE\n" * 40
    # accumulé sur 200 pages : lettres nombreuses mais densité nulle → indécidable
    assert decider_langue(entetes, "Décision", None, pages=200) == "inconnue"
    # l'annonce de la source prend alors le relais
    assert decider_langue(entetes, "Décision", "ar", pages=200) == "ar"
    # la même masse sur une seule page de vrai texte → détection latine fiable
    assert decider_langue(entetes, "x", "ar", pages=1) == "fr"


def test_ingester_sans_langue_n_impose_rien(client, monkeypatch):
    """Le champ langue de l'endpoint n'a pas de défaut « fr » : absent ou
    vide, il ne déclare rien (la langue est détectée ou déduite du titre).
    Régression du bug réel : les scans arabes de circulaires étiquetés
    « fr » parce qu'un défaut de formulaire parlait à leur place."""
    from types import SimpleNamespace

    from app import main

    parametres = SimpleNamespace(database_url="postgresql://factice")
    monkeypatch.setattr(main, "get_settings", lambda: parametres)
    captures: list[DepotDocument] = []

    async def _ingest_factice(contenu, depot, bus, stockage):
        captures.append(depot)
        return pipeline.BilanIngestion("x|ar|originale", "e" * 64, "insere")

    monkeypatch.setattr(main, "ingest_document", _ingest_factice)
    client.app.state.bus = object()
    client.app.state.stockage = object()
    entetes = {"X-Internal-Token": "jeton-test"}

    def _depot(**donnees):
        reponse = client.post(
            "/internal/documents/ingester",
            headers=entetes,
            files={"fichier": ("t.pdf", b"%PDF-x", "application/pdf")},
            data=donnees,
        )
        assert reponse.status_code == 200, reponse.text
        return captures[-1]

    assert _depot(reference="1", titre="مراسلات").langue_indiquee is None
    assert _depot(reference="2", titre="مراسلات", langue="").langue_indiquee is None
    assert _depot(reference="3", titre="مراسلات", langue="ar").langue_indiquee == "ar"


def test_scan_arabe_etiquete_ar_meme_en_quarantaine(preparations):
    bus = BusFactice()
    depot = DepotDocument(reference="9-99-9", titre="منشور وزاري", langue_indiquee="ar")
    bilan = asyncio.run(
        ingest_document(b"pas-un-pdf", depot, bus, preparations["stockage"])
    )
    assert bilan.statut == "insere_quarantaine"
    assert bilan.document_id.endswith("|ar|originale")


def test_endpoints_sans_base_503(client):
    reponse = client.get("/internal/documents/stats", headers={"X-Internal-Token": "jeton-test"})
    assert reponse.status_code == 503
    reponse = client.post(
        "/internal/documents/classement/reorganiser", headers={"X-Internal-Token": "jeton-test"}
    )
    assert reponse.status_code == 503


def test_stockage_dossier_copier_et_supprimer_prefixe(tmp_path):
    from app.stockage import StockageDossier

    stockage = StockageDossier(tmp_path)
    stockage.deposer("adala/aaa.pdf", b"%PDF-1")
    stockage.deposer("adala/bbb.pdf", b"%PDF-2")
    stockage.copier("adala/aaa.pdf", "classe/ar/A-fiable/aaa.pdf")
    assert stockage.lire("classe/ar/A-fiable/aaa.pdf") == b"%PDF-1"
    supprimes = stockage.supprimer_prefixe("classe/")
    assert supprimes == 1
    assert stockage.lire("adala/aaa.pdf") == b"%PDF-1"  # la brute ne bouge pas
    assert not (tmp_path / "classe").exists()
