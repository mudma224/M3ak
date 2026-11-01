"""Couverture des collectes : ce que la source offre vs ce qui est déjà en base.

Pour cibler une collecte sans tirer à l'aveugle : par source, rapproche le
catalogue de la source (menu Idarati, thèmes Adala, référentiel
Jurisprudence.ma) des documents réellement en base, et affiche ce qui
manque — les identifiants affichés se recopient tels quels dans
``tools/piloter_collecte.py``.

Sources d'information (aucune écriture, aucune collecte) :
- idarati        : menu des thématiques (endpoint interne du corpus) vs
                   ``fiches.thematique_id`` (base du service recherche) ;
                   ``--tailles`` interroge en plus la source pour compter les
                   fiches disponibles des thématiques jamais collectées
                   (poli ~1,2 s par requête).
- jurisprudence  : référentiel figé ``pilot/jurisprudence_volumetrie.json``
                   vs ``decisions.thematiques`` (base du service recherche).
- adala          : thèmes et volumes du jour (endpoint interne) vs total
                   collecté — le détail par thème n'est pas tracé en base ;
                   la collecte par thème étant idempotente, la relancer ne
                   ramène que les manquants.

Usage :
    python tools/couverture_collectes.py                     # les trois sources
    python tools/couverture_collectes.py --source idarati    # une seule
    python tools/couverture_collectes.py --source idarati --tailles
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

RACINE = Path(__file__).resolve().parent.parent
BUCKET = None  # pas d'accès MinIO ici : la couverture lit les bases et les endpoints
HOTE_IDARATI = "procedures.idarati.ma"
DELAI_SECONDES = 1.2
AGENT = "M3ak-outillage/1.0 (couverture des collectes; contact: porteur M3ak)"


def journal(message: str) -> None:
    print(message, flush=True)


def jeton_interne() -> str:
    for ligne in (RACINE / "infra" / ".env").read_text(encoding="utf-8").splitlines():
        if match := re.match(r"^INTERNAL_TOKEN=(.*)$", ligne.strip()):
            return match.group(1)
    raise SystemExit("INTERNAL_TOKEN introuvable dans infra/.env")


def _docker() -> str:
    """Le client docker — PATH d'abord, sinon l'installation « par
    utilisateur » de Docker Desktop : les mises à jour récentes ne
    l'ajoutent plus au PATH (constaté 2026-10-13)."""
    if shutil.which("docker"):
        return "docker"
    candidat = (
        Path.home() / "AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe"
    )
    if candidat.exists():
        return str(candidat)
    return "docker"


