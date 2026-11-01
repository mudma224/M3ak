"""Republie les décisions de justice dont l'indexation n'a pas abouti.

Les décisions sont déjà en couche brute et normalisée ; si leur
publication vers l'index a échoué (ex. caractères de contrôle refusés
par PostgreSQL, corrigés depuis dans le connecteur), l'outil relit le
JSON de la couche brute MinIO, reconstruit la charge ``DecisionAIndexer``
(texte assaini par la logique courante du connecteur) et la republie sur
la file dédiée. L'idempotence du consommateur (clé = decision_id) fait
que rejouer une décision déjà indexée ne crée jamais de doublon.

Les fiches à rejouer sont calculées par différence entre la base du
service corpus (source jurisprudence_ma) et la table ``decisions`` du
service recherche.

Usage :
    python tools/rejouer_decisions.py            # rejeu des fiches manquantes
    python tools/rejouer_decisions.py --dry-run  # liste sans publier
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
import subprocess
import sys
import urllib.parse
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "services" / "corpus"))

from app.connecteurs import jurisprudence
from app.messaging import BusRabbitMQ
from m3ak_contracts import FILE_DECISIONS_TO_INDEX, ChargeDecisionsAIndexer, envelopper

RACINE_PROJET = Path(__file__).resolve().parent.parent


def journal(message: str) -> None:
    print(message, flush=True)


def _psql(conteneur: str, utilisateur: str, base: str, requete: str) -> str:
    return subprocess.run(
        ["docker", "exec", conteneur, "psql", "-U", utilisateur, "-d", base,
         "-t", "-A", "-F", "\x1f", "-c", requete],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout


def url_rabbitmq() -> str:
    """Construit l'URL du courtier depuis infra/.env — hôte bouclage strict."""
    env = {}
    for ligne in (RACINE_PROJET / "infra" / ".env").read_text(encoding="utf-8").splitlines():
        if match := re.match(r"^(RABBITMQ_USER|RABBITMQ_PASSWORD)=(.*)$", ligne.strip()):
            env[match.group(1)] = match.group(2)
    if "RABBITMQ_USER" not in env or "RABBITMQ_PASSWORD" not in env:
        raise SystemExit("RABBITMQ_USER/PASSWORD introuvables dans infra/.env")
    url = (
        f"amqp://{urllib.parse.quote(env['RABBITMQ_USER'])}:"
        f"{urllib.parse.quote(env['RABBITMQ_PASSWORD'])}@127.0.0.1:5672/%2F"
    )
    partes = urllib.parse.urlparse(url)
    if partes.scheme != "amqp" or partes.username != env["RABBITMQ_USER"]:
        raise SystemExit("URL RabbitMQ inattendue")
    for information in socket.getaddrinfo(partes.hostname, partes.port or 5672):
        if not ipaddress.ip_address(information[4][0]).is_loopback:
            raise SystemExit("le courtier doit etre sur une adresse bouclage")
    return url


def brutes_minio(empreintes: list[str]) -> dict[str, bytes]:
    """Lecture des JSON de la couche brute — alias réinitialisé dans la
    même commande (il ne persiste pas d'une exécution à l'autre) ; un
    écho vide suit chaque cat : ``mc cat`` ne termine pas sa sortie par
    un saut de ligne, les JSON se colleraient au séparateur suivant."""
    sortie = subprocess.run(
        ["docker", "exec", "m3ak-minio", "sh", "-c",
         ('mc alias set local http://localhost:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" '
          '>/dev/null; for e in ' + " ".join(empreintes) + "; do "
          'echo "=== $e"; mc cat "local/m3ak-corpus-brut/jurisprudence/$e.json"; echo; done')],
        capture_output=True, check=True,
    ).stdout
    brutes: dict[str, bytes] = {}
    courant = None
    tampons: list[bytes] = []
    for ligne in sortie.split(b"\n"):
        if ligne.startswith(b"=== "):
            if courant is not None:
                brutes[courant] = b"\n".join(tampons)
            courant = ligne[4:].decode()
            tampons = []
        else:
            tampons.append(ligne)
    if courant is not None:
        brutes[courant] = b"\n".join(tampons)
    return brutes


def principale(dry_run: bool) -> int:
    lignes = _psql(
        "m3ak-corpus-db", "u_corpus", "db_corpus",
        "SELECT document_id, empreinte_sha256, langue FROM documents "
        "WHERE source = 'jurisprudence_ma' ORDER BY document_id",
    )
    corpus = {
        m[0]: {"empreinte": m[1], "langue": m[2]}
        for m in (ligne.split("\x1f", 2) for ligne in lignes.strip().splitlines())
        if len(m) == 3
    }
    indexes = set(
        _psql("m3ak-recherche-db", "u_recherche", "db_recherche",
              "SELECT document_id FROM decisions").strip().splitlines()
    )
    # Les décisions à langue indécidable ne sont jamais publiées par
    # conception : elles n'ont pas à être rejouées.
    manquants = {
        doc: infos
        for doc, infos in corpus.items()
        if doc not in indexes and infos["langue"] in ("ar", "fr")
    }
    journal(f"{len(corpus)} decisions en base corpus, {len(indexes)} indexees, {len(manquants)} a rejouer")
    if not manquants:
        return 0

    brutes = brutes_minio([infos["empreinte"] for infos in manquants.values()])
    if dry_run:
        for document_id in sorted(manquants):
            journal(f"  A_REJOUER {document_id}")
        return 0

    async def scenario() -> int:
        bus = BusRabbitMQ(url_rabbitmq(), "corpus-rejeu")
        await bus.connecter()
        await bus.declarer_file_travail(FILE_DECISIONS_TO_INDEX)
        publiees = 0
        try:
            for document_id in sorted(manquants):
                infos = manquants[document_id]
                brut = brutes.get(infos["empreinte"])
                if brut is None or not brut.strip():
                    journal(f"  SANS_BRUT {document_id} ({infos['empreinte'][:12]})")
                    continue
                charge = jurisprudence.construire_charge(json.loads(brut))
                charge["decision_id"] = document_id
                charge["document_id"] = document_id
                charge["langue"] = infos["langue"]
                enveloppe = envelopper(
                    ChargeDecisionsAIndexer(decisions=[charge]),
                    correlation_id=f"rejeu-decision-{infos['empreinte'][:12]}",
                    emis_par="corpus-rejeu",
                )
                await bus.publier(FILE_DECISIONS_TO_INDEX, enveloppe)
                publiees += 1
                journal(f"  REPUBLIE {document_id}")
        finally:
            await bus.fermer()
        return 0 if publiees == len(manquants) else 1

    return asyncio.run(scenario())


if __name__ == "__main__":
    import argparse

    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--dry-run", action="store_true")
    arguments = analyseur.parse_args()
    sys.exit(principale(arguments.dry_run))
