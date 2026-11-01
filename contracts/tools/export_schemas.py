"""Export des JSON Schema des contrats — référence inter-langage (frontend, outils).

Usage (environnement conda m3ak) :
    python contracts/tools/export_schemas.py
Écrit un fichier JSON par modèle dans contracts/schemas/.
"""

from __future__ import annotations

import json
from pathlib import Path

from m3ak_contracts.conversation import DemandeQuestion, ReponseQuestion, SourceCitee
from m3ak_contracts.decisions import DecisionAIndexer
from m3ak_contracts.documents import (
    DocumentDecision,
    DocumentDonneeComplementaire,
    DocumentProcedure,
    DocumentTexteNormatif,
)
from m3ak_contracts.fiches import FicheAIndexer
from m3ak_contracts.generation import DemandeGeneration, EvenementGeneration, ReponseGeneration
from m3ak_contracts.http import ReponsePret, ReponseSante
from m3ak_contracts.messages import (
    MessageDecisionsAIndexer,
    MessageFichesAIndexer,
    MessageOcrJob,
    MessageSegmentsAIndexer,
)
from m3ak_contracts.recherche import RequeteRecherche
from m3ak_contracts.segments import SegmentArticle

SORTIE = Path(__file__).resolve().parent.parent / "schemas"

MODELES = [
    DocumentTexteNormatif,
    DocumentProcedure,
    DocumentDecision,
    DocumentDonneeComplementaire,
    SegmentArticle,
    FicheAIndexer,
    DecisionAIndexer,
    MessageSegmentsAIndexer,
    MessageFichesAIndexer,
    MessageDecisionsAIndexer,
    MessageOcrJob,
    RequeteRecherche,
    DemandeQuestion,
    ReponseQuestion,
    SourceCitee,
    DemandeGeneration,
    ReponseGeneration,
    EvenementGeneration,
    ReponseSante,
    ReponsePret,
]


def main() -> None:
    SORTIE.mkdir(exist_ok=True)
    for modele in MODELES:
        schema = modele.model_json_schema()
        chemin = SORTIE / (modele.__name__ + ".schema.json")
        chemin.write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"exporte : {chemin.relative_to(SORTIE.parent.parent)}")
    print(f"{len(MODELES)} schemas exportes dans contracts/schemas/")


if __name__ == "__main__":
    main()
