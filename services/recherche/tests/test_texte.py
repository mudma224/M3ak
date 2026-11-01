"""Tests de la normalisation plein texte (arabe/français)."""

from __future__ import annotations

from app.texte import normaliser


def test_supprime_diacritiques_et_tatweel():
    # "المادةَ" avec fatha + "الــموادــة" avec tatweel -> formes lisses
    assert normaliser("المادةَ") == "الماده"
    assert normaliser("الـــقـــانون") == normaliser("القانون")


def test_unifie_formes_alef_hamza_et_maqsura():
    assert normaliser("أخوه إبراهيم") == normaliser("اخوه ابراهيم")
    assert normaliser("على") == normaliser("علي")
    assert normaliser("آمر") == normaliser("امر")


def test_ta_marbuta_assimilee_au_ha():
    assert normaliser("الدولة") == normaliser("الدوله")


def test_texte_francais_inchange():
    texte = "Le droit du travail, article 42-1 (version consolidée)."
    assert normaliser(texte) == texte


def test_texte_mixte():
    # La partie arabe est normalisée, la partie latine reste intacte.
    resultat = normaliser("Loi n° 1-59-413 : المادةَ الأولى")
    assert "Loi n° 1-59-413 :" in resultat
    assert "الماده" in resultat
