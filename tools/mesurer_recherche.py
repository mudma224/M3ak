"""Mesure de la récupération sur le jeu doré (phase 2, étape 1).

Appelle l'API interne du service recherche pour chaque question du jeu
doré (pilot/jeu_dore/questions.json) et calcule :
- recall@k : proportion de questions dont au moins une source attendue
  figure dans le top-k ;
- MRR : moyenne de 1/rang de la première source attendue retrouvée ;
détaillés par langue et par famille. Le rapport est versionné dans
pilot/jeu_dore/resultats/ ; les questions ratées sont listées pour le
diagnostic.

Prérequis : service recherche démarré, index alimenté (backfill au moins
partiellement exécuté), jeton interne dans infra/.env.

Usage :
    python tools/mesurer_recherche.py            # top-k = 5
    python tools/mesurer_recherche.py --k 10
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

RACINE = Path(__file__).resolve().parent.parent
FICHIER_QUESTIONS = RACINE / "pilot" / "jeu_dore" / "questions.json"
DOSSIER_RESULTATS = RACINE / "pilot" / "jeu_dore" / "resultats"
URL_RECHERCHE = "http://127.0.0.1/internal/recherche"
HOST_RECHERCHE = "recherche.m3ak.localhost"


def journal(message: str) -> None:
    print(message, flush=True)


def jeton_interne() -> str:
    for ligne in (RACINE / "infra" / ".env").read_text(encoding="utf-8").splitlines():
        if ligne.startswith("INTERNAL_TOKEN="):
            return ligne.split("=", 1)[1].strip()
    raise SystemExit("INTERNAL_TOKEN introuvable dans infra/.env")


def valider_url_bouclage(url: str) -> None:
    """L'API interne est joignable via Traefik en local uniquement."""
    adresse = urlparse(url).hostname
    if not all(
        ipaddress.ip_address(information[4][0]).is_loopback
        for information in socket.getaddrinfo(adresse, None)
    ):
        raise SystemExit("l'URL du service recherche doit être une adresse de bouclage")


def mesurer(k: int) -> int:
    questions = json.loads(FICHIER_QUESTIONS.read_text(encoding="utf-8"))["questions"]
    entetes = {
        "Host": HOST_RECHERCHE,
        "X-Internal-Token": jeton_interne(),
        "Content-Type": "application/json",
    }
    valider_url_bouclage(URL_RECHERCHE)

    detail: list[dict] = []
    with httpx.Client(trust_env=False, timeout=60.0) as client:
        for question in questions:
            debut = time.perf_counter()
            reponse = client.post(
                URL_RECHERCHE,
                headers=entetes,
                json={"question": question["question"], "top_k": k},
            )
            duree_ms = int((time.perf_counter() - debut) * 1000)
            if reponse.status_code != 200:
                raise SystemExit(
                    f"{question['id']} : HTTP {reponse.status_code} — {reponse.text[:200]}"
                )
            corps = reponse.json()
            resultats = corps.get("resultats", [])
            attendus = set(question["attendus"])
            rang_premier = None
            retrouves: set[str] = set()
            for rang, resultat in enumerate(resultats, start=1):
                if resultat["document_id"] in attendus:
                    retrouves.add(resultat["document_id"])
                    if rang_premier is None:
                        rang_premier = rang
            detail.append(
                {
                    "id": question["id"],
                    "langue": question["langue"],
                    "famille": question["famille"],
                    "note": question.get("note", ""),
                    "mode": corps.get("mode"),
                    "cache": corps.get("cache", False),
                    "nb_resultats": len(resultats),
                    "retrouves": sorted(retrouves),
                    "attendus_total": len(attendus),
                    "rang_premier": rang_premier,
                    "recall": 1.0 if retrouves else 0.0,
                    "mrr": 1.0 / rang_premier if rang_premier else 0.0,
                    "duree_ms": duree_ms,
                }
            )
            marque = f"rang {rang_premier}" if rang_premier else "RATEE"
            journal(f"  {question['id']} : {marque} ({corps.get('mode')}, {duree_ms} ms)")

    rapport = {
        "date": datetime.now(timezone.utc).isoformat(),
        "k": k,
        "n_questions": len(detail),
        "recall": sum(d["recall"] for d in detail) / len(detail),
        "mrr": sum(d["mrr"] for d in detail) / len(detail),
        "duree_ms_moyenne": sum(d["duree_ms"] for d in detail) / len(detail),
        "modes": sorted({d["mode"] for d in detail if d["mode"]}),
        "par_langue": _agregat(detail, "langue"),
        "par_famille": _agregat(detail, "famille"),
        "detail": detail,
    }
    DOSSIER_RESULTATS.mkdir(parents=True, exist_ok=True)
    horodatage = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    chemin = DOSSIER_RESULTATS / f"mesure-{horodatage}-k{k}.json"
    chemin.write_text(
        json.dumps(rapport, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    journal("")
    journal("=== Récupération sur le jeu doré ===")
    journal(f"recall@{k} : {rapport['recall']:.2%}    MRR : {rapport['mrr']:.3f}")
    journal(f"durée moyenne : {rapport['duree_ms_moyenne']:.0f} ms ; modes : {rapport['modes']}")
    for langue, valeurs in rapport["par_langue"].items():
        journal(f"  {langue} : recall {valeurs['recall']:.2%}, MRR {valeurs['mrr']:.3f}")
    for famille, valeurs in rapport["par_famille"].items():
        journal(f"  {famille} : recall {valeurs['recall']:.2%}, MRR {valeurs['mrr']:.3f}")
    ratees = [d for d in detail if d["recall"] == 0]
    if ratees:
        journal(f"\nQuestions ratées ({len(ratees)}) :")
        for d in ratees:
            journal(f"  {d['id']} [{d['langue']}/{d['famille']}] {d['note']}")
    journal(f"\nRapport : {chemin.relative_to(RACINE)}")
    return 0


def _agregat(detail: list[dict], cle: str) -> dict:
    agregat: dict[str, dict] = {}
    for valeur in sorted({d[cle] for d in detail}):
        parties = [d for d in detail if d[cle] == valeur]
        agregat[valeur] = {
            "n": len(parties),
            "recall": sum(d["recall"] for d in parties) / len(parties),
            "mrr": sum(d["mrr"] for d in parties) / len(parties),
        }
    return agregat


def principale() -> int:
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--k", type=int, default=5, help="profondeur du top-k mesuré")
    arguments = analyseur.parse_args()
    return mesurer(arguments.k)


if __name__ == "__main__":
    raise SystemExit(principale())
