"""Tests des contrats de recherche (v0.4.0) : requête, résultat, réponse."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from m3ak_contracts import (
    FAMILLES_UNITE,
    SOURCES_PAR_FAMILLE,
    Langue,
    ReponseRecherche,
    RequeteRecherche,
    ResultatRecherche,
)


def test_requete_defauts_et_bornes():
    requete = RequeteRecherche(question="Comment résilier un bail commercial ?")
    assert requete.top_k == 10
    assert requete.familles is None
    assert RequeteRecherche(question="q", top_k=50).top_k == 50
    with pytest.raises(ValidationError):
        RequeteRecherche(question="q", top_k=0)
    with pytest.raises(ValidationError):
        RequeteRecherche(question="q", top_k=51)
    with pytest.raises(ValidationError):
        RequeteRecherche(question="   ")


def test_familles_unite_et_sources_cohérentes():
    assert FAMILLES_UNITE == ("segment", "fiche", "passage")
    assert set(SOURCES_PAR_FAMILLE) == set(FAMILLES_UNITE)
    # Chaque famille porte sa source et sa collection (hiérarchie des sources).
    assert SOURCES_PAR_FAMILLE["segment"] == ("adala", "textes_normatifs")
    assert SOURCES_PAR_FAMILLE["fiche"] == ("idarati", "procedures")
    assert SOURCES_PAR_FAMILLE["passage"] == ("jurisprudence_ma", "jurisprudence")


def test_resultat_complet_valide():
    resultat = ResultatRecherche(
        unite_id="jp-42#p0",
        famille="passage",
        document_id="jp-42|ar|originale",
        titre="Décision n° 1234",
        texte="Pourvoi concernant un bail commercial…",
        langue=Langue.FRANCAIS,
        source="jurisprudence_ma",
        collection="jurisprudence",
        contexte={"decision_id": "jp-42|ar|originale"},
        score=0.0325,
    )
    assert resultat.score > 0


def test_resultat_sans_famille_connue_rejete():
    with pytest.raises(ValidationError):
        ResultatRecherche(
            unite_id="x",
            famille="inconnue",
            document_id="x",
            titre="t",
            texte="t",
            langue=Langue.ARABE,
            source="adala",
            collection="textes_normatifs",
            score=0.0,
        )


def test_reponse_recherche_modes_et_cache():
    reponse = ReponseRecherche(resultats=[], mode="texte", duree_ms=12)
    assert reponse.cache is False
    assert reponse.mode == "texte"
    with pytest.raises(ValidationError):
        ReponseRecherche(resultats=[], mode="impossible")
