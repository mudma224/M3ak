"""Mesure de bout en bout sur le jeu doré (phase 2, étape 5).

Pour chaque question : session anonyme → POST /sessions/{id}/messages
(mode bloc, température 0, fournisseur choisi — local par défaut,
décision du porteur) → réponse VÉRIFIÉE ou refus explicite. Mesures
mécaniques (sans juge LLM) :
- rappel des attendus dans les sources retenues par la réponse (recall, MRR) ;
- taux de refus, par motif (score_insuffisant, generation_tronquee…) ;
- citations vérifiées mécaniquement, renvois rejetés ;
- latences (génération, durée totale).
La fidélité au corpus est garantie par la vérification verbatim du
service (RAGAS faithfulness à juge LLM : reportée, décision de
sobriété).

Échantillon stratifié par (langue, famille), graine fixe pour la
reproductibilité. Point de contrôle JSONL : le harnais peut être
interrompu et repris sans rejouer les questions déjà mesurées. Rapport
versionné dans pilot/jeu_dore/resultats/.

Prérequis : stack démarrée (conversation + recherche + llm + LM Studio
si fournisseur local), Traefik sur 127.0.0.1.

Usage :
    python tools/mesurer_bout_en_bout.py --taille 3      # validation
    python tools/mesurer_bout_en_bout.py --taille 60     # échantillon
    python tools/mesurer_bout_en_bout.py                 # tout le jeu
    python tools/mesurer_bout_en_bout.py --fraiche       # ignore la reprise
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import random
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

RACINE = Path(__file__).resolve().parent.parent
FICHIER_QUESTIONS = RACINE / "pilot" / "jeu_dore" / "questions.json"
DOSSIER_RESULTATS = RACINE / "pilot" / "jeu_dore" / "resultats"
POINT_DE_CONTROLE = DOSSIER_RESULTATS / "bout-en-bout-point-de-controle.jsonl"
URL_CONVERSATION = "http://127.0.0.1"
HOST_CONVERSATION = "conversation.m3ak.localhost"
DELAI_QUESTION_SECONDES = 900.0


def journal(message: str) -> None:
    print(message, flush=True)


def valider_url_bouclage(url: str) -> None:
    """Le service conversation est joignable via Traefik en local uniquement."""
    adresse = urlparse(url).hostname
    if not all(
        ipaddress.ip_address(information[4][0]).is_loopback
        for information in socket.getaddrinfo(adresse, None)
    ):
        raise SystemExit("l'URL du service conversation doit être une adresse de bouclage")


def _echantillon_stratifie(questions: list[dict], taille: int, graine: int) -> list[dict]:
    """Répartition proportionnelle par (langue, famille), plus forts
    restes, tirage aléatoire sous graine fixe (harnais reproductible)."""
    if taille <= 0 or taille >= len(questions):
        return list(questions)
    strates: dict[tuple[str, str], list[dict]] = {}
    for question in questions:
        strates.setdefault((question["langue"], question["famille"]), []).append(question)
    quotients = {
        cle: len(valeurs) * taille / len(questions) for cle, valeurs in strates.items()
    }
    bases = {cle: int(q) for cle, q in quotients.items()}
    manquants = taille - sum(bases.values())
    par_reste = sorted(quotients, key=lambda cle: quotients[cle] - bases[cle], reverse=True)
    for cle in par_reste[:manquants]:
        bases[cle] += 1
    random.seed(graine)
    echantillon: list[dict] = []
    for cle, nombre in bases.items():
        echantillon.extend(random.sample(strates[cle], nombre))
    return echantillon


def _charger_deja_mesures() -> dict[str, dict]:
    if not POINT_DE_CONTROLE.exists():
        return {}
    mesures: dict[str, dict] = {}
    for ligne in POINT_DE_CONTROLE.read_text(encoding="utf-8").splitlines():
        if ligne.strip():
            mesure = json.loads(ligne)
            mesures[mesure["id"]] = mesure
    return mesures


def _mesurer_question(client: httpx.Client, entetes: dict, question: dict, fournisseur: str) -> dict:
    debut = time.perf_counter()
    resultat: dict = {
        "id": question["id"],
        "langue": question["langue"],
        "famille": question["famille"],
        "note": question.get("note", ""),
    }
    try:
        session = client.post(f"{URL_CONVERSATION}/sessions", headers=entetes, timeout=30.0)
        session.raise_for_status()
        identifiant = session.json()["session_id"]
        reponse = client.post(
            f"{URL_CONVERSATION}/sessions/{identifiant}/messages",
            headers=entetes,
            json={"question": question["question"], "fournisseur": fournisseur},
            timeout=DELAI_QUESTION_SECONDES,
        )
        if reponse.status_code != 200:
            resultat["erreur"] = f"HTTP {reponse.status_code} : {reponse.text[:150]}"
            return resultat
        corps = reponse.json()
    except httpx.HTTPError as erreur:
        resultat["erreur"] = f"transport : {type(erreur).__name__}"
        return resultat

    attendus = set(question["attendus"])
    sources = corps.get("sources", [])
    rang_premier = None
    retrouves: set[str] = set()
    for rang, source in enumerate(sources, start=1):
        if source["document_id"] in attendus:
            retrouves.add(source["document_id"])
            if rang_premier is None:
                rang_premier = rang
    resultat.update(
        {
            "refus": corps.get("refus", False),
            "motif_refus": corps.get("motif_refus"),
            "nb_sources": len(sources),
            "retrouves": sorted(retrouves),
            "attendus_total": len(attendus),
            "rang_premier": rang_premier,
            "recall": 1.0 if retrouves else 0.0,
            "mrr": 1.0 / rang_premier if rang_premier else 0.0,
            "citations_verifiees": corps.get("citations_verifiees"),
            "citations_rejetees": corps.get("citations_rejetees", 0),
            "score_meilleur": corps.get("score_meilleur"),
            "repli_interlingue": corps.get("repli_interlingue", False),
            "latence_generation_ms": corps.get("latence_generation_ms"),
            "duree_ms": int((time.perf_counter() - debut) * 1000),
        }
    )
    return resultat


def _percentile(valeurs: list[float], part: float) -> float:
    if not valeurs:
        return 0.0
    valeurs = sorted(valeurs)
    index = min(len(valeurs) - 1, round(part * (len(valeurs) - 1)))
    return valeurs[index]


def _agregat(detail: list[dict], cle: str) -> dict:
    agregat: dict[str, dict] = {}
    for valeur in sorted({d[cle] for d in detail}):
        parties = [d for d in detail if d[cle] == valeur]
        agregat[valeur] = _resume(parties)
    return agregat


def _resume(parties: list[dict]) -> dict:
    valides = [d for d in parties if "erreur" not in d]
    repondues = [d for d in valides if not d["refus"]]
    return {
        "n": len(parties),
        "erreurs": len(parties) - len(valides),
        "recall": (sum(d["recall"] for d in valides) / len(valides)) if valides else 0.0,
        "mrr": (sum(d["mrr"] for d in valides) / len(valides)) if valides else 0.0,
        "taux_refus": (1 - len(repondues) / len(valides)) if valides else 0.0,
        "citations_verifiees_pct": (
            sum(1 for d in repondues if d["citations_verifiees"]) / len(repondues)
        )
        if repondues
        else 0.0,
        "duree_ms_mediane": _percentile([d["duree_ms"] for d in valides], 0.5),
    }


def principale() -> int:
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument(
        "--taille", type=int, default=0, help="taille de l'échantillon (0 = tout le jeu)"
    )
    analyseur.add_argument("--graine", type=int, default=42)
    analyseur.add_argument("--fournisseur", default="local", help="local | google | zai")
    analyseur.add_argument(
        "--fraiche", action="store_true", help="ignore le point de contrôle (repart de zéro)"
    )
    arguments = analyseur.parse_args()

    jeu = json.loads(FICHIER_QUESTIONS.read_text(encoding="utf-8"))
    questions = jeu["questions"]
    valider_url_bouclage(URL_CONVERSATION)

    if arguments.fraiche and POINT_DE_CONTROLE.exists():
        POINT_DE_CONTROLE.unlink()
    deja_mesures = _charger_deja_mesures()
    # Les questions restées en erreur transport sont rejouées à la reprise
    # (la dernière ligne du point de contrôle fait foi).
    deja_mesures = {qid: m for qid, m in deja_mesures.items() if "erreur" not in m}
    cible = [
        q for q in _echantillon_stratifie(questions, arguments.taille, arguments.graine)
        if q["id"] not in deja_mesures
    ]
    journal(
        f"jeu doré v{jeu['version']} — {len(cible)} question(s) à mesurer "
        f"({len(deja_mesures)} déjà en point de contrôle), fournisseur={arguments.fournisseur}"
    )

    entetes = {"Host": HOST_CONVERSATION, "Content-Type": "application/json"}
    detail: list[dict] = list(deja_mesures.values())
    debut_run = time.perf_counter()
    try:
        with httpx.Client(trust_env=False) as client:
            for numero, question in enumerate(cible, start=1):
                mesure = _mesurer_question(client, entetes, question, arguments.fournisseur)
                if "erreur" in mesure:
                    # Rattrapage léger : une erreur isolée (LM Studio
                    # saturé un instant) ne doit pas coûter la question.
                    journal(f"    erreur ({mesure['erreur'][:60]}) — nouvelle tentative dans 30 s")
                    time.sleep(30)
                    mesure = _mesurer_question(client, entetes, question, arguments.fournisseur)
                detail.append(mesure)
                with POINT_DE_CONTROLE.open("a", encoding="utf-8") as flot:
                    flot.write(json.dumps(mesure, ensure_ascii=False) + "\n")
                if mesure.get("erreur"):
                    marque = mesure["erreur"]
                elif mesure["refus"]:
                    marque = "REFUS " + (mesure.get("motif_refus") or "?")
                else:
                    marque = f"rang {mesure['rang_premier']}"
                ecoule = time.perf_counter() - debut_run
                reste = ecoule / numero * (len(cible) - numero)
                journal(
                    f"  [{numero}/{len(cible)}] {mesure['id']} [{mesure['langue']}/"
                    f"{mesure['famille']}] {marque} ({mesure.get('duree_ms', 0) // 1000} s ; "
                    f"reste estimé {reste / 60:.0f} min)"
                )
    except KeyboardInterrupt:
        journal("\nInterrompu — le point de contrôle conserve les questions mesurées.")

    evalues = [d for d in detail if "erreur" not in d]
    if not evalues:
        journal("aucune question mesurée")
        return 1
    motifs: dict[str, int] = {}
    for d in evalues:
        if d["refus"]:
            motifs[d["motif_refus"] or "inconnu"] = motifs.get(d["motif_refus"] or "inconnu", 0) + 1

    rapport = {
        "date": datetime.now(timezone.utc).isoformat(),
        "jeu_version": jeu["version"],
        "fournisseur": arguments.fournisseur,
        "graine": arguments.graine,
        "n_questions": len(detail),
        "erreurs_transport": len(detail) - len(evalues),
        "recall": sum(d["recall"] for d in evalues) / len(evalues),
        "mrr": sum(d["mrr"] for d in evalues) / len(evalues),
        "taux_refus": sum(1 for d in evalues if d["refus"]) / len(evalues),
        "refus_par_motif": motifs,
        "citations_rejetees_total": sum(d["citations_rejetees"] for d in evalues),
        "duree_ms_mediane": _percentile([d["duree_ms"] for d in evalues], 0.5),
        "duree_ms_p90": _percentile([d["duree_ms"] for d in evalues], 0.9),
        "par_langue": _agregat(evalues, "langue"),
        "par_famille": _agregat(evalues, "famille"),
        "detail": detail,
    }
    DOSSIER_RESULTATS.mkdir(parents=True, exist_ok=True)
    horodatage = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    chemin = DOSSIER_RESULTATS / f"bout-en-bout-{horodatage}-{arguments.fournisseur}.json"
    chemin.write_text(
        json.dumps(rapport, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    journal("")
    journal("=== Bout en bout sur le jeu doré (local) ===")
    journal(
        f"recall (attendus dans les sources de la réponse) : {rapport['recall']:.2%}   "
        f"MRR : {rapport['mrr']:.3f}"
    )
    journal(f"taux de refus : {rapport['taux_refus']:.2%} {motifs}")
    journal(
        f"durée médiane : {rapport['duree_ms_mediane'] / 1000:.0f} s ; "
        f"p90 : {rapport['duree_ms_p90'] / 1000:.0f} s"
    )
    for cle, valeurs in ("langue", rapport["par_langue"]), ("famille", rapport["par_famille"]):
        for valeur, resume in valeurs.items():
            journal(
                f"  {cle}={valeur} : recall {resume['recall']:.2%}, refus "
                f"{resume['taux_refus']:.2%}, médiane {resume['duree_ms_mediane'] / 1000:.0f} s"
            )
    journal(f"\nRapport : {chemin.relative_to(RACINE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(principale())