def _psql(conteneur: str, utilisateur: str, base: str, requete: str) -> str:
    return subprocess.run(
        [_docker(), "exec", conteneur, "psql", "-U", utilisateur, "-d", base,
         "-t", "-A", "-F", "\x1f", "-c", requete],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout


def _client_interne() -> httpx.Client:
    return httpx.Client(
        base_url="http://127.0.0.1", timeout=60, trust_env=False,
        headers={"Host": "corpus.m3ak.localhost", "X-Internal-Token": jeton_interne()},
    )


def _endpoint_interne(chemin: str) -> dict:
    """Un endpoint interne du service corpus — message clair si la stack
    est arrêtée (l'outil lit des données vivantes, il n'en produit pas)."""
    try:
        with _client_interne() as client:
            reponse = client.get(chemin)
            reponse.raise_for_status()
            return reponse.json()
    except httpx.HTTPError as erreur:
        raise SystemExit(
            f"endpoint corpus injoignable ({chemin}) — la stack est-elle "
            f"lancée ? (docker compose -f infra/docker-compose.yml up -d) "
            f"detail : {erreur}"
        )


# ── Idarati ──────────────────────────────────────────────────────────────

def couverture_idarati(tailles: bool, limite_tailles: int) -> int:
    menu = _endpoint_interne("/internal/connecteurs/idarati/thematiques").get(
        "thematiques", []
    )
    lignes = _psql(
        "m3ak-recherche-db", "u_recherche", "db_recherche",
        "SELECT thematique_id, count(*) FROM fiches GROUP BY thematique_id",
    )
    collecte = {
        m[0]: int(m[1])
        for m in (ligne.split("\x1f", 1) for ligne in lignes.strip().splitlines())
        if len(m) == 2
    }

    entrees = [
        {"id": t.get("id") or "", "titre": t.get("titre") or "",
         "public": t.get("public") or "", "collecte": collecte.get(t.get("id") or "", 0)}
        for t in menu
    ]
    # La même thématique se répète sous plusieurs publics : déduplication
    # par identifiant (le compte collecté est porté par l'identifiant).
    par_id: dict[str, dict] = {}
    for entree in entrees:
        groupe = par_id.setdefault(
            entree["id"],
            {"id": entree["id"], "titre": entree["titre"],
             "publics": [], "collecte": entree["collecte"]},
        )
        groupe["publics"].append(entree["public"])
    thematiques = list(par_id.values())
    restantes = [e for e in thematiques if e["collecte"] == 0]
    entamees = sorted(
        (e for e in thematiques if e["collecte"] > 0), key=lambda e: -e["collecte"]
    )

    tailles_dispo: dict[str, int] = {}
    if tailles and restantes:
        cibles = restantes[: limite_tailles] if limite_tailles > 0 else restantes
        journal(f"interrogation de la source pour {len(cibles)} thématique(s)…")
        source = httpx.Client(
            headers={"User-Agent": AGENT}, timeout=30,
            follow_redirects=False, trust_env=False,
        )
        with source:
            for rang, entree in enumerate(cibles, start=1):
                try:
                    reponse = source.get(
                        f"https://{HOTE_IDARATI}/api/informational/procedures/search",
                        params={"title": "", "thematicId": entree["id"],
                                "page": 0, "size": 1},
                    )
                    reponse.raise_for_status()
                    tailles_dispo[entree["id"]] = int(
                        reponse.json().get("totalElements") or 0
                    )
                except Exception as erreur:  # noqa: BLE001 — consigné, on poursuit
                    journal(f"  echec taille {entree['id'][:8]} : {str(erreur)[:80]}")
                if rang % 20 == 0:
                    journal(f"  {rang}/{len(cibles)}…")
                time.sleep(DELAI_SECONDES)

    journal(f"\n=== IDARATI — {len(menu)} entrées au menu, {len(thematiques)} thématiques "
            f"distinctes : {len(restantes)} jamais collectées, {len(entamees)} entamées")
    journal("\n-- JAMAIS COLLECTÉES (0 fiche en base) — ids prêts pour piloter_collecte --")
    for entree in sorted(restantes, key=lambda e: (-(tailles_dispo.get(e["id"], 0)), e["titre"])):
        taille = tailles_dispo.get(entree["id"])
        disponible = f" | dispo: {taille}" if taille is not None else ""
        journal(f"  {entree['id']}  {entree['titre']} "
                f"[{' · '.join(entree['publics'])}]{disponible}")
    journal("\n-- ENTAMÉES (fiches en base) --")
    for entree in entamees:
        journal(f"  {entree['collecte']:4d} fiches | {entree['titre']} "
                f"[{' · '.join(entree['publics'])}] | {entree['id']}")
    journal(
        "\ncollecter : python tools/piloter_collecte.py --connecteur idarati "
        "--thematiques \"ID1,ID2\" --limite N"
    )
    return 0


# ── Jurisprudence.ma ─────────────────────────────────────────────────────

def couverture_jurisprudence() -> int:
    chemin_referentiel = RACINE / "pilot" / "jurisprudence_volumetrie.json"
    if not chemin_referentiel.exists():
        journal(
            "\n=== JURISPRUDENCE.MA — référentiel absent : pilot/ est un espace "
            "de travail interne, non versionné. Le fichier "
            "jurisprudence_volumetrie.json est produit par la passe de "
            "volumétrie du connecteur jurisprudence."
        )
        return 0
    referentiel = json.loads(chemin_referentiel.read_text(encoding="utf-8"))
    dispo: dict[str, int] = {str(k): int(v) for k, v in referentiel.get("themes", {}).items()}
    lignes = _psql(
        "m3ak-recherche-db", "u_recherche", "db_recherche",
        "SELECT thematiques FROM decisions WHERE thematiques IS NOT NULL",
    )
    collecte: dict[str, int] = {}
    for ligne in lignes.strip().splitlines():
        try:
            for slug in json.loads(ligne):
                collecte[slug] = collecte.get(slug, 0) + 1
        except json.JSONDecodeError:
            continue

    slugs = sorted(set(dispo) | set(collecte), key=lambda s: -(dispo.get(s, 0) - collecte.get(s, 0)))
    journal(f"\n=== JURISPRUDENCE.MA — {len(dispo)} thèmes au référentiel "
            f"(figé {referentiel.get('date', '?')}), {len(collecte)} thèmes entamés")
    journal("\n-- RESTANT (disponible − collecté, indicatif : une décision peut porter plusieurs thèmes) --")
    for slug in slugs:
        d, c = dispo.get(slug, 0), collecte.get(slug, 0)
        restant = max(d - c, 0)
        etat = "complet" if (d and c >= d) else f"restant {restant}"
        if c or d:
            journal(f"  {slug:42s} dispo {d:5d} | collecté {c:5d} | {etat}")
    journal(
        "\ncollecter : python tools/piloter_collecte.py --connecteur jurisprudence "
        "--thematiques \"SLUG1,SLUG2\" --limites '{\"SLUG1\": 200}'"
    )
    return 0


# ── Adala ────────────────────────────────────────────────────────────────

def couverture_adala() -> int:
    themes = _endpoint_interne("/internal/connecteurs/adala/themes").get(
        "themes", []
    )
    total = _psql(
        "m3ak-corpus-db", "u_corpus", "db_corpus",
        "SELECT count(*) FROM documents WHERE source = 'adala'",
    ).strip()
    journal(f"\n=== ADALA — {len(themes)} thèmes, {total} documents collectés (tous thèmes)")
    journal("\n-- THÈMES ET VOLUMES DU JOUR (le détail par thème n'est pas tracé en base ; "
            "la collecte par thème est idempotente : relancer ne ramène que les manquants) --")
    for theme in sorted(themes, key=lambda t: int(t.get("documents") or 0)):
        journal(f"  theme {theme.get('cle'):>3} | dispo {theme.get('documents'):>4} "
                f"| {theme.get('nom')}")
    journal(
        "\ncollecter : python tools/piloter_collecte.py --connecteur adala "
        "--theme CLE --limite N"
    )
    return 0


def principale() -> int:
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument(
        "--source", choices=["idarati", "jurisprudence", "adala", "tout"], default="tout",
    )
    analyseur.add_argument(
        "--tailles", action="store_true",
        help="idarati : interroge la source pour compter les fiches disponibles "
             "des thématiques jamais collectées (~1,2 s par requête)",
    )
    analyseur.add_argument(
        "--limite-tailles", type=int, default=0,
        help="bornage des interrogations de taille (0 = toutes)",
    )
    arguments = analyseur.parse_args()

    if arguments.source in ("idarati", "tout"):
        couverture_idarati(arguments.tailles, arguments.limite_tailles)
    if arguments.source in ("jurisprudence", "tout"):
        couverture_jurisprudence()
    if arguments.source in ("adala", "tout"):
        couverture_adala()
    return 0


if __name__ == "__main__":
    sys.exit(principale())
