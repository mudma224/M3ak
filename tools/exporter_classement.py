"""Exporte en local la vue de classement des PDF du corpus.

Pour chaque document de la base du service corpus, télécharge la copie
brute (GET /internal/documents/brut/<empreinte>) et la range dans :

    pilot/classement/<langue>/<A-fiable|B-marque|C-quarantaine>/<reference>-<empreinte12>.pdf

Le dossier est purgé puis régénéré à chaque exécution : c'est une vue de
revue manuelle (améliorer la grammaire au fil de l'eau), jamais la
couche brute — qui reste immuable, par empreinte, dans MinIO et pilot/raw.

Usage :
    python tools/exporter_classement.py                # tout le corpus
    python tools/exporter_classement.py --classe C     # une seule classe
    python tools/exporter_classement.py --langue ar    # une seule langue
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

RACINE = Path(__file__).resolve().parent.parent
DOSSIER_CLASSEMENT = RACINE / "pilot" / "classement"
DOSSIERS_CLASSES = {"A": "A-fiable", "B": "B-marque", "C": "C-quarantaine"}
DELAI_SECONDES = 0.15


def journal(message: str) -> None:
    print(message, flush=True)


def jeton_interne() -> str:
    for ligne in (RACINE / "infra" / ".env").read_text(encoding="utf-8").splitlines():
        if match := re.match(r"^INTERNAL_TOKEN=(.*)$", ligne.strip()):
            return match.group(1)
    raise SystemExit("INTERNAL_TOKEN introuvable dans infra/.env")


def documents(filtre_langue: str | None, filtre_classe: str | None) -> list[dict]:
    sortie = subprocess.run(
        ["docker", "exec", "m3ak-corpus-db", "psql", "-U", "u_corpus", "-d", "db_corpus",
         "-t", "-A", "-F", "\x1f", "-c",
         "SELECT document_id, langue, classe_qualite, empreinte_sha256 FROM documents ORDER BY document_id"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    retenus = []
    for ligne in sortie.strip().splitlines():
        morceaux = ligne.split("\x1f")
        if len(morceaux) != 4:
            continue
        document_id, langue, classe, empreinte = morceaux
        if filtre_langue and langue != filtre_langue:
            continue
        if filtre_classe and classe != filtre_classe:
            continue
        reference = re.sub(r'[<>:"/\\|?*]+', "-", document_id.split("|")[0])
        retenus.append(
            {
                "langue": langue,
                "classe": classe,
                "empreinte": empreinte,
                "nom": f"{reference}-{empreinte[:12]}.pdf",
            }
        )
    return retenus


def purger_dossier() -> None:
    if not DOSSIER_CLASSEMENT.is_dir():
        return

    def _vider(dossier: Path) -> None:
        for enfant in dossier.iterdir():
            if enfant.is_dir():
                _vider(enfant)
                enfant.rmdir()
            else:
                enfant.unlink()

    _vider(DOSSIER_CLASSEMENT)


def exporter_local(filtre_langue: str | None, filtre_classe: str | None) -> int:
    if filtre_classe and filtre_classe not in DOSSIERS_CLASSES:
        raise SystemExit("classe invalide (A|B|C)")
    if filtre_classe or filtre_langue:
        # Filtre : on ne purge pas tout le dossier, seulement les sous-dossiers visés.
        cibles = [
            DOSSIER_CLASSEMENT / langue / DOSSIERS_CLASSES[classe]
            for langue in ([filtre_langue] if filtre_langue else ["ar", "fr", "inconnue"])
            for classe in ([filtre_classe] if filtre_classe else ["A", "B", "C"])
        ]
    else:
        purger_dossier()
        cibles = []
    for cible in cibles:
        if cible.is_dir():
            for enfant in cible.iterdir():
                if enfant.is_file():
                    enfant.unlink()

    liste = documents(filtre_langue, filtre_classe)
    journal(f"{len(liste)} documents a exporter vers {DOSSIER_CLASSEMENT}")
    jeton = jeton_interne()
    entetes = {"Host": "corpus.m3ak.localhost", "X-Internal-Token": jeton}
    compteurs: dict[str, int] = {}
    with httpx.Client(
        base_url="http://127.0.0.1", timeout=120, trust_env=False
    ) as client:
        for document in liste:
            reponse = client.get("/internal/documents/brut/" + document["empreinte"], headers=entetes)
            if reponse.status_code != 200:
                journal(f"  ERREUR {document['nom']} — HTTP {reponse.status_code}")
                compteurs["erreur"] = compteurs.get("erreur", 0) + 1
                continue
            destination = (
                DOSSIER_CLASSEMENT / document["langue"] / DOSSIERS_CLASSES[document["classe"]]
            )
            destination.mkdir(parents=True, exist_ok=True)
            (destination / document["nom"]).write_bytes(reponse.content)
            cle = f"{document['langue']}/{DOSSIERS_CLASSES[document['classe']]}"
            compteurs[cle] = compteurs.get(cle, 0) + 1
            time.sleep(DELAI_SECONDES)

    for cle, nombre in sorted(compteurs.items()):
        journal(f"  {cle:24} {nombre:4} fichiers")
    total = sum(v for k, v in compteurs.items() if k != "erreur")
    journal(f"termine : {total} fichiers exportes")
    return 1 if compteurs.get("erreur") else 0


if __name__ == "__main__":
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--langue", choices=["ar", "fr", "inconnue"])
    analyseur.add_argument("--classe", choices=["A", "B", "C"])
    arguments = analyseur.parse_args()
    sys.exit(exporter_local(arguments.langue, arguments.classe))
