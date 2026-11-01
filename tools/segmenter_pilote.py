"""Harnais du jalon B : segmente les 24 documents du corpus pilote.

Extrait le texte natif de chaque PDF du registre (pypdf), applique la
grammaire de segmentation du service corpus, écrit les segments par document
(pilot/segments/, ignoré par git) et produit le rapport de qualité
(pilot/rapport_segmentation.md, versionné).

Usage :
    python tools/segmenter_pilote.py            # tout le pilote
    python tools/segmenter_pilote.py --cles A1  # un document
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "services" / "corpus"))

from app.segmentation import classer, segmenter

DOSSIER_SEGMENTS = RACINE / "pilot" / "segments"
FICHIER_RAPPORT = RACINE / "pilot" / "rapport_segmentation.md"
FICHIER_REGISTRE = RACINE / "pilot" / "registre.json"


def extraire_texte(fichier: Path) -> tuple[str, int]:
    from pypdf import PdfReader

    lecteur = PdfReader(fichier)
    pages = len(lecteur.pages)
    texte = "\n".join((page.extract_text() or "") for page in lecteur.pages)
    return texte, pages


def principale() -> int:
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--cles", help="sous-ensemble de clés pilotes (A1,B2)")
    arguments = analyseur.parse_args()
    cibles = arguments.cles.split(",") if arguments.cles else None

    registre = json.loads(FICHIER_REGISTRE.read_text(encoding="utf-8"))["documents"]
    documents_tries = sorted(registre.values(), key=lambda e: e["cle_pilote"])
    # Les intitulés complets et les familles vivent dans la liste figée
    # (le registre ne porte que l'état de collecte).
    liste_figee = json.loads((RACINE / "pilot" / "corpus_pilote.json").read_text(encoding="utf-8"))
    intitules = {d["cle"]: d for d in liste_figee["documents"]}

    lignes_rapport: list[str] = []
    synthesis = {"A": 0, "B": 0, "C": 0}
    total_articles = 0

    for entree in documents_tries:
        cle = entree["cle_pilote"]
        if cibles and cle not in cibles:
            continue
        fiche = intitules.get(cle, {})
        intitule = fiche.get("intitule", "?")
        fichier = RACINE / entree["chemin_local"]
        try:
            texte, pages = extraire_texte(fichier)
        except Exception as erreur:  # noqa: BLE001 — consigné dans le rapport
            lignes_rapport.append(
                f"| {cle} | {intitule} | — | — | — | — | — | — | C | "
                f"extraction impossible : {str(erreur)[:60]} |"
            )
            synthesis["C"] += 1
            continue

        resultat = segmenter(texte)
        classe = classer(resultat, caracteres=len(texte), pages=pages)
        synthesis[classe] += 1
        total_articles += len(resultat.articles)

        DOSSIER_SEGMENTS.mkdir(parents=True, exist_ok=True)
        (DOSSIER_SEGMENTS / f"{cle}.json").write_text(
            json.dumps(
                {
                    "cle_pilote": cle,
                    "chemin_local": entree["chemin_local"],
                    "empreinte_sha256": entree["empreinte_sha256"],
                    "pages": pages,
                    "caracteres": len(texte),
                    "langue": resultat.langue,
                    "score_numerotation": round(resultat.score_numerotation, 4),
                    "classe_qualite": classe,
                    "nb_articles": len(resultat.articles),
                    "preambule": resultat.preambule,
                    "articles": resultat.articles,
                    "avertissements": [
                        {"ligne": a.ligne, "message": a.message} for a in resultat.avertissements
                    ],
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )

        # Observations réelles : comptage des avertissements par catégorie.
        messages = [a.message for a in resultat.avertissements]
        categories = (
            ("numéro parasité réparé", "numéros parasités réparés", "parasité"),
            ("série parallèle écartée", "séries parallèles écartées", "écartée"),
            ("saut de numérotation", "sauts de numérotation", "saut de numérotation"),
            ("numéro illisible", "numéros illisibles", "illisible"),
        )
        observations = " ; ".join(
            f"{n} {singulier if n == 1 else pluriel}"
            for singulier, pluriel, marqueur in categories
            if (n := sum(marqueur in m for m in messages))
        ) or "—"
        famille = fiche.get("famille", "?")
        lignes_rapport.append(
            f"| {cle} | {intitule} | {famille} | {pages} | {len(texte)} | "
            f"{resultat.langue} | {len(resultat.articles)} | "
            f"{resultat.score_numerotation:.2f} | {classe} | {observations} |"
        )
        print(f"{cle:4} {classe} {len(resultat.articles):4} articles  score={resultat.score_numerotation:.2f}  "
              f"{len(resultat.avertissements):3} avertissements")

    rapport = [
        "# Rapport de segmentation — corpus pilote (jalon B)",
        "",
        "Extraction texte natif (pypdf) + grammaire de segmentation du service",
        "corpus (`services/corpus/app/segmentation.py`). Classes : **A** fiable,",
        "**B** exploitable mais marqué, **C** quarantaine (jamais indexé en silence).",
        "",
        "## Synthèse",
        "",
        (
            f"- Documents : {len(lignes_rapport)} — classes A/B/C : "
            f"{synthesis['A']}/{synthesis['B']}/{synthesis['C']}"
        ),
        f"- Articles segmentés au total : {total_articles}",
        "",
        "## Détail par document",
        "",
        "| Clé | Intitulé | Famille | Pages | Car. | Langue | Articles | Score num. | Classe | Observations |",
        "|---|---|---|---|---|---|---|---|---|---|",
        *lignes_rapport,
        "",
        "## Constats et limites",
        "",
        "### Ce qui est validé",
        "",
        "- Frontières strictes (« Article N » isolé sur sa ligne) : codes et",
        "  lois modernes segmentés de bout en bout — Constitution 180/180,",
        "  code du travail 589/589, Moudawana 400, code pénal 699, DOC 1 305.",
        "- Frontières collées (« Art. 4. » abrégé des dahirs de 1913,",
        "  « Article 503-1 - … » des lois modificatives) : repli automatique",
        "  quand le document n'a aucune frontière stricte.",
        "- Contexte hiérarchique (livre / chapitre / section) propagé aux",
        "  segments ; titres exclus du texte des articles.",
        "",
        "### Problèmes traités pendant le jalon B (aucun report en phase 1)",
        "",
        "- **Numéros parasités par l'extraction** (folio ou numéro de note",
        "  collé : « Article 19 » lu « 1911 », « 7 » lu « 72 ») : réparés",
        "  quand la suite de la séquence le confirme, avertissement de",
        "  traçabilité (CPC : 568 articles, score 0,99).",
        "- **Séries parallèles** (tables analytiques et titres marginaux lus",
        "  en travers d'une mise en page à colonnes, intercalés dans tout le",
        "  document) : séparées de la série principale par programmation",
        "  dynamique — les frontières parasites sont écartées et n'ont jamais",
        "  produit de segments (C7 : 69 articles propres + 35 déchets",
        "  identifiés ; C1, A6).",
        "- **Sauts de numérotation des versions consolidées** (articles",
        "  abrogés ou fusionnés) : re-synchronisation bornée (≤ 100), chaque",
        "  saut averti (DOC 1 305 articles à 1,00 ; dahirs de 1913 : 459 à",
        "  0,96 ; loi SA annotée : 435 à 0,99).",
        "- **Articles insérés composés** (« 43-1 » à « 43-5 », « 2 bis ») :",
        "  reconnus comme sous-série sans casser la numérotation (loi SNC :",
        "  145 articles à 1,00 ; lois modificatives C5/E4 à 1,00).",
        "",
        "### Ce qui dépend réellement de la phase 1 ou 2",
        "",
        "- **Validation de la grammaire arabe sur corpus réel** : Adala ne",
        "  sert que des versions françaises ; la grammaire AR (المادة، الفصل",
        "  désambiguïsé, ordinaux, chiffres orientaux) est couverte par les",
        "  tests unitaires et attend des textes arabes réels (SGG/BO).",
        "- **Décompte croisé pour les textes annotés** : la loi SA (D1) est",
        "  une édition consolidée à colonnes avec notes de modification ;",
        "  435 articles sont détectés en série continue, mais certains",
        "  articles noyés dans le flux peuvent échapper à toute extraction",
        "  ligne à ligne — à croiser avec d'autres éditions du même texte",
        "  (redondance inter-sources prévue par l'architecture).",
        "- Le sous-découpage des articles longs et le regroupement des",
        "  articles courts sont des décisions d'indexation (phase 2) ; chaque",
        "  segment porte déjà sa longueur pour préparer ce travail.",
        "",
    ]
    if cibles:
        # Un run partiel met à jour les segments mais jamais le rapport :
        # celui-ci reflète l'ensemble du pilote et serait faussé par un
        # sous-ensemble (les fichiers pilot/segments/<cle>.json, eux, sont
        # régénérés document par document).
        print("rapport non régénéré (run partiel) — relancer sans --cles pour le régénérer")
        return 0
    FICHIER_RAPPORT.write_text("\n".join(rapport), encoding="utf-8")
    print(f"rapport : {FICHIER_RAPPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(principale())
