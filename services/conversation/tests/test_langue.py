"""Tests de la détection de langue (langue du dernier message)."""

from __future__ import annotations

from app.langue import detecter_langue


def test_question_arabe():
    assert detecter_langue("ما هي مدة التقادم في القانون المغربي؟") == "ar"


def test_question_francaise():
    assert detecter_langue("Quel est le délai de prescription en droit marocain ?") == "fr"


def test_question_sans_lettres_repli_francais():
    assert detecter_langue("12345 ?!") == "fr"
