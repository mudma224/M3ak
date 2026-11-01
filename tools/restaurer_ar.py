"""Restaure des documents arabes depuis l'export local de classement.

La couche brute MinIO est la source de vérité ; si les fiches de la base
sont perdues, la correspondance référence ↔ empreinte reste disponible
dans les noms de fichiers de l'arborescence locale de classement :
``pilot/classement/ar/<classe>/<référence>-<empreinte12>.pdf``.

Cet outil reconstruit les fiches exactes sans rien re-télécharger :
noms de fichiers → références et empreintes ; manifestes de collecte →
titres ; GET brut + POST ingester → le pipeline rejoue tout
(extraction, segmentation, langue, classe) comme une première ingestion.

Limites assumées : les documents collectés après le dernier export local
ne sont pas couverts par les noms de fichiers (ils reviendront par une
re-collecte idempotente — même empreinte, même document) ; les titres
absents des manifestes retombent sur la référence.

Usage :
    python tools/restaurer_ar.py            # applique la restauration
    python tools/restaurer_ar.py --dry-run  # liste sans ingérer
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

RACINE = Path(__file__).resolve().parent.parent
DOSSIER_CLASSEMENT_AR = RACINE / "pilot" / "classement" / "ar"
DELAI_SECONDES = 0.3


def journal(message: str) -> None:
    print(message, flush=True)


def jeton_interne() -> str:
    for ligne in (RACINE / "infra" / ".env").read_text(encoding="utf-8").splitlines():
        if match := re.match(r"^INTERNAL_TOKEN=(.*)$", ligne.strip()):
            return match.group(1)
    raise SystemExit("INTERNAL_TOKEN introuvable dans infra/.env")


def _psql(requete: str) -> str:
    return subprocess.run(
        ["docker", "exec", "m3ak-corpus-db", "psql", "-U", "u_corpus", "-d", "db_corpus",
         "-t", "-A", "-c", requete],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout


def liaisons_locales() -> list[dict]:
    """Référence et empreinte (12 caractères) lues dans les noms de fichiers.

    L'exportateur assainit les références pour le système de fichiers : la
    correspondance inverse vers la référence originale se fait plus bas via
    les manifestes.
    """
    liaisons = []
    for dossier in DOSSIER_CLASSEMENT_AR.iterdir():
        if not dossier.is_dir():
            continue
        for pdf in dossier.glob("*.pdf"):
            racine = pdf.stem
            nom, empreinte12 = racine.rsplit("-", 1)
            liaisons.append(
                {"reference": nom, "empreinte12": empreinte12, "classe": dossier.name}
            )
    return liaisons


def carte_empreintes_minio() -> dict[str, str]:
    """Préfixe (12 caractères) → empreinte complète, depuis la couche brute.

    L'alias mc est réinitialisé dans la même commande : il n'est pas
    garanti de persister d'une exécution docker exec à l'autre.
    """
    sortie = subprocess.run(
        ["docker", "exec", "m3ak-minio", "sh", "-c",
         ('mc alias set local http://localhost:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" '
          '>/dev/null && mc ls --recursive local/m3ak-corpus-brut/adala/')],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    carte: dict[str, str] = {}
    for ligne in sortie.strip().splitlines():
        # « [date] taille adala/<empreinte64>.pdf » — le nom est le dernier champ
        nom = ligne.split()[-1]
        empreinte64 = nom.split("/")[-1].removesuffix(".pdf")
        if re.fullmatch(r"[0-9a-f]{64}", empreinte64):
            prefixe = empreinte64[:12]
            if prefixe in carte and carte[prefixe] != empreinte64:
                raise SystemExit(f"collision de préfixe d'empreinte : {prefixe}")
            carte[prefixe] = empreinte64
    return carte


def tables_des_manifestes() -> tuple[dict[str, str], dict[str, str]]:
    """(titres, références originales) depuis la table manifestes.

    titres : référence (originale) → titre, dernière occurrence.
    references_originales : référence assainie → référence originale,
    pour inverser l'assainissement des noms de fichiers.
    """
    assainir = re.compile(r'[<>:"/\\|?*]+')
    lignes = _psql("SELECT entrees FROM manifestes ORDER BY id").strip().splitlines()
    titres: dict[str, str] = {}
    originales: dict[str, str] = {}
    for ligne in lignes:
        for entree in json.loads(ligne):
            reference = entree.get("reference") or ""
            titre = entree.get("titre") or ""
            if not reference or not titre:
                continue
            titres[reference] = titre
            assainie = assainir.sub("-", reference)
            if assainie in originales and originales[assainie] != reference:
                continue  # collision d'assainissement : on ne devine pas
            originales[assainie] = reference
    return titres, originales


def principale(dry_run: bool) -> int:
    liaisons = liaisons_locales()
    journal(f"{len(liaisons)} liaisons lues dans {DOSSIER_CLASSEMENT_AR}")
    carte = carte_empreintes_minio()
    journal(f"{len(carte)} objets bruts côté MinIO")
    titres, originales = tables_des_manifestes()
    journal(f"{len(titres)} titres issus des manifestes")

    jeton = jeton_interne()
    entetes = {"Host": "corpus.m3ak.localhost", "X-Internal-Token": jeton}
    compteurs: dict[str, int] = {}
    with httpx.Client(
        base_url="http://127.0.0.1", timeout=300, trust_env=False
    ) as client:
        for liaison in liaisons:
            empreinte = carte.get(liaison["empreinte12"])
            if empreinte is None:
                journal(f"  SANS_OBJET_BRUT {liaison['reference']} ({liaison['empreinte12']})")
                compteurs["sans_objet"] = compteurs.get("sans_objet", 0) + 1
                continue
            reference = originales.get(liaison["reference"], liaison["reference"])
            titre = titres.get(reference) or reference
            if dry_run:
                journal(f"  A_RESTAURER {reference} <- {liaison['classe']}")
                compteurs["a_restaurer"] = compteurs.get("a_restaurer", 0) + 1
                continue
            brut = client.get("/internal/documents/brut/" + empreinte, headers=entetes)
            if brut.status_code != 200:
                journal(f"  ERREUR brut {reference} — HTTP {brut.status_code}")
                compteurs["echec_brut"] = compteurs.get("echec_brut", 0) + 1
                continue
            reponse = client.post(
                "/internal/documents/ingester",
                headers=entetes,
                files={"fichier": (liaison["empreinte12"] + ".pdf", brut.content, "application/pdf")},
                data={"reference": reference, "titre": titre, "source": "adala"},
            )
            if reponse.status_code != 200:
                journal(f"  ERREUR ingester {reference} — HTTP {reponse.status_code} {reponse.text[:80]}")
                compteurs["echec"] = compteurs.get("echec", 0) + 1
            else:
                corps = reponse.json()
                compteurs[corps["statut"]] = compteurs.get(corps["statut"], 0) + 1
                journal(
                    f"  {corps['statut'].upper():18} {reference[:28]:28} classe {corps['classe_qualite']}, "
                    f"{corps['nb_articles']} articles"
                )
            time.sleep(DELAI_SECONDES)

    journal(f"termine : {json.dumps(compteurs, ensure_ascii=False)}")
    return 1 if compteurs.get("echec") or compteurs.get("echec_brut") else 0


if __name__ == "__main__":
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--dry-run", action="store_true")
    arguments = analyseur.parse_args()
    sys.exit(principale(arguments.dry_run))
