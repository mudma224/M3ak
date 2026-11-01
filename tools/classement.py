"""Régénère la vue de classement complète : MinIO PUIS copie locale.

Une seule commande pour la revue manuelle de la grammaire : le service
corpus reconstruit ``classe/<langue>/<A-fiable|B-marque|C-quarantaine>/``
dans MinIO (copies server-side, la couche brute ne bouge jamais), puis
les PDF sont téléchargés et posés automatiquement dans
``pilot/classement/<langue>/<classe>/`` — les deux vues sont purgées
puis régénérées à l'identique.

C'est le point d'entrée à utiliser après chaque re-segmentation ou
collecte : rien à faire à la main, les fichiers sont déplacés
automatiquement des deux côtés.

Usage :
    python tools/classement.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
from exporter_classement import exporter_local, jeton_interne

RACINE = Path(__file__).resolve().parent.parent


def journal(message: str) -> None:
    print(message, flush=True)


def regenerer_vue_minio(client: httpx.Client, entetes: dict) -> int:
    """Appelle le service corpus : purge classe/ + copie par langue et classe."""
    reponse = client.post(
        "/internal/documents/classement/reorganiser", headers=entetes
    )
    if reponse.status_code != 200:
        journal(f"ERREUR reorganiser — HTTP {reponse.status_code} {reponse.text[:200]}")
        return 1
    corps = reponse.json()
    journal(
        f"vue MinIO regeneree : {corps['copies']} copies "
        f"({corps['anciennes_supprimees']} anciennes supprimees)"
    )
    return 0


def principale() -> int:
    jeton = jeton_interne()
    entetes = {"Host": "corpus.m3ak.localhost", "X-Internal-Token": jeton}
    with httpx.Client(
        base_url="http://127.0.0.1", timeout=300, trust_env=False
    ) as client:
        code_minio = regenerer_vue_minio(client, entetes)
    journal("--- copie locale ---")
    code_local = exporter_local(None, None)
    return code_minio or code_local


if __name__ == "__main__":
    sys.exit(principale())
