"""Analyse un échantillon de documents d'une classe — revue de grammaire.

Pour chaque document échantillonné (réparti sur l'étendue des tailles),
récupère la copie brute et montre ce que l'extraction produit réellement :
comptage lettres arabes/latines, densité par page, lignes les plus
fréquentes. Sert à concevoir les règles (langue, seuils de détection) sur
des données réelles plutôt que sur des hypothèses.

Usage :
    python tools/analyser_quarantaine.py --langue fr --classe C --nombre 12
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from io import BytesIO
from pathlib import Path

import httpx

RACINE = Path(__file__).resolve().parent.parent


def journal(message: str) -> None:
    print(message, flush=True)


def jeton_interne() -> str:
    for ligne in (RACINE / "infra" / ".env").read_text(encoding="utf-8").splitlines():
        if match := re.match(r"^INTERNAL_TOKEN=(.*)$", ligne.strip()):
            return match.group(1)
    raise SystemExit("INTERNAL_TOKEN introuvable dans infra/.env")


def documents(langue: str, classe: str) -> list[dict]:
    sortie = subprocess.run(
        ["docker", "exec", "m3ak-corpus-db", "psql", "-U", "u_corpus", "-d", "db_corpus",
         "-t", "-A", "-F", "\x1f", "-c",
         "SELECT empreinte_sha256, split_part(document_id, '|', 1), titre, pages, caracteres "
         "FROM documents WHERE langue = '" + langue + "' AND classe_qualite = '" + classe + "' "
         "ORDER BY pages"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    retenus = []
    for ligne in sortie.strip().splitlines():
        morceaux = ligne.split("\x1f")
        if len(morceaux) != 5:
            continue
        empreinte, reference, titre, pages, caracteres = morceaux
        retenus.append(
            {
                "empreinte": empreinte,
                "reference": reference,
                "titre": titre,
                "pages": int(pages or 0),
                "caracteres": int(caracteres or 0),
            }
        )
    return retenus


def echantillon(liste: list[dict], nombre: int) -> list[dict]:
    """Répartit le prélèvement sur l'étendue des tailles (pages croissantes)."""
    if len(liste) <= nombre:
        return liste
    pas = len(liste) / nombre
    return [liste[int(i * pas)] for i in range(nombre)]


def analyser(contenu: bytes) -> dict:
    from pypdf import PdfReader

    lecteur = PdfReader(BytesIO(contenu))
    pages = len(lecteur.pages)
    lignes_texte: list[str] = []
    for page in lecteur.pages:
        lignes_texte.extend((page.extract_text() or "").splitlines())
    texte = "\n".join(lignes_texte)
    arabes = sum(1 for c in texte if "\u0600" <= c <= "\u06ff")
    latines = sum(1 for c in texte if c.isascii() and c.isalpha())
    frequents = Counter(
        ligne.strip() for ligne in lignes_texte if len(ligne.strip()) >= 4
    )
    return {
        "pages": pages,
        "lignes": len(lignes_texte),
        "caracteres": len(texte),
        "arabes": arabes,
        "latines": latines,
        "frequents": frequents.most_common(6),
    }


def principale(langue: str, classe: str, nombre: int) -> int:
    liste = documents(langue, classe)
    journal(f"{len(liste)} documents {langue}/{classe} — échantillon de {min(nombre, len(liste))}")
    jeton = jeton_interne()
    entetes = {"Host": "corpus.m3ak.localhost", "X-Internal-Token": jeton}
    with httpx.Client(
        base_url="http://127.0.0.1", timeout=180, trust_env=False
    ) as client:
        for document in echantillon(liste, nombre):
            reponse = client.get("/internal/documents/brut/" + document["empreinte"], headers=entetes)
            if reponse.status_code != 200:
                journal(f"  ERREUR {document['reference']} — HTTP {reponse.status_code}")
                continue
            mesures = analyser(reponse.content)
            densite = mesures["caracteres"] // max(mesures["pages"], 1)
            journal(
                f"\n=== {document['reference']} ({mesures['pages']}p, "
                f"{document['caracteres']} car. en base)"
            )
            journal(f"  titre   : {document['titre'][:90]}")
            journal(
                f"  mesures : {mesures['caracteres']} car., {mesures['lignes']} lignes, "
                f"densité {densite}/page, lettres ar {mesures['arabes']} / lat {mesures['latines']}"
            )
            for ligne, fois in mesures["frequents"]:
                journal(f"  x{fois:3}  {ligne[:80]}")
    return 0


if __name__ == "__main__":
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--langue", choices=["ar", "fr", "inconnue"], default="fr")
    analyseur.add_argument("--classe", choices=["A", "B", "C"], default="C")
    analyseur.add_argument("--nombre", type=int, default=12)
    arguments = analyseur.parse_args()
    sys.exit(principale(arguments.langue, arguments.classe, arguments.nombre))
