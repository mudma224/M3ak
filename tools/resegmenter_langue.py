"""Re-segmente tous les documents d'une langue quand la grammaire évolue.

La couche brute étant définitive, rien n'est re-téléchargé depuis la
source : chaque document est relu via GET /internal/documents/brut/<empreinte>
puis re-déposé sur POST /internal/documents/ingester. La purge préalable
des fiches de la langue (base corpus + segments du service recherche)
garantit que le pipeline re-traite chaque contenu au lieu de répondre
« deja_present ».

Les commandes psql sont écrites intégralement en littéraux (la langue,
validée ar|fr, n'entre jamais dans une construction de commande).

Garde-fou : le service corpus doit répondre au test /internal/echo avant
toute purge — la purge n'a de sens que si la ré-ingestion qui suit peut
atteindre le service.

Aucune langue n'est ré-imposée à la ré-ingestion : l'ancienne étiquette
est précisément ce qu'on réexamine (les scans arabes mal étiquetés « fr »
le resteraient si on la repassait comme indication). Le pipeline décide
donc seul : détection du contenu, puis langue du titre, puis « inconnue ».

Usage :
    python tools/resegmenter_langue.py ar
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

RACINE = Path(__file__).resolve().parent.parent
DELAI_SECONDES = 0.3


def journal(message: str) -> None:
    print(message, flush=True)


def jeton_interne() -> str:
    for ligne in (RACINE / "infra" / ".env").read_text(encoding="utf-8").splitlines():
        if match := re.match(r"^INTERNAL_TOKEN=(.*)$", ligne.strip()):
            return match.group(1)
    raise SystemExit("INTERNAL_TOKEN introuvable dans infra/.env")


def _lister_ar() -> list[dict]:
    sortie = subprocess.run(
        ["docker", "exec", "m3ak-corpus-db", "psql", "-U", "u_corpus", "-d", "db_corpus",
         "-t", "-A", "-F", "\x1f", "-c",
         "SELECT empreinte_sha256, document_id, titre FROM documents WHERE langue = 'ar' ORDER BY document_id"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    return _analyser(sortie)


def _lister_fr() -> list[dict]:
    sortie = subprocess.run(
        ["docker", "exec", "m3ak-corpus-db", "psql", "-U", "u_corpus", "-d", "db_corpus",
         "-t", "-A", "-F", "\x1f", "-c",
         "SELECT empreinte_sha256, document_id, titre FROM documents WHERE langue = 'fr' ORDER BY document_id"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    return _analyser(sortie)


def _analyser(sortie: str) -> list[dict]:
    documents = []
    for ligne in sortie.strip().splitlines():
        morceaux = ligne.split("\x1f", 2)
        if len(morceaux) != 3:
            continue
        empreinte, document_id, titre = morceaux
        documents.append({"empreinte": empreinte, "document_id": document_id, "titre": titre})
    return documents


def _purger_ar() -> None:
    sortie_corpus = subprocess.run(
        ["docker", "exec", "m3ak-corpus-db", "psql", "-U", "u_corpus", "-d", "db_corpus",
         "-c", "DELETE FROM documents WHERE langue = 'ar'"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    journal(f"purge db_corpus (ar) : {sortie_corpus.strip()}")
    sortie_recherche = subprocess.run(
        ["docker", "exec", "m3ak-recherche-db", "psql", "-U", "u_recherche", "-d", "db_recherche",
         "-c", "DELETE FROM segments WHERE langue = 'ar'"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    journal(f"purge db_recherche (ar) : {sortie_recherche.strip()}")


def _purger_fr() -> None:
    sortie_corpus = subprocess.run(
        ["docker", "exec", "m3ak-corpus-db", "psql", "-U", "u_corpus", "-d", "db_corpus",
         "-c", "DELETE FROM documents WHERE langue = 'fr'"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    journal(f"purge db_corpus (fr) : {sortie_corpus.strip()}")
    sortie_recherche = subprocess.run(
        ["docker", "exec", "m3ak-recherche-db", "psql", "-U", "u_recherche", "-d", "db_recherche",
         "-c", "DELETE FROM segments WHERE langue = 'fr'"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    journal(f"purge db_recherche (fr) : {sortie_recherche.strip()}")


def principale(langue: str) -> int:
    if langue == "ar":
        documents = _lister_ar()
    elif langue == "fr":
        documents = _lister_fr()
    else:
        raise SystemExit("langue invalide (ar|fr)")
    if not documents:
        journal(f"aucun document {langue} en base — rien a faire")
        return 0
    journal(f"{len(documents)} documents {langue} a re-segmenter")

    jeton = jeton_interne()
    entetes = {"Host": "corpus.m3ak.localhost", "X-Internal-Token": jeton}
    # Garde-fou : la purge n'a de sens que si le service répond —
    # ne jamais supprimer des fiches qu'on ne peut pas ré-ingérer.
    with httpx.Client(
        base_url="http://127.0.0.1", timeout=10, trust_env=False
    ) as client:
        sonde = client.get("/internal/echo", headers=entetes)
        if sonde.status_code != 200:
            raise SystemExit(
                f"service corpus injoignable (HTTP {sonde.status_code}) — purge annulee, rien n'a ete supprime"
            )
    if langue == "ar":
        _purger_ar()
    else:
        _purger_fr()

    compteur: dict[str, int] = {}
    with httpx.Client(
        base_url="http://127.0.0.1", timeout=300, trust_env=False
    ) as client:
        # le document_id original porte la référence et la langue ;
        # la référence se retrouve en tête de la clé « reference|langue|version »
        for document in documents:
            reference = document["document_id"].split("|")[0]
            brut = client.get(
                "/internal/documents/brut/" + document["empreinte"], headers=entetes
            )
            if brut.status_code != 200:
                journal(f"  ERREUR brut {document['empreinte'][:12]} — HTTP {brut.status_code}")
                compteur["echec_brut"] = compteur.get("echec_brut", 0) + 1
                continue
            reponse = client.post(
                "/internal/documents/ingester",
                headers=entetes,
                files={
                    "fichier": (document["empreinte"][:12] + ".pdf", brut.content, "application/pdf")
                },
                data={
                    "reference": reference,
                    "titre": document["titre"],
                    # volontairement vide : ne pas ré-imposer l'étiquette
                    # examinée — le pipeline redécide (détection → titre)
                    "langue": "",
                    "source": "adala",
                },
            )
            if reponse.status_code != 200:
                journal(f"  ERREUR ingester {reference} — HTTP {reponse.status_code} {reponse.text[:80]}")
                compteur["echec"] = compteur.get("echec", 0) + 1
            else:
                corps = reponse.json()
                compteur[corps["statut"]] = compteur.get(corps["statut"], 0) + 1
                journal(
                    f"  {corps['statut'].upper():18} {reference[:28]:28} classe {corps['classe_qualite']}, "
                    f"{corps['nb_articles']} articles"
                )
            time.sleep(DELAI_SECONDES)

    journal(f"termine : {json.dumps(compteur, ensure_ascii=False)}")
    return 1 if compteur.get("echec") or compteur.get("echec_brut") else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    sys.exit(principale(sys.argv[1]))
