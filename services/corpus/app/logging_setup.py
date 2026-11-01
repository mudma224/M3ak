"""Journalisation structurée JSON + contexte de corrélation."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar

# Identifiant de corrélation de la requête en cours (propagé inter-services).
correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")


class FormateurJSON(logging.Formatter):
    """Un log = une ligne JSON, parsable par n'importe quel agrégateur."""

    def format(self, record: logging.LogRecord) -> str:
        charge = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "niveau": record.levelname,
            "service": "corpus",
            "correlation_id": correlation_id.get(),
            "message": record.getMessage(),
        }
        if record.exc_info:
            charge["exception"] = self.formatException(record.exc_info)
        return json.dumps(charge, ensure_ascii=False)


def configurer_logging(niveau: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(FormateurJSON())
    racine = logging.getLogger()
    racine.handlers = [handler]
    racine.setLevel(niveau.upper())
