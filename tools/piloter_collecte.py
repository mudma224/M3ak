"""Lance une collecte (Adala, Idarati ou Jurisprudence.ma) et la suit
jusqu'à son terme.

Poste la demande d'exécution au service corpus puis interroge l'état à
intervalle régulier : le journal borné du connecteur trace la progression
(statuts cumulés + dernières lignes). Une seule collecte à la fois côté
service par connecteur (409 si une autre tourne).

Usage :
    python tools/piloter_collecte.py --connecteur adala --theme 103
    python tools/piloter_collecte.py --connecteur idarati \\
        --thematiques <id1,id2,…>        # ids figés dans pilot/idarati_pilote.json
    python tools/piloter_collecte.py --connecteur jurisprudence \\
        --thematiques procedure-civile,commercial \\
        --limites '{"procedure-civile": 1000}'
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
INTERVALLE_SECONDES = 60


def journal(message: str) -> None:
    print(message, flush=True)


def jeton_interne() -> str:
    for ligne in (RACINE / "infra" / ".env").read_text(encoding="utf-8").splitlines():
        if match := re.match(r"^INTERNAL_TOKEN=(.*)$", ligne.strip()):
            return match.group(1)
    raise SystemExit("INTERNAL_TOKEN introuvable dans infra/.env")


def charge_collecte(connecteur: str, arguments) -> dict:
    if connecteur == "adala":
        return {"theme": arguments.theme, "limite": arguments.limite, "dry_run": arguments.dry_run}
    if not arguments.thematiques:
        raise SystemExit(f"--thematiques requis pour le connecteur {connecteur}")
    charge = {
        "thematiques": [t.strip() for t in arguments.thematiques.split(",") if t.strip()],
        "dry_run": arguments.dry_run,
    }
    if connecteur == "idarati":
        charge["limite"] = arguments.limite
    if connecteur == "jurisprudence":
        charge["limites"] = json.loads(arguments.limites) if arguments.limites else {}
    return charge


def principale(connecteur: str, arguments) -> int:
    jeton = jeton_interne()
    entetes = {"Host": "corpus.m3ak.localhost", "X-Internal-Token": jeton}
    chemin = f"/internal/connecteurs/{connecteur}/executer"
    chemin_statut = f"/internal/connecteurs/{connecteur}/statut"
    with httpx.Client(
        base_url="http://127.0.0.1", timeout=60, trust_env=False
    ) as client:
        lancement = client.post(chemin, headers=entetes, json=charge_collecte(connecteur, arguments))
        if lancement.status_code == 409:
            journal("une collecte est deja en cours — on suit celle-la")
        elif lancement.status_code != 200:
            journal(f"ERREUR lancement — HTTP {lancement.status_code} {lancement.text[:200]}")
            return 1
        else:
            journal(f"collecte demarree : {lancement.text[:200]}")

        while True:
            time.sleep(INTERVALLE_SECONDES)
            etat = client.get(chemin_statut, headers=entetes)
            if etat.status_code != 200:
                journal(f"ERREUR statut — HTTP {etat.status_code}")
                return 1
            corps = etat.json()
            statuts = json.dumps(corps["statuts"], ensure_ascii=False)
            if corps["en_cours"]:
                journal(f"en cours : {statuts}")
                continue
            journal(f"terminee : {statuts}")
            for ligne in corps["journal"][-5:]:
                journal(f"  {ligne}")
            return 0


if __name__ == "__main__":
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--connecteur", choices=["adala", "idarati", "jurisprudence"], default="adala")
    analyseur.add_argument("--theme", type=int, help="adala : identifiant du theme")
    analyseur.add_argument("--thematiques", help="idarati/jurisprudence : ids ou slugs separes par des virgules")
    analyseur.add_argument("--limites", help="jurisprudence : JSON {slug: borne}")
    analyseur.add_argument("--limite", type=int, default=1000)
    analyseur.add_argument("--dry-run", action="store_true")
    arguments = analyseur.parse_args()
    if arguments.connecteur == "adala" and arguments.theme is None:
        raise SystemExit("--theme requis pour le connecteur adala")
    sys.exit(principale(arguments.connecteur, arguments))
