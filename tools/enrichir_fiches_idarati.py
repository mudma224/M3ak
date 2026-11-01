"""Complète les fiches Idarati déjà collectées avec leurs PIÈCES requises.

L'endpoint ``procedures/{id}/documents`` de la source (découvert pour
l'étape 4 — checklist de démarche) sert les pièces groupées par sections
(« الوثائق المطلوبة »...) ; il n'existait pas au moment de la collecte
initiale. L'outil repasse sur les fiches du corpus, interroge l'endpoint
pour celles dont les pièces manquent, et dépose la réponse TELLE QUE
SERVIE en couche brute sous ``idarati/{empreinte}.pieces.json`` — la clé
dérivée attendue par ``GET /internal/documents/{id}/fiche``. La couche
normalisée n'est pas touchée (aucun changement d'empreinte, aucune
re-indexation).

Idempotent : les pièces déjà présentes sont sautées — rejouable autant
de fois que nécessaire. Poli : ~1 requête/seconde et demie, agent
identifiable. Le conteneur MinIO et la base du corpus doivent tourner.

Usage :
    python tools/enrichir_fiches_idarati.py                # tout le corpus
    python tools/enrichir_fiches_idarati.py --dry-run      # recense sans collecter
    python tools/enrichir_fiches_idarati.py --limite 100   # par tranches
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time

import httpx

BUCKET = "m3ak-corpus-brut"
PREFIXE = "idarati"
DELAI_SECONDES = 1.2
AGENT = "M3ak-outillage/1.0 (enrichissement pieces Idarati; contact: porteur M3ak)"
HOTE = "procedures.idarati.ma"


def journal(message: str) -> None:
    print(message, flush=True)


def _psql(requete: str) -> str:
    return subprocess.run(
        ["docker", "exec", "m3ak-corpus-db", "psql", "-U", "u_corpus",
         "-d", "db_corpus", "-t", "-A", "-F", "\x1f", "-c", requete],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout


def fiches_corpus() -> list[dict]:
    """Les fiches Idarati du corpus : identifiant source, empreinte
    (elle porte la clé brute) et titre pour le suivi."""
    lignes = _psql(
        "SELECT document_id, empreinte_sha256, titre FROM documents "
        "WHERE source = 'idarati' AND en_quarantaine = false "
        "ORDER BY document_id"
    )
    fiches = []
    for ligne in lignes.strip().splitlines():
        parties = ligne.split("\x1f", 2)
        if len(parties) != 3:
            continue
        document_id, empreinte, titre = parties
        identifiant = document_id.split("|", 1)[0]
        fiches.append(
            {"document_id": document_id, "identifiant": identifiant,
             "empreinte": empreinte, "titre": titre}
        )
    return fiches


def pieces_existantes() -> set[str]:
    """Les empreintes qui ont déjà leurs pièces, NON VIDES, en couche
    brute. Un objet de 0 octet est traité comme absent : un dépôt raté
    (stdin non transféré, ex. docker exec sans -i) ne doit jamais être
    pris pour un enrichissement réussi."""
    sortie = subprocess.run(
        ["docker", "exec", "m3ak-minio", "sh", "-c",
         ('mc alias set local http://localhost:9000 "$MINIO_ROOT_USER" '
          '"$MINIO_ROOT_PASSWORD" >/dev/null; '
          f'mc ls --json --recursive "local/{BUCKET}/{PREFIXE}/"')],
        capture_output=True, text=True, check=True,
    ).stdout
    existantes: set[str] = set()
    for ligne in sortie.splitlines():
        try:
            objet = json.loads(ligne)
        except json.JSONDecodeError:
            continue
        cle = str(objet.get("key") or "")
        if int(objet.get("size") or 0) <= 0:
            continue
        if match := re.search(r"/idarati/([0-9a-f]{64})\.pieces\.json$", cle):
            existantes.add(match.group(1))
    return existantes


def deposer_pieces(empreinte: str, contenu: bytes) -> None:
    """Dépôt brut via ``mc pipe`` (le contenu arrive sur l'entrée standard ;
    il n'est jamais écrit sur le disque de la machine hôte). Le ``-i`` est
    INDISPENSABLE : sans lui, docker exec ne transfère pas l'entrée
    standard et l'objet est stocké vide."""
    subprocess.run(
        ["docker", "exec", "-i", "m3ak-minio", "sh", "-c",
         ('mc alias set local http://localhost:9000 "$MINIO_ROOT_USER" '
          '"$MINIO_ROOT_PASSWORD" >/dev/null; '
          f'mc pipe "local/{BUCKET}/{PREFIXE}/{empreinte}.pieces.json"')],
        input=contenu, capture_output=True, check=True,
    )


def principale(dry_run: bool, limite: int, pause: float) -> int:
    fiches = fiches_corpus()
    existantes = pieces_existantes()
    a_traiter = [f for f in fiches if f["empreinte"] not in existantes]
    if limite > 0:
        a_traiter = a_traiter[:limite]
    journal(
        f"fiches idarati : {len(fiches)} — pièces déjà en couche brute : "
        f"{len(fiches) - len([f for f in fiches if f['empreinte'] not in existantes])} — "
        f"à traiter : {len(a_traiter)}"
        + (" (dry-run)" if dry_run else "")
    )
    if dry_run:
        for fiche in a_traiter[:20]:
            journal(f"  serait collecté : {fiche['identifiant'][:8]} {fiche['titre'][:60]}")
        if len(a_traiter) > 20:
            journal(f"  … et {len(a_traiter) - 20} autres")
        return 0

    statuts = {"pieces": 0, "sans_pieces": 0, "echec": 0}
    client = httpx.Client(
        headers={"User-Agent": AGENT},
        timeout=60,
        follow_redirects=False,
        trust_env=False,
    )
    with client:
        for rang, fiche in enumerate(a_traiter, start=1):
            identifiant = fiche["identifiant"]
            try:
                reponse = client.get(
                    f"https://{HOTE}/api/informational/procedures/{identifiant}/documents"
                )
                if reponse.status_code == 404:
                    statuts["sans_pieces"] += 1
                elif reponse.status_code == 200:
                    deposer_pieces(fiche["empreinte"], reponse.content)
                    statuts["pieces"] += 1
                else:
                    reponse.raise_for_status()
            except Exception as erreur:  # noqa: BLE001 — consigné, l'outil poursuit
                statuts["echec"] += 1
                journal(f"  echec {identifiant[:8]} : {str(erreur)[:90]}")
            if rang % 50 == 0:
                journal(f"  {rang}/{len(a_traiter)} traités — {statuts}")
            time.sleep(pause)

    journal(f"terminé : {statuts}")
    journal(
        "les pièces sont en couche brute ; l'endpoint de lecture du service "
        "corpus (GET /internal/documents/{id}/fiche) les sert telles quelles."
    )
    return 0


if __name__ == "__main__":
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--dry-run", action="store_true", help="recense sans collecter")
    parseur.add_argument("--limite", type=int, default=0, help="bornage de fiches (0 = toutes)")
    parseur.add_argument(
        "--pause-secondes", type=float, default=DELAI_SECONDES,
        help="pause entre requêtes (politesse envers la source)",
    )
    arguments = parseur.parse_args()
    sys.exit(principale(arguments.dry_run, arguments.limite, arguments.pause_secondes))
