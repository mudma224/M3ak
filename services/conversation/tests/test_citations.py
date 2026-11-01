"""Tests de la vérification mécanique des citations."""

from __future__ import annotations

from app.citations import verifier_citations

PASSAGES = {
    1: "L'article 3 prévoit un délai de trente jours.",
    2: "Le licenciement doit être motivé.",
}


def test_renvois_valides_conserves():
    v = verifier_citations("Le délai est de trente jours [1] et le motif est requis [2].", PASSAGES)
    assert v.valide
    assert v.ids_valides == {1, 2}
    assert v.ids_rejetes == set()
    assert "[1]" in v.texte and "[2]" in v.texte


def test_renvoi_inexistant_rejete_du_texte():
    v = verifier_citations("Le délai est de trente jours [1]. Voir aussi [7].", PASSAGES)
    assert v.valide
    assert v.ids_rejetes == {7}
    assert "[7]" not in v.texte


def test_aucun_renvoi_rend_la_reponse_invalide():
    v = verifier_citations("Réponse sans aucune source.", PASSAGES)
    assert not v.valide
    assert v.motif is not None


def test_citation_verbatim_trouvee_meme_coupee_par_des_sauts():
    v = verifier_citations("Le texte dit : «L'article 3 prévoit un délai\nde trente jours.» [1]", PASSAGES)
    assert v.valide


def test_citation_verbatim_hallucinee_est_un_echec():
    v = verifier_citations("«Disposition entièrement inventée» [1].", PASSAGES)
    assert not v.valide
    assert "introuvable" in (v.motif or "")


def test_citation_verbatim_hors_passage_cite_est_un_echec():
    # La citation vient du passage 2 mais n'est rattachée qu'au passage 1.
    v = verifier_citations("«Le licenciement doit être motivé» [1].", PASSAGES)
    assert not v.valide


def test_verbatim_tolere_les_variantes_typographiques():
    # Le libellé cité doit être exact ; la graphie (apostrophe courbe,
    # tiret long, points de suspension) ne fait pas l'hallucination.
    passages = {
        1: "L'appel est formé dans le délai de trente jours - délai franc... sauf exceptions."
    }
    texte = (
        "L\u2019appel est formé \u00ab dans le délai de trente jours \u2014 délai "
        "franc\u2026 sauf exceptions \u00bb [1]."
    )
    v = verifier_citations(texte, passages)
    assert v.valide


def test_verbatim_arabe_tolere_diacritiques_et_formes_de_lettres():
    # Règles actées phase 2 (unification arabe du plein texte) appliquées
    # au verbatim : diacritiques/tatweel retirés, alef/hamza unifiés. Le
    # passage est diacritisé ; la citation du modèle est en écriture nue
    # (dérive typique des modèles génératifs).
    passages = {
        1: "يُجرى التصريحُ بإبرامِ عقدِ الشغلِ لدى الصندوقِ الوطني."
    }
    texte = "Le texte prévoit que «يجرى التصريح بابرام عقد الشغل لدى الصندوق الوطني» [1]."
    v = verifier_citations(texte, passages)
    assert v.valide

def test_couverture_chaque_passage_cite_doit_porter_sa_citation():
    """Mode SANS_REPONSE : une description se cite — chaque passage cité
    doit porter au moins une citation retrouvée EN LUI, sinon la prose
    libre sur le corpus passerait la vérification."""
    passages = {
        1: "L'appel est formé dans le délai de trente jours.",
        2: "Le licenciement doit être motivé par une cause réelle et sérieuse.",
    }
    # Passage 2 cité mais décrit sans citation : invalide.
    texte = "Le passage [1] porte sur «le délai de trente jours» [1] et le passage [2] traite du licenciement."
    v = verifier_citations(texte, passages, exiger_couverture=True)
    assert not v.valide and "passage [2]" in v.motif
    # Sans le mode couverture (réponse normale), la même prose est valide.
    assert verifier_citations(texte, passages).valide
    # Chaque passage cité porte sa citation : valide.
    texte_complet = (
        "Le délai : «le délai de trente jours» [1]. Le fond : "
        "«une cause réelle et sérieuse» [2]."
    )
    v = verifier_citations(texte_complet, passages, exiger_couverture=True)
    assert v.valide


def test_couverture_une_citation_partagee_par_deux_passages_identiques_suffit():
    passages = {
        1: "Disposition commune à plusieurs textes.",
        2: "Disposition commune à plusieurs textes.",
    }
    texte = "Les deux textes portent «Disposition commune à plusieurs textes» [1] [2]."
    v = verifier_citations(texte, passages, exiger_couverture=True)
    assert v.valide
