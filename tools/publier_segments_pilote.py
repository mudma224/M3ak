"""Publication du corpus pilote vers la file segments.to_index (jalon B).

Dernier maillon de la chaîne de bout en bout : les segments produits par
la grammaire de segmentation (pilot/segments/) sont normalisés au contrat
SegmentArticle (services/corpus/app/normalisation.py) puis publiés sur la
file RabbitMQ consommée par le service recherche, qui les persiste de
façon idempotente (clé = segment_id).

Le harnais émet en son nom (emis_par="corpus-pilote") : le service corpus
publiera de la même façon en phase 1, avec les mêmes enveloppes.

Usage :
    python tools/publier_segments_pilote.py            # tout le pilote
    python tools/publier_segments_pilote.py --dry-run  # montrer sans publier
    python tools/publier_segments_pilote.py --cles A1,C2
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import ipaddress
import json
import re
import socket
import sys
import urllib.parse
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "services" / "recherche"))

from app.messaging import BusRabbitMQ
from m3ak_contracts import (
    FILE_SEGMENTS_TO_INDEX,
    ChargeSegmentsAIndexer,
    envelopper,
)

# Le module de normalisation vit dans le package du service corpus ; les
# deux services exposant un package « app », il est chargé par chemin.
_spec = importlib.util.spec_from_file_location(
    "normalisation_corpus", RACINE / "services" / "corpus" / "app" / "normalisation.py"
)
if _spec is None or _spec.loader is None:
    raise ImportError("impossible de charger le module de normalisation du service corpus")
normalisation = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(normalisation)

FICHIER_LISTE = RACINE / "pilot" / "corpus_pilote.json"
DOSSIER_SEGMENTS = RACINE / "pilot" / "segments"
TAILLE_LOT = 100


def journal(message: str) -> None:
    print(message, flush=True)


def url_rabbitmq() -> str:
    """Construit l'URL du courtier depuis infra/.env — hôte bouclage strict."""
    env = {}
    chemin_env = RACINE / "infra" / ".env"
    for ligne in chemin_env.read_text(encoding="utf-8").splitlines():
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


async def principale(dry_run: bool, cibles: list[str] | None) -> int:
    liste = json.loads(FICHIER_LISTE.read_text(encoding="utf-8"))
    documents = [d for d in liste["documents"] if not cibles or d["cle"] in cibles]

    total_segments = 0
    total_lots = 0
    bus = None
    if not dry_run:
        bus = BusRabbitMQ(url_rabbitmq(), "corpus-pilote")
        await bus.connecter()
        await bus.declarer_file_travail(FILE_SEGMENTS_TO_INDEX)

    try:
        for document in documents:
            chemin_segments = DOSSIER_SEGMENTS / f"{document['cle']}.json"
            if not chemin_segments.exists():
                journal(f"  IGNORE {document['cle']} — pas de fichier {chemin_segments.name}")
                continue
            segmentation = json.loads(chemin_segments.read_text(encoding="utf-8"))
            lots = normalisation.construire_lot(document, segmentation, taille_lot=TAILLE_LOT)
            publiables = sum(len(lot) for lot in lots)
            exclus = len(segmentation["articles"]) - publiables
            for lot in lots:
                total_lots += 1
                total_segments += len(lot)
                if dry_run:
                    continue
                enveloppe = envelopper(
                    ChargeSegmentsAIndexer(segments=lot),
                    correlation_id="pilote-jalon-b",
                    emis_par="corpus-pilote",
                )
                if bus is None:
                    raise RuntimeError("le bus RabbitMQ n'est pas initialise")
                await bus.publier(FILE_SEGMENTS_TO_INDEX, enveloppe)
            journal(
                f"  {'PRET' if dry_run else 'PUBLIE'} {document['cle']} {document['intitule']} — "
                f"{publiables} segments en {len(lots)} lot(s)"
                + (f" ({exclus} vides exclus)" if exclus else "")
            )
    finally:
        if bus is not None:
            await bus.fermer()

    journal(
        f"termine : {len(documents)} documents, {total_segments} segments, {total_lots} messages"
    )
    return 0


if __name__ == "__main__":
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--dry-run", action="store_true", help="montrer sans publier")
    analyseur.add_argument("--cles", help="sous-ensemble de clés pilotes (A1,C2)")
    arguments = analyseur.parse_args()
    cibles = arguments.cles.split(",") if arguments.cles else None
    sys.exit(asyncio.run(principale(arguments.dry_run, cibles)))
