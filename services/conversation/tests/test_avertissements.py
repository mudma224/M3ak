"""Tests des avertissements contextuels."""

from __future__ import annotations

from app.avertissements import detecter_avertissements


def test_famille_francais():
    a = detecter_avertissements("Comment se déroule une procédure de divorce au Maroc ?", "fr")
    assert len(a) == 1
    assert "avocat" in a[0]


def test_travail_arabe():
    a = detecter_avertissements("تم فصلي من العمل دون سبب، ما هي حقوقي؟", "ar")
    assert len(a) == 1
    assert "مفتشية العمل" in a[0]


def test_argent_francais():
    a = detecter_avertissements("Comment récupérer un crédit impayé ?", "fr")
    assert len(a) == 1


def test_question_neutre():
    assert detecter_avertissements("Comment créer une association au Maroc ?", "fr") == []
