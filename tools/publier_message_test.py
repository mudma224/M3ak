"""Démo de résilience RabbitMQ : un message valide consommé, un invalide en DLQ.

Usage (après docker compose up, depuis la racine du dépôt) :

    python tools/publier_message_test.py \
        --url amqp://UTILISATEUR:MOTDEPASSE@127.0.0.1:5672/%2F \
        --management http://UTILISATEUR:MOTDEPASSE@127.0.0.1:15672

Les identifiants sont ceux du fichier infra/.env (RABBITMQ_USER / RABBITMQ_PASSWORD).
Attendu : le message valide est consommé par le service recherche (visible
dans ses journaux), le message invalide atterrit dans segments.to_index.dlq.

Par mesure de sécurité (SSRF), les URLs cibles sont limitées aux adresses
de bouclage locales ; toute autre destination exige --hote-distant et doit
être pleinement assumée par l'opérateur.
"""

from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

import aio_pika

ECHANGE = "m3ak"
FILE_TRAVAIL = "segments.to_index"

HOTES_LOCAUX = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _hote_est_local(nom_hote: str) -> bool:
    """Vrai si le nom (et toutes ses adresses résolues) est de bouclage."""
    if nom_hote in HOTES_LOCAUX:
        return True
    try:
        adresses = socket.getaddrinfo(nom_hote, None)
    except socket.gaierror:
        return False
    return all(
        ipaddress.ip_address(info[4][0]).is_loopback for info in adresses
    )


def valider_url(url: str, schemas_autorises: set[str], hote_distant_autorise: bool) -> urllib.parse.SplitResult:
    """Valide protocole et destination d'une URL avant toute requête sortante.

    Blocages : schéma non autorisé, hôte absent, hôte non local sans
    autorisation explicite, identifiants absents (l'API RabbitMQ les exige).
    """
    decoupage = urllib.parse.urlsplit(url)
    if decoupage.scheme not in schemas_autorises:
        raise ValueError(f"schema non autorise : {decoupage.scheme!r} (attendu : {sorted(schemas_autorises)})")
    nom_hote = decoupage.hostname or ""
    if not nom_hote:
        raise ValueError("hote absent de l'URL")
    if not hote_distant_autorise and not _hote_est_local(nom_hote):
        raise ValueError(
            f"hote non local refuse : {nom_hote!r} — utiliser --hote-distant en connaissance de cause"
        )
    if not decoupage.username or decoupage.password is None:
        raise ValueError("identifiants attendus dans l'URL (utilisateur:motdepasse@hote)")
    return decoupage

SEGMENT_VALIDE = {
    "segment_id": "loi-49-16|fr|originale#art-4",
    "document_id": "loi-49-16|fr|originale",
    "articles": ["4"],
    "texte": "Article 4 — extrait de démonstration de la loi n° 49-16.",
    "langue": "fr",
    "contexte": {
        "texte_identifiant": "loi-49-16|fr|originale",
        "texte_titre": "Loi n° 49-16 relative aux baux commerciaux",
    },
}

MESSAGE_VALIDE = {
    "schema_version": "1.0",
    "correlation_id": "demo-valide",
    "emis_le": datetime.now(UTC).isoformat(),
    "emis_par": "outils",
    "type_message": "segments_a_indexer",
    "charge": {"type_message": "segments_a_indexer", "segments": [SEGMENT_VALIDE]},
}

# Charge vide : viole min_length=1 de ChargeSegmentsAIndexer -> doit partir en DLQ.
MESSAGE_INVALIDE = {
    "schema_version": "1.0",
    "correlation_id": "demo-invalide",
    "emis_par": "outils",
    "type_message": "segments_a_indexer",
    "charge": {"type_message": "segments_a_indexer", "segments": []},
}


async def publier(url: str) -> None:
    connexion = await aio_pika.connect_robust(url)
    canal = await connexion.channel()
    echange = await canal.get_exchange(ECHANGE)
    for etiquette, charge in [("valide", MESSAGE_VALIDE), ("invalide", MESSAGE_INVALIDE)]:
        message = aio_pika.Message(
            body=json.dumps(charge).encode("utf-8"),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            content_type="application/json",
        )
        await echange.publish(message, routing_key=FILE_TRAVAIL)
        print(f"publie : message {etiquette} (correlation_id={charge['correlation_id']})")
    await connexion.close()


class _SansRedirection(urllib.request.HTTPRedirectHandler):
    """L'API de gestion RabbitMQ ne redirige jamais : toute redirection est suspecte."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.URLError(f"redirection refusee (code {code}) vers {newurl}")


_OUVERTURE_SANS_REDIRECTION = urllib.request.build_opener(_SansRedirection())


def profondeur_file(base_gestion: urllib.parse.SplitResult, file: str) -> int:
    # URL sans identifiants (urllib ne les parse pas dans le host) :
    # l'authentification passe par l'en-tête Authorization ci-dessous.
    hote_port = base_gestion.netloc.rpartition("@")[2]
    requete = urllib.request.Request(
        f"{base_gestion.scheme}://{hote_port}/api/queues/%2F/{urllib.parse.quote(file, safe='')}"
    )
    authentification = base64.b64encode(
        f"{base_gestion.username}:{base_gestion.password}".encode()
    ).decode()
    requete.add_header("Authorization", f"Basic {authentification}")
    with _OUVERTURE_SANS_REDIRECTION.open(requete, timeout=10) as reponse:
        return json.load(reponse).get("messages", 0)


def main() -> int:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--url", required=True, help="URL AMQP (amqp://...@127.0.0.1:5672/%2F)")
    parseur.add_argument("--management", required=True, help="URL API de gestion (http://...@127.0.0.1:15672)")
    parseur.add_argument(
        "--hote-distant",
        action="store_true",
        help="autoriser explicitement un hote non local (desactive la protection SSRF par defaut)",
    )
    arguments = parseur.parse_args()

    import asyncio

    try:
        url_amqp = valider_url(arguments.url, {"amqp", "amqps"}, arguments.hote_distant)
        base_gestion = valider_url(arguments.management, {"http", "https"}, arguments.hote_distant)
    except ValueError as erreur:
        print(f"ERREUR de validation d'URL : {erreur}", file=sys.stderr)
        return 2

    asyncio.run(publier(arguments.url))
    _ = url_amqp  # validation effectuée : la connexion AMQP utilise l'URL validée ci-dessus

    print("attente de 3 secondes pour la consommation...")
    import time

    time.sleep(3)

    trav = profondeur_file(base_gestion, FILE_TRAVAIL)
    dlq = profondeur_file(base_gestion, FILE_TRAVAIL + ".dlq")
    print(f"profondeur {FILE_TRAVAIL} = {trav} (attendu 0)")
    print(f"profondeur {FILE_TRAVAIL}.dlq = {dlq} (attendu >= 1)")

    if trav == 0 and dlq >= 1:
        print("SUCCES : consommation et routage vers la DLQ fonctionnels.")
        return 0
    print("ECHEC : vérifier les journaux du service recherche.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
