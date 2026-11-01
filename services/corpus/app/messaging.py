"""Enrobage du courtier RabbitMQ pour le service corpus.

Sémantique : livraison at-least-once (consommateurs idempotents requis),
files quorum durables, dead-letter exchange sur chaque file de travail,
publication avec confirmation.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

import aio_pika
from pydantic import BaseModel, ValidationError

from .logging_setup import correlation_id

logger = logging.getLogger(__name__)

EXCHANGE_PRINCIPAL = "m3ak"
EXCHANGE_LETTRES_MORTES = "m3ak.dlx"

TypeMessage = TypeVar("TypeMessage", bound=BaseModel)


class BusRabbitMQ:
    """Connexion robuste au courtier ; à fermer à l'arrêt du service."""

    def __init__(self, url: str, service: str) -> None:
        self._url = url
        self._service = service
        self._connexion = None
        self._canal = None

    @property
    def connecte(self) -> bool:
        return self._connexion is not None and not self._connexion.is_closed

    async def connecter(self) -> None:
        self._connexion = await aio_pika.connect_robust(self._url)
        self._canal = await self._connexion.channel()
        await self._canal.set_qos(prefetch_count=10)
        await self._canal.declare_exchange(
            EXCHANGE_PRINCIPAL, aio_pika.ExchangeType.DIRECT, durable=True
        )
        await self._canal.declare_exchange(
            EXCHANGE_LETTRES_MORTES, aio_pika.ExchangeType.DIRECT, durable=True
        )
        logger.info("bus rabbitmq connecte (service=%s)", self._service)

    async def declarer_file_travail(self, nom_file: str) -> None:
        """Déclare la file de travail et sa dead-letter queue, de façon idempotente."""
        assert self._canal is not None
        echange = await self._canal.get_exchange(EXCHANGE_PRINCIPAL)
        echange_dlq = await self._canal.get_exchange(EXCHANGE_LETTRES_MORTES)
        file = await self._canal.declare_queue(
            nom_file,
            durable=True,
            arguments={
                "x-queue-type": "quorum",
                "x-dead-letter-exchange": EXCHANGE_LETTRES_MORTES,
                "x-dead-letter-routing-key": nom_file + ".dlq",
            },
        )
        file_dlq = await self._canal.declare_queue(
            nom_file + ".dlq", durable=True, arguments={"x-queue-type": "quorum"}
        )
        await file.bind(echange, routing_key=nom_file)
        await file_dlq.bind(echange_dlq, routing_key=nom_file + ".dlq")

    async def publier(self, cle_routage: str, enveloppe: BaseModel) -> None:
        assert self._canal is not None
        echange = await self._canal.get_exchange(EXCHANGE_PRINCIPAL)
        message = aio_pika.Message(
            body=enveloppe.model_dump_json().encode("utf-8"),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=getattr(enveloppe, "message_id", None),
            correlation_id=getattr(enveloppe, "correlation_id", None),
            content_type="application/json",
            type=getattr(enveloppe, "type_message", None),
        )
        await echange.publish(message, routing_key=cle_routage)

    async def consommer(
        self,
        nom_file: str,
        classe_message: type[TypeMessage],
        traiter: Callable[[TypeMessage], Awaitable[None]],
    ) -> None:
        """Consomme une file ; tout message invalide ou en échec part en DLQ."""
        assert self._canal is not None
        file = await self._canal.get_queue(nom_file)

        async def _sur_message(brut: aio_pika.IncomingMessage) -> None:
            async with brut.process(requeue=False):
                try:
                    enveloppe = classe_message.model_validate_json(brut.body)
                except ValidationError as erreur:
                    logger.error("message invalide -> DLQ %s.dlq : %s", nom_file, erreur)
                    raise
                jeton = correlation_id.set(getattr(enveloppe, "correlation_id", "-"))
                try:
                    await traiter(enveloppe)
                finally:
                    correlation_id.reset(jeton)

        await file.consume(_sur_message)
        logger.info("consommation demarree sur la file %s", nom_file)

    async def fermer(self) -> None:
        if self._connexion is not None:
            await self._connexion.close()
            self._connexion = None
            self._canal = None
