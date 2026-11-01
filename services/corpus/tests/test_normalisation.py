"""Tests de la normalisation des segments vers le contrat SegmentArticle."""

from __future__ import annotations

from app.normalisation import cle_document, construire_lot

DOCUMENT = {
    "cle": "A1",
    "intitule": "Constitution de 2011",
    "reference_promulgation": "1-11-91",
}

SEGMENTATION = {
    "langue": "fr",
    "articles": [
        {
            "numero_brut": "premier",
            "numero": 1,
            "contexte": {"livre": None, "chapitre": "PREMIER : Dispositions générales", "section": None},
            "texte": "Le Maroc est une monarchie.",
            "longueur": 26,
        },
        {
            "numero_brut": "2 bis",
            "numero": 2,
            "contexte": {"livre": None, "chapitre": "PREMIER : Dispositions générales", "section": "II : Des droits"},
            "texte": "Texte inséré.",
            "longueur": 15,
        },
    ],
}


def test_cle_document_stable():
    assert cle_document("1-11-91", "fr") == "1-11-91|fr|originale"
    assert cle_document("1-11-91", "ar") == "1-11-91|ar|originale"


def test_lot_au_format_contrat():
    lots = construire_lot(DOCUMENT, SEGMENTATION)
    segments = [segment for lot in lots for segment in lot]
    assert len(segments) == 2
    premier = segments[0]
    assert premier.document_id == "1-11-91|fr|originale"
    assert premier.segment_id == "1-11-91|fr|originale#art-premier-0001"
    assert premier.articles == ["premier"]
    assert premier.langue.value == "fr"
    assert premier.contexte.texte_titre == "Constitution de 2011"
    assert premier.contexte.chapitre.startswith("PREMIER")
    assert premier.contexte.section is None


def test_segment_id_idempotent_avec_espaces_et_suffixes():
    lots = construire_lot(DOCUMENT, SEGMENTATION)
    insere = lots[0][1]
    assert insere.segment_id == "1-11-91|fr|originale#art-2-bis-0002"


def test_numeros_repetes_segment_ids_distincts():
    """Un texte avec deux fois « Article 5 » (annexe, reprise partielle) doit
    produire deux segments distincts — la position garantit l'unicité."""
    segmentation = {
        "langue": "fr",
        "articles": [
            {"numero_brut": "5", "numero": 5, "contexte": {}, "texte": "Premier cinq.", "longueur": 12},
            {"numero_brut": "6", "numero": 6, "contexte": {}, "texte": "Six.", "longueur": 4},
            {"numero_brut": "5", "numero": 5, "contexte": {}, "texte": "Second cinq.", "longueur": 12},
        ],
    }
    segments = [segment for lot in construire_lot(DOCUMENT, segmentation) for segment in lot]
    ids = [segment.segment_id for segment in segments]
    assert len(set(ids)) == 3
    assert ids[0].endswith("#art-5-0001")
    assert ids[2].endswith("#art-5-0003")


def test_lots_bornes():
    segmentation = {
        "langue": "fr",
        "articles": [
            {"numero_brut": str(n), "numero": n, "contexte": {}, "texte": f"Texte {n}.", "longueur": 8}
            for n in range(1, 251)
        ],
    }
    lots = construire_lot(DOCUMENT, segmentation, taille_lot=100)
    assert [len(lot) for lot in lots] == [100, 100, 50]


def test_lot_vide():
    assert construire_lot(DOCUMENT, {"langue": "fr", "articles": []}) == []


def test_segments_au_texte_vide_exclus():
    segmentation = {
        "langue": "fr",
        "articles": [
            {"numero_brut": "1", "numero": 1, "contexte": {}, "texte": "Valide.", "longueur": 7},
            {"numero_brut": "2", "numero": 2, "contexte": {}, "texte": "", "longueur": 0},
            {"numero_brut": "3", "numero": 3, "contexte": {}, "texte": "   ", "longueur": 3},
        ],
    }
    lots = construire_lot(DOCUMENT, segmentation)
    segments = [segment for lot in lots for segment in lot]
    assert [segment.articles[0] for segment in segments] == ["1"]
