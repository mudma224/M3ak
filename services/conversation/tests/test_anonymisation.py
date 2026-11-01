"""Tests de l'anonymisation — placeholders, sans re-substitution."""

from __future__ import annotations

from app.anonymisation import anonymiser


def test_presentation_francaise_masquee():
    texte, n = anonymiser("Je m'appelle Ahmed, quel est le délai de recours ?")
    assert "Ahmed" not in texte
    assert "[PERSONNE_1]" in texte
    assert n == 1


def test_presentation_arabe_masquee():
    texte, n = anonymiser("اسمي محمد، ما هي حقوقي بعد الفصل؟")
    assert "محمد" not in texte
    assert n == 1


def test_titre_monsieur_masque():
    texte, n = anonymiser("M. Benali a-t-il droit à ses indemnités ?")
    assert "Benali" not in texte
    assert n == 1


def test_titre_arabe_masque():
    texte, n = anonymiser("السيد العلوي تقدم بشكوى، ما هي الإجراءات؟")
    assert "العلوي" not in texte
    assert n == 1


def test_question_sans_nom_intacte():
    texte, n = anonymiser("Quelles sont les conditions du divorce ?")
    assert texte == "Quelles sont les conditions du divorce ?"
    assert n == 0


def test_plusieurs_noms_numerotes():
    texte, n = anonymiser("Je m'appelle Ali. Le témoin est M. Omar.")
    assert "[PERSONNE_1]" in texte and "[PERSONNE_2]" in texte
    assert n == 2


def test_limite_v1_seule_la_presentation_est_masquee():
    # Le détecteur heuristique V1 ne voit que les formules de présentation
    # et les titres ; un nom cité en cours de phrase passe — un NER local
    # prendra le relais en phase 5.
    texte, n = anonymiser("Mon frère Hassan a été convoqué.")
    assert "Hassan" in texte
    assert n == 0
