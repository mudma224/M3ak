"""Tests de l'extraction des réponses fournisseur (fonction pure).

Couvre les modèles à raisonnement hybride (Gemma 4 : content vide, le
budget étant consommé par le raisonnement) et les blocs <think> enbalisés.
"""

from __future__ import annotations

from app.fournisseurs import extraire_reponse


def _corps(content, reasoning=None, modele="google/gemma-4-e4b", jetons=128):
    message = {"content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return {
        "choices": [{"message": message, "finish_reason": "length"}],
        "model": modele,
        "usage": {"prompt_tokens": 30, "completion_tokens": jetons},
    }


def test_content_classique_reste_inchange():
    resultat = extraire_reponse(_corps("Réponse normale."), "x")
    assert resultat.texte == "Réponse normale."
    assert resultat.modele == "google/gemma-4-e4b"
    assert resultat.jetons_sortie == 128


def test_gemma_hybride_content_vide_replie_sur_le_raisonnement():
    corps = _corps("", reasoning="Thinking Process:\n1. analyser la question.")
    resultat = extraire_reponse(corps, "x")
    assert resultat.texte.startswith("Thinking Process:")
    assert resultat.texte != ""


def test_bloc_think_enbalise_est_retire():
    corps = _corps("<think>hmm reflects</think>La réponse utile.")
    resultat = extraire_reponse(corps, "x")
    assert resultat.texte == "La réponse utile."


def test_content_blanc_vaut_content_vide():
    corps = _corps("   ", reasoning="raisonnement de secours")
    resultat = extraire_reponse(corps, "x")
    assert resultat.texte == "raisonnement de secours"


def test_usage_absent_ne_casse_pas():
    corps = {"choices": [{"message": {"content": "ok"}}], "model": "m"}
    resultat = extraire_reponse(corps, "m")
    assert resultat.texte == "ok"
    assert resultat.jetons_entree is None
