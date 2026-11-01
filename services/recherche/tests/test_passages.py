"""Tests du découpage des décisions de justice en passages."""

from __future__ import annotations

from m3ak_contracts import DecisionAIndexer

from app.passages import FENETRE_MOTS, SEUIL_FRAGMENT, decouper_en_fenetres, generer_passages


def _decision(**surcharge) -> DecisionAIndexer:
    base = {
        "decision_id": "jp-42",
        "document_id": "jp-42|ar|originale",
        "titre": "Décision n° 1234 du 2024-03-15",
        "langue": "ar",
        "thematiques": ["procedure-civile"],
        "juridiction": "Cour d'appel de Casablanca",
        "ville": "Casablanca",
        "date_decision": "2024-03-15",
        "resume_francais": "Pourvoi concernant un bail commercial.",
    }
    base.update(surcharge)
    return DecisionAIndexer(**base)


def test_texte_court_un_seul_passage():
    fenetres = decouper_en_fenetres(" ".join(["mot"] * 100))
    assert fenetres == [" ".join(["mot"] * 100)]


def test_fenetres_avec_recouvrement():
    mots = [f"m{i}" for i in range(FENETRE_MOTS * 3)]
    fenetres = decouper_en_fenetres(" ".join(mots))
    assert len(fenetres) >= 3
    # Le début de la deuxième fenêtre est un chevauchement de la première.
    fin_premiere = fenetres[0].split()[-1]
    assert fin_premiere in fenetres[1].split()
    # Aucun mot perdu : la dernière fenêtre atteint le dernier mot.
    assert fenetres[-1].split()[-1] == f"m{FENETRE_MOTS * 3 - 1}"


def test_fragment_final_fusionne():
    # FENETRE_MOTS + 30 mots : le fragment final (< SEUIL_FRAGMENT) est
    # fusionné au passage précédent, jamais laissé en passage minuscule.
    mots = [f"m{i}" for i in range(FENETRE_MOTS + 30)]
    fenetres = decouper_en_fenetres(" ".join(mots))
    assert len(fenetres) == 1 or len(fenetres[-1].split()) >= SEUIL_FRAGMENT


def test_resume_francais_premier_puis_texte_arabe():
    decision = _decision(
        resume_francais="Résumé du pourvoi.",
        texte_integral_arabe="مضمون الحكم كاملا هنا",
    )
    passages = generer_passages(decision)
    assert len(passages) == 2
    assert passages[0].langue == "fr"
    assert passages[0].type_contenu == "resume"
    assert passages[0].passage_id == "jp-42#p0"
    assert passages[1].langue == "ar"
    assert passages[1].type_contenu == "texte"
    assert passages[1].passage_id == "jp-42#p1"


def test_contexte_decision_porte_par_chaque_passage():
    decision = _decision(texte_integral_arabe="نص الحكم")
    passages = generer_passages(decision)
    for passage in passages:
        assert passage.contexte["decision_id"] == "jp-42"
        assert passage.contexte["juridiction"] == "Cour d'appel de Casablanca"
        assert passage.contexte["date_decision"] == "2024-03-15"


def test_decision_sans_contenu_textuel_liste_vide():
    assert generer_passages(_decision(resume_francais=None)) == []


def test_decision_sans_texte_integral_replie_sur_le_resume_arabe():
    decision = _decision(resume_francais=None, resume_arabe="خلاصة الحكم")
    passages = generer_passages(decision)
    assert len(passages) == 1
    assert passages[0].langue == "ar"
    assert passages[0].type_contenu == "resume"
