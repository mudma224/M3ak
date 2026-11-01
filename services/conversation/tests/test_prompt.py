"""Tests de l'assemblage du prompt — délimiteurs stricts, langue, échapatoire."""

from __future__ import annotations

from app.prompt import assembler_messages

PASSAGES = [(1, "Code des obligations", "adala", "Texte de l'article 3."),]


def test_structure_et_delimiteurs():
    messages = assembler_messages("Quel est le délai ?", PASSAGES, "fr")
    assert len(messages) == 2
    assert messages[0].role == "system"
    assert messages[1].role == "user"
    assert '<passage id="1" titre="Code des obligations" source="adala">' in messages[1].contenu
    assert "</passage>" in messages[1].contenu
    assert "Question : Quel est le délai ?" in messages[1].contenu


def test_echapatoire_et_regles_citations_dans_les_deux_langues():
    for langue in ("fr", "ar"):
        messages = assembler_messages("q", PASSAGES, langue)
        assert "INSUFFISANT" in messages[0].contenu
        assert "[n]" in messages[0].contenu
