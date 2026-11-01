"""Tests du découpage des lots d'embeddings (budget mémoire du serveur)."""

from __future__ import annotations

from app.embeddings import (
    BUDGET_CARACTERES_LOT,
    MAX_CARACTERES,
    TAILLE_LOT,
    decouper_par_budget,
)


def test_ordre_et_completude():
    textes = [f"t{i}" * 10 for i in range(50)]
    groupes = decouper_par_budget(textes)
    aplati = [t for groupe in groupes for t in groupe]
    assert aplati == textes


def test_taille_des_groupes_bornee():
    textes = ["x" * 100] * (TAILLE_LOT * 2 + 3)
    groupes = decouper_par_budget(textes)
    assert all(len(g) <= TAILLE_LOT for g in groupes)


def test_budget_caracteres_respecte():
    textes = ["x" * 9000] * 10  # tronqués à MAX_CARACTERES (8 000)
    groupes = decouper_par_budget(textes)
    # Invariant central : aucun groupe ne dépasse le budget de caractères,
    # chaque ajout étant vérifié avant insertion.
    assert all(sum(len(t) for t in g) <= BUDGET_CARACTERES_LOT for g in groupes)
    assert all(len(g) <= TAILLE_LOT for g in groupes)
    # Trois textes tronqués de 8 000 (24 000) remplissent exactement un groupe.
    assert any(len(g) == 3 for g in groupes)


def test_troncature_au_garde_fou():
    textes = ["y" * (MAX_CARACTERES + 5000), "court"]
    groupes = decouper_par_budget(textes)
    aplati = [t for groupe in groupes for t in groupe]
    assert len(aplati[0]) == MAX_CARACTERES
    assert aplati[1] == "court"


def test_listes_degeneres():
    assert decouper_par_budget([]) == []
    assert decouper_par_budget(["un texte"]) == [["un texte"]]
