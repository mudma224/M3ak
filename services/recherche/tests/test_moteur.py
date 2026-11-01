"""Tests de la fusion RRF, de l'entrelacement et du re-classement."""

from __future__ import annotations

from app.moteur import dedupliquer_passages, entrelacer_par_famille, fusion_rrf
from app.rerank import appliquer_reclassement


def _unite(unite_id: str) -> dict:
    return {"unite_id": unite_id, "texte": f"contenu {unite_id}"}


def test_unite_presente_dans_les_deux_listes_cumule_son_score():
    a = fusion_rrf([[_unite("x")], []])
    b = fusion_rrf([[_unite("x")], [_unite("x")]])
    # x est en tête des deux listes dans b : son score double par rapport à a.
    assert b[0]["score"] > a[0]["score"]
    assert b[0]["score"] == 2 * a[0]["score"]


def test_classement_par_rang_et_intersection():
    vec = [_unite("a"), _unite("b"), _unite("c")]
    txt = [_unite("b"), _unite("a"), _unite("d")]
    fusionnes = fusion_rrf([vec, txt])
    identifiants = [u["unite_id"] for u in fusionnes]
    # "a" et "b" (présents dans les deux listes) passent devant "c" et "d".
    assert set(identifiants[:2]) == {"a", "b"}
    assert set(identifiants[2:]) == {"c", "d"}


def test_listes_vides_et_liste_unique():
    assert fusion_rrf([]) == []
    assert fusion_rrf([[], []]) == []
    seuls = fusion_rrf([[_unite("a"), _unite("b")]])
    assert [u["unite_id"] for u in seuls] == ["a", "b"]


def test_score_decroit_avec_le_rang():
    fusionnes = fusion_rrf([[_unite("a"), _unite("b"), _unite("c")]])
    scores = [u["score"] for u in fusionnes]
    assert scores[0] > scores[1] > scores[2]


def test_entrelacement_garantit_la_diversite_des_familles():
    # Une famille « dominante » (ses deux systèmes sont d'accord) ne doit
    # pas évincer les autres du top-k.
    dominante = [{**_unite(f"d{i}"), "famille": "segment"} for i in range(10)]
    faible = [{**_unite(f"f{i}"), "famille": "passage"} for i in range(10)]
    resultat = entrelacer_par_famille({"segment": dominante, "passage": faible}, top_k=6)
    familles = [u["famille"] for u in resultat]
    assert familles.count("segment") == 3
    assert familles.count("passage") == 3
    # L'ordre RRF est conservé au sein de chaque famille.
    identifiants = [u["unite_id"] for u in resultat]
    assert identifiants == ["d0", "f0", "d1", "f1", "d2", "f2"]


def test_entrelacement_cas_limites():
    assert entrelacer_par_famille({}, top_k=5) == []
    courts = {"a": [_unite("a0")], "b": [_unite("b0"), _unite("b1")]}
    resultat = entrelacer_par_famille(courts, top_k=5)
    assert [u["unite_id"] for u in resultat] == ["a0", "b0", "b1"]


def test_reclassement_reordonne_et_porte_le_score():
    candidats = [_unite("a"), _unite("b"), _unite("c")]
    reordonnes = appliquer_reclassement(candidats, [(2, 0.91), (0, 0.42)])
    assert [u["unite_id"] for u in reordonnes] == ["c", "a"]
    assert reordonnes[0]["score"] == 0.91
    assert reordonnes[1]["score"] == 0.42


def test_reclassement_ignore_les_indices_hors_limites():
    candidats = [_unite("a")]
    reordonnes = appliquer_reclassement(candidats, [(0, 0.5), (7, 0.9), (-1, 0.3)])
    assert [u["unite_id"] for u in reordonnes] == ["a"]
    assert reordonnes[0]["score"] == 0.5


def test_reclassement_vide():
    assert appliquer_reclassement([], []) == []


def _passage(unite_id: str, decision_id: str, score: float, resume: bool = False) -> dict:
    type_contenu = "resume" if resume else "texte"
    return {
        "unite_id": unite_id,
        "famille": "passage",
        "document_id": decision_id,
        "score": score,
        "contexte": {"decision_id": decision_id, "type_contenu": type_contenu},
    }


def test_dedup_note_chaque_decision_a_son_maximum():
    passages = [
        _passage("d1#p0", "jp-1|ar|originale", 0.30),
        _passage("d1#p3", "jp-1|ar|originale", 0.85),
        _passage("d2#p0", "jp-2|ar|originale", 0.60),
    ]
    resultat = dedupliquer_passages(passages)
    # jp-1 est représenté par sa meilleure fenêtre ; une seule unité par décision.
    assert [u["unite_id"] for u in resultat] == ["d1#p3", "d2#p0"]
    assert resultat[0]["score"] == 0.85


def test_dedup_prefere_le_resume_en_score_proche():
    passages = [
        _passage("d1#p0", "jp-1|ar|originale", 0.90),
        _passage("d1#resume", "jp-1|ar|originale", 0.88, resume=True),
        _passage("d2#p0", "jp-2|ar|originale", 0.50),
    ]
    resultat = dedupliquer_passages(passages)
    assert resultat[0]["unite_id"] == "d1#resume"  # résumé à 0.88 >= 0.90 - 0.05


def test_dedup_garde_la_meilleure_fenetre_si_le_resume_est_trop_bas():
    passages = [
        _passage("d1#p0", "jp-1|ar|originale", 0.90),
        _passage("d1#resume", "jp-1|ar|originale", 0.40, resume=True),
    ]
    resultat = dedupliquer_passages(passages)
    assert resultat[0]["unite_id"] == "d1#p0"


def test_dedup_ne_touche_pas_les_autres_familles():
    segment = {"unite_id": "s1", "famille": "segment", "score": 0.9, "contexte": {}}
    resultat = dedupliquer_passages([segment, _passage("d1#p0", "jp-1", 0.5)])
    assert len(resultat) == 2
    assert any(u["unite_id"] == "s1" for u in resultat)
