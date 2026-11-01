"""Ingère le corpus pilote via le VRAI service corpus (phase 1).

Chaque PDF collecté du pilote (pilot/registre.json) est déposé sur
POST /internal/documents/ingester avec ses métadonnées de la liste figée
(pilot/corpus_pilote.json) : le service exécute le pipeline complet
(couche brute MinIO → extraction → segmentation → scoring → publication
des segments). Ré-ingérer est sans effet (statut deja_present).

Usage :
    python tools/ingester_pilote.py            # tout le pilote
    python tools/ingester_pilote.py --cles A1  # un document
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx

RACINE = Path(__file__).resolve().parent.parent
FICHIER_LISTE = RACINE / "pilot" / "corpus_pilote.json"
FICHIER_REGISTRE = RACINE / "pilot" / "registre.json"
DELAI_SECONDES = 0.2


def journal(message: str) -> None:
    print(message, flush=True)


def jeton_interne() -> str:
    for ligne in (RACINE / "infra" / ".env").read_text(encoding="utf-8").splitlines():
        if match := re.match(r"^INTERNAL_TOKEN=(.*)$", ligne.strip()):
            return match.group(1)
    raise SystemExit("INTERNAL_TOKEN introuvable dans infra/.env")


def principale(cibles: list[str] | None) -> int:
    liste = json.loads(FICHIER_LISTE.read_text(encoding="utf-8"))
    registre = json.loads(FICHIER_REGISTRE.read_text(encoding="utf-8"))["documents"]
    chemins = {entree["cle_pilote"]: entree["chemin_local"] for entree in registre.values()}

    compteur: dict[str, int] = {}
    with httpx.Client(
        base_url="http://127.0.0.1", timeout=120, trust_env=False
    ) as client:
        for document in liste["documents"]:
            cle = document["cle"]
            if cibles and cle not in cibles:
                continue
            chemin = chemins.get(cle)
            if chemin is None:
                journal(f"  IGNORE {cle} — pas de PDF au registre")
                continue
            chemin_pdf_adala = document["chemin_pdf_adala"]
            url_source = (
                "https://adala.justice.gov.ma/api/uploads/"
                + chemin_pdf_adala[len("uploads/"):]
            )
            reponse = client.post(
                "/internal/documents/ingester",
                headers={
                    "Host": "corpus.m3ak.localhost",
                    "X-Internal-Token": jeton_interne(),
                },
                files={"fichier": (Path(chemin).name, (RACINE / chemin).read_bytes(), "application/pdf")},
                data={
                    "reference": document["reference_promulgation"],
                    "titre": document["intitule"],
                    "langue": "fr",
                    "source": "adala",
                    "url_source": url_source,
                },
            )
            if reponse.status_code != 200:
                journal(f"  ERREUR {cle} {document['intitule']} — HTTP {reponse.status_code} : {reponse.text[:120]}")
                compteur["erreur"] = compteur.get("erreur", 0) + 1
            else:
                corps = reponse.json()
                compteur[corps["statut"]] = compteur.get(corps["statut"], 0) + 1
                journal(
                    f"  {corps['statut'].upper():18} {cle} {document['intitule'][:44]} — "
                    f"classe {corps['classe_qualite']}, {corps['nb_articles']} articles, "
                    f"{corps['nb_messages_publies']} message(s)"
                )
            time.sleep(DELAI_SECONDES)

    journal(f"termine : {compteur}")
    return 1 if compteur.get("erreur") else 0


if __name__ == "__main__":
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--cles", help="sous-ensemble de clés pilotes (A1,B2)")
    arguments = analyseur.parse_args()
    sys.exit(principale(arguments.cles.split(",") if arguments.cles else None))
