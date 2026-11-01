"""Tests des contrats partagés : exemples valides, rejets attendus, clés canoniques."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from m3ak_contracts import (
    ChampsTexteNormatif,
    ChargeDecisionsAIndexer,
    ChargeFichesAIndexer,
    ChargeSegmentsAIndexer,
    Collection,
    ContexteHierarchique,
    DecisionAIndexer,
    DocumentTexteNormatif,
    EmpreinteContenu,
    FicheAIndexer,
    HorodatageCollecte,
    IdentifiantStable,
    Langue,
    MessageDecisionsAIndexer,
    MessageFichesAIndexer,
    MessageSegmentsAIndexer,
    SegmentArticle,
    TypeTexte,
    envelopper,
)


def _texte_normatif_exemple() -> DocumentTexteNormatif:
    return DocumentTexteNormatif(
        identifiant=IdentifiantStable(
            numero_officiel="loi-49-16", langue=Langue.FRANCAIS, version="originale"
        ),
        collection=Collection.TEXTES_NORMATIFS,
        source="adala",
        titre="Loi n° 49-16 relative aux baux d'immeubles ou de locaux loués à usage commercial",
        langue=Langue.FRANCAIS,
        url_source="https://adala.justice.gov.ma/fr/search?term=49-16",
        chemin_local="corpus/adala/raw/2026/loi-49-16_fr.pdf",
        dates=HorodatageCollecte(
            date_publication=date(2017, 6, 16),
            date_recuperation=datetime(2026, 9, 17, tzinfo=UTC),
        ),
        empreinte=EmpreinteContenu(valeur="a" * 64),
        specifics=ChampsTexteNormatif(
            type_texte=TypeTexte.LOI,
            numero_loi="49-16",
            theme="Matière civile",
            version_consolidee=False,
            mots_cles=["baux", "commercial"],
        ),
    )


def _segment_exemple() -> SegmentArticle:
    return SegmentArticle(
        segment_id="loi-49-16|fr|originale#art-4",
        document_id="loi-49-16|fr|originale",
        articles=["4"],
        texte="Le bail commercial est régi par les dispositions de la présente loi...",
        langue=Langue.FRANCAIS,
        contexte=ContexteHierarchique(
            texte_identifiant="loi-49-16|fr|originale",
            texte_titre="Loi n° 49-16 relative aux baux commerciaux",
            chapitre="Dispositions générales",
        ),
    )


def test_identifiant_stable_cle_canonique():
    ident = IdentifiantStable(numero_officiel="loi-49-16", langue=Langue.ARABE)
    assert ident.cle() == "loi-49-16|ar|originale"


def test_variantes_partagent_la_meme_base_de_deduplication():
    fr = IdentifiantStable(numero_officiel="loi-49-16", langue=Langue.FRANCAIS)
    ar = IdentifiantStable(numero_officiel="loi-49-16", langue=Langue.ARABE)
    assert fr.numero_officiel == ar.numero_officiel and fr.langue != ar.langue


def test_document_texte_normatif_valide():
    doc = _texte_normatif_exemple()
    assert doc.identifiant.cle() == "loi-49-16|fr|originale"
    assert doc.statut_juridique.value == "inconnu"  # réservé, non renseigné en V1


def test_document_sans_empreinte_rejete():
    donnees = _texte_normatif_exemple().model_dump()
    del donnees["empreinte"]
    with pytest.raises(ValidationError):
        DocumentTexteNormatif.model_validate(donnees)


def test_segment_sans_articles_rejete():
    donnees = _segment_exemple().model_dump()
    donnees["articles"] = []
    with pytest.raises(ValidationError):
        SegmentArticle.model_validate(donnees)


def test_enveloppe_messages_a_indexer_et_reconstruction():
    charge = ChargeSegmentsAIndexer(segments=[_segment_exemple()])
    enveloppe = envelopper(charge, correlation_id="corr-123", emis_par="corpus")
    assert enveloppe.type_message == "segments_a_indexer"
    assert enveloppe.correlation_id == "corr-123"
    assert enveloppe.schema_version == "1.0"
    assert isinstance(enveloppe, MessageSegmentsAIndexer)

    # Sérialisation JSON -> reconstruction du bon type d'enveloppe
    reconstruite = MessageSegmentsAIndexer.model_validate_json(enveloppe.model_dump_json())
    assert reconstruite.charge.segments[0].segment_id == "loi-49-16|fr|originale#art-4"
    assert reconstruite.correlation_id == "corr-123"


def _fiche_exemple() -> FicheAIndexer:
    return FicheAIndexer(
        fiche_id="f-1|ar|originale",
        document_id="f-1|ar|originale",
        titre="إيداع عقد الزواج المدني بالمصالح القنصلية",
        langue=Langue.ARABE,
        texte="إيداع عقد الزواج المدني — على الفور — بالمجان",
        thematique_id="them-1",
        administration="وزارة الشؤون الخارجية",
    )


def test_fiche_sans_texte_rejete():
    donnees = _fiche_exemple().model_dump()
    donnees["texte"] = ""
    with pytest.raises(ValidationError):
        FicheAIndexer.model_validate(donnees)


def test_enveloppe_fiches_a_indexer_et_reconstruction():
    charge = ChargeFichesAIndexer(fiches=[_fiche_exemple()])
    enveloppe = envelopper(charge, correlation_id="corr-f1", emis_par="corpus")
    assert enveloppe.type_message == "fiches_a_indexer"
    assert isinstance(enveloppe, MessageFichesAIndexer)

    reconstruite = MessageFichesAIndexer.model_validate_json(enveloppe.model_dump_json())
    assert reconstruite.charge.fiches[0].fiche_id == "f-1|ar|originale"
    assert reconstruite.charge.fiches[0].langue == Langue.ARABE


def _decision_exemple() -> DecisionAIndexer:
    return DecisionAIndexer(
        decision_id="jp-83650|ar|originale",
        document_id="jp-83650|ar|originale",
        titre="Élections du conseil de l'ordre : application immédiate de la loi nouvelle",
        langue=Langue.ARABE,
        thematiques=["profession-davocat", "batonnier-et-conseil-de-lordre"],
        juridiction="Cour d'appel",
        ville="Rabat",
        type_decision="Arrêt",
        numero_decision="353",
        date_decision="1983-01-31",
        resume_francais="Le recours en annulation des opérations électorales…",
        texte_integral_arabe="إن حق الطعن في العمليات الانتخابية…",
    )


def test_decision_bilingue_reconstruction():
    charge = ChargeDecisionsAIndexer(decisions=[_decision_exemple()])
    enveloppe = envelopper(charge, correlation_id="corr-d1", emis_par="corpus")
    assert enveloppe.type_message == "decisions_a_indexer"
    assert isinstance(enveloppe, MessageDecisionsAIndexer)

    reconstruite = MessageDecisionsAIndexer.model_validate_json(enveloppe.model_dump_json())
    decision = reconstruite.charge.decisions[0]
    assert decision.decision_id == "jp-83650|ar|originale"
    # les deux langues restent portées par des champs distincts
    assert decision.resume_francais.startswith("Le recours")
    assert decision.texte_integral_arabe.startswith("إن حق")


def test_decision_sans_titre_rejete():
    donnees = _decision_exemple().model_dump()
    donnees["titre"] = ""
    with pytest.raises(ValidationError):
        DecisionAIndexer.model_validate(donnees)
