"""Générateur des squelettes de services M3ak (phase 0, jalon A).

Crée les six services à partir d'un modèle commun, avec les variations
propres à chaque rôle. Usage (une fois, depuis la racine du dépôt) :

    python tools/scaffold_services.py

Ce script est conservé comme trace de la génération initiale ; les services
générés sont ensuite la source de vérité (on ne le relance pas par-dessus).
"""

from __future__ import annotations

from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
SERVICES = RACINE / "services"

# name: (prefixe_env, port, host_traefik, possede_base, role_messagerie)
MANIFEST = {
    "corpus": ("CORPUS", 8001, "corpus.m3ak.localhost", True, "publisher"),
    "recherche": ("RECHERCHE", 8002, "recherche.m3ak.localhost", True, "consumer"),
    "conversation": ("CONVERSATION", 8003, "conversation.m3ak.localhost", True, "aucun"),
    "documents": ("DOCUMENTS", 8004, "documents.m3ak.localhost", True, "aucun"),
    "llm": ("LLM", 8005, "llm.m3ak.localhost", True, "aucun"),
    "whatsapp": ("WHATSAPP", 8006, "whatsapp.m3ak.localhost", False, "aucun"),
}

# ═══════════════════════════ Modèles de fichiers ═══════════════════════════


def pyproject(name: str, possede_base: bool) -> str:
    deps_bdd = (
        '  "sqlalchemy>=2.0",\n  "psycopg[binary]>=3.1",\n' if possede_base else ""
    )
    return f'''[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[project]
name = "m3ak-service-{name}"
version = "0.1.0"
description = "Service {name} de la plateforme M3ak"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "pydantic>=2.7",
  "pydantic-settings>=2.2",
{deps_bdd}  "aio-pika>=9.4",
  "httpx>=0.27",
  "m3ak-contracts>=0.1.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "ruff>=0.4"]

[project.scripts]
{name} = "app.__main__:main"

[tool.setuptools.packages.find]
include = ["app*"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint.per-file-ignores]
# /ready doit signaler une base indisponible plutôt que de lever une exception.
"app/db.py" = ["BLE001"]
'''


def dockerfile(name: str, port: int) -> str:
    return f'''# Image du service {name} — python 3.12-slim, utilisateur non-root.
# Contexte de build : racine du dépôt (pour inclure le paquet contracts).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \\
    PIP_NO_CACHE_DIR=1 \\
    PYTHONDONTWRITEBYTECODE=1

RUN groupadd --system app && useradd --system --gid app --create-home app

WORKDIR /opt/service

# Contrats partagés d'abord (mise en cache de couche)
COPY contracts /opt/contracts
RUN pip install /opt/contracts

# Puis le service lui-même
COPY services/{name}/pyproject.toml ./pyproject.toml
COPY services/{name}/app ./app
RUN pip install .

USER app
EXPOSE {port}

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \\
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:{port}/health')"

CMD ["python", "-m", "app"]
'''


CONFIG = '''"""Configuration du service __NAME__ — préfixe d'environnement __PREFIX___."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Toute la configuration arrive par variables d'environnement préfixées.

    Aucun secret n'est écrit dans le code : les valeurs sensibles vivent
    dans le fichier infra/.env (voir infra/.env.example).
    """

    model_config = SettingsConfigDict(env_prefix="__PREFIX___", extra="ignore")

    service_name: str = "__NAME__"
    port: int = __PORT__
    database_url: str | None = None  # injectée par compose ; None = sans base
    internal_token: str = "dev-jeton-a-remplacer"
    rabbitmq_url: str | None = "amqp://m3ak:m3ak@rabbitmq:5672/%2F"
    log_level: str = "INFO"
    environment: str = "development"
__CHAMPS_ROLE__


@lru_cache
def get_settings() -> Settings:
    return Settings()
'''

LOGGING = '''"""Journalisation structurée JSON + contexte de corrélation."""

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
            "service": "__NAME__",
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
'''

SECURITY = '''"""Authentification des appels internes par jeton partagé.

Le réseau Docker isole déjà les services entre eux ; le jeton empêche
l'erreur humaine (un appel oublié, un environnement mal configuré).
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from .config import get_settings


async def exiger_jeton_interne(
    x_internal_token: str | None = Header(default=None),
) -> str:
    attendu = get_settings().internal_token
    if not x_internal_token or not secrets.compare_digest(x_internal_token, attendu):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Jeton interne manquant ou invalide",
        )
    return x_internal_token
'''

DB = '''"""Accès exclusif à LA base du service __NAME__.

Principe database-per-service : ce module est le seul point d'entrée vers
la base ; aucun autre service ne connaît ces identifiants.
"""

from __future__ import annotations

from sqlalchemy import Column, MetaData, String, Table, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

_metadata = MetaData()
_table_service_meta = Table(
    "service_meta",
    _metadata,
    Column("cle", String, primary_key=True),
    Column("valeur", String, nullable=False),
)

_engine: AsyncEngine | None = None


async def initialiser_base(url: str) -> None:
    """Crée le moteur et la table méta minimale du service."""
    global _engine
    _engine = create_async_engine(url, pool_pre_ping=True)
    async with _engine.begin() as conn:
        await conn.run_sync(_metadata.create_all)


async def verifier_base() -> bool:
    """Vrai si la base répond (utilisé par /ready).

    La connexion + une requête scalaire passent par run_sync (API publique
    SQLAlchemy) : cela valide à la fois la joignabilité et l'authenticité
    des identifiants du service.
    """
    if _engine is None:
        return False
    try:
        async with _engine.connect() as conn:
            await conn.run_sync(lambda session: session.scalar(select(1)))
        return True
    except Exception:
        return False


async def fermer_base() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
'''

DB_SANS_BASE = '''"""Service __NAME__ — sans base de données en V1.

Le service __NAME__ ne possède pas encore de données propres (voir
AGENTS.md, architecture). Ce module est conservé pour l'uniformité des
squelettes : ses fonctions sont volontairement inertes, et aucune
dépendance de base de données n'est installée dans l'image.
"""

from __future__ import annotations


async def initialiser_base(url: str) -> None:
    """Sans objet : le service __NAME__ ne possède pas de base en V1."""
    raise NotImplementedError("le service __NAME__ ne possede pas de base en V1")


async def verifier_base() -> bool:
    return False


async def fermer_base() -> None:
    return None
'''

MESSAGING = '''"""Enrobage du courtier RabbitMQ pour le service __NAME__.

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
'''

MAIN_PARTIE_COMMUNE = '''"""Point d'entrée du service __NAME__."""

from __future__ import annotations

import contextlib
import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from m3ak_contracts import (
    ErreurCommune,
    EtatDependance,
    ReponseEcho,
    ReponsePret,
    ReponseSante,
)

from . import db
from .config import get_settings
from .logging_setup import configurer_logging, correlation_id
from .security import exiger_jeton_interne

VERSION = "0.1.0"
logger = logging.getLogger(__name__)
'''

MAIN_LIFESPAN_AUCUN = '''
@contextlib.asynccontextmanager
async def duree_de_vie(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configurer_logging(settings.log_level)
    yield
    await db.fermer_base()
'''

MAIN_LIFESPAN_PUBLISHER = '''
@contextlib.asynccontextmanager
async def duree_de_vie(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configurer_logging(settings.log_level)
    if settings.database_url:
        await db.initialiser_base(settings.database_url)
    bus = BusRabbitMQ(settings.rabbitmq_url or "", settings.service_name)
    await bus.connecter()
    await bus.declarer_file_travail(FILE_SEGMENTS_TO_INDEX)
    app.state.bus = bus
    yield
    app.state.bus = None
    await bus.fermer()
    await db.fermer_base()
'''

MAIN_LIFESPAN_CONSUMER = '''
async def traiter_segments(message: "MessageSegmentsAIndexer") -> None:
    """Phase 0 : accusé de réception. L'upsert idempotent par segment_id
    dans la base du service arrive en phase 1."""
    premier = message.charge.segments[0]
    logger.info(
        "segments recus : %d (document=%s, emis_par=%s)",
        len(message.charge.segments),
        premier.document_id,
        message.emis_par,
    )


@contextlib.asynccontextmanager
async def duree_de_vie(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configurer_logging(settings.log_level)
    if settings.database_url:
        await db.initialiser_base(settings.database_url)
    bus = BusRabbitMQ(settings.rabbitmq_url or "", settings.service_name)
    await bus.connecter()
    await bus.declarer_file_travail(FILE_SEGMENTS_TO_INDEX)
    await bus.consommer(FILE_SEGMENTS_TO_INDEX, MessageSegmentsAIndexer, traiter_segments)
    app.state.bus = bus
    yield
    app.state.bus = None
    await bus.fermer()
    await db.fermer_base()
'''

MAIN_CREATE_APP = '''

def create_app() -> FastAPI:
    """Fabrique de l'application — facilite les tests sans infrastructure."""
    application = FastAPI(
        title="M3ak — service __NAME__",
        version=VERSION,
        lifespan=duree_de_vie,
    )

    @application.middleware("http")
    async def middleware_correlation(requete: Request, suivant) -> Response:
        identifiant = requete.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        jeton = correlation_id.set(identifiant)
        try:
            reponse: Response = await suivant(requete)
            reponse.headers["X-Correlation-ID"] = identifiant
            return reponse
        finally:
            correlation_id.reset(jeton)

    @application.exception_handler(HTTPException)
    async def gestion_http(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ErreurCommune(
                detail=str(exc.detail), correlation_id=correlation_id.get()
            ).model_dump(),
        )

    @application.get("/health", response_model=ReponseSante)
    async def sante() -> ReponseSante:
        """Vivacité — ouverte (utilisée par les healthchecks Docker/Traefik)."""
        return ReponseSante(status="ok", service="__NAME__", version=VERSION)

    @application.get("/ready", response_model=ReponsePret)
    async def pret(_: str = Depends(exiger_jeton_interne)) -> ReponsePret:
        """Prêt à servir — protégé par jeton interne."""
        settings = get_settings()
        dependances: list[EtatDependance] = []
        if settings.database_url:
            dependances.append(
                EtatDependance(nom="base", etat="ok" if await db.verifier_base() else "degrade")
            )
        statut = "ok" if all(dep.etat == "ok" for dep in dependances) else "degrade"
        return ReponsePret(status=statut, service="__NAME__", dependances=dependances)

    @application.get("/internal/echo", response_model=ReponseEcho)
    async def echo(_: str = Depends(exiger_jeton_interne)) -> ReponseEcho:
        """Écho protégé — vérifie jeton et propagation du correlation ID."""
        return ReponseEcho(service="__NAME__", correlation_id=correlation_id.get())

__ROUTES_ROLE__

    return application


application = create_app()
'''

MAIN_ROUTES_PUBLISHER = '''    @application.post("/internal/segments/publier-test")
    async def publier_segments_test(
        segments: list[SegmentArticle], _: str = Depends(exiger_jeton_interne)
    ) -> dict:
        """Publie un lot de segments vers la file d'indexation (démo phase 0)."""
        bus: BusRabbitMQ | None = getattr(application.state, "bus", None)
        if bus is None:
            raise HTTPException(status_code=503, detail="Bus RabbitMQ non initialise")
        charge = ChargeSegmentsAIndexer(segments=segments)
        enveloppe = envelopper(
            charge,
            correlation_id=correlation_id.get(),
            emis_par=get_settings().service_name,
        )
        await bus.publier(FILE_SEGMENTS_TO_INDEX, enveloppe)
        return {
            "message_id": enveloppe.message_id,
            "correlation_id": enveloppe.correlation_id,
            "segments_publies": len(segments),
        }
'''

MAIN_ROUTES_CONVERSATION = '''    @application.get("/demo/appel-recherche")
    async def demo_appel_recherche(_: str = Depends(exiger_jeton_interne)) -> dict:
        """Appel synchrone inter-services avec propagation du correlation ID."""
        settings = get_settings()
        url = settings.url_recherche.rstrip("/") + "/internal/echo"
        async with AsyncClient(timeout=5.0) as client:
            reponse = await client.get(
                url,
                headers={
                    "X-Internal-Token": settings.internal_token,
                    "X-Correlation-ID": correlation_id.get(),
                },
            )
        return {
            "service_local": settings.service_name,
            "correlation_id_local": correlation_id.get(),
            "reponse_recherche": reponse.json(),
        }
'''

MAIN_IMPORTS_PUBLISHER = '''from m3ak_contracts import (
    ChargeSegmentsAIndexer,
    ErreurCommune,
    EtatDependance,
    FILE_SEGMENTS_TO_INDEX,
    ReponseEcho,
    ReponsePret,
    ReponseSante,
    SegmentArticle,
    envelopper,
)

from .messaging import BusRabbitMQ
'''

MAIN_IMPORTS_CONSUMER = '''from m3ak_contracts import (
    ErreurCommune,
    EtatDependance,
    FILE_SEGMENTS_TO_INDEX,
    MessageSegmentsAIndexer,
    ReponseEcho,
    ReponsePret,
    ReponseSante,
)

from .messaging import BusRabbitMQ
'''

CONFIG_CONVERSATION_EXTRA = '''    url_recherche: str = "http://recherche:8002"
'''

__MAIN__ = '''"""Lancement du service en processus (python -m app)."""

from __future__ import annotations

import uvicorn

from .config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run("app.main:application", host="0.0.0.0", port=settings.port)


if __name__ == "__main__":
    main()
'''

APP_INIT = '"""Service __NAME__ — plateforme M3ak."""\n\n__version__ = "0.1.0"\n'

CONFTEST = '''"""Fixtures de test du service __NAME__ — sans infrastructure (unitaires)."""

from __future__ import annotations

import os

# Environnement de test AVANT tout import de l'application.
os.environ.setdefault("__PREFIX___INTERNAL_TOKEN", "jeton-test")
os.environ.setdefault("__PREFIX___DATABASE_URL", "")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402


@pytest.fixture()
def client() -> TestClient:
    # Sans gestionnaire de contexte : la durée de vie (base, courtier)
    # n'est PAS exécutée — tests purement unitaires.
    return TestClient(create_app())
'''

TESTS = '''"""Tests unitaires communs : santé, corrélation, jeton interne."""

from __future__ import annotations

ENTETE_JETON = {"X-Internal-Token": "jeton-test"}


def test_sante_ok(client):
    reponse = client.get("/health")
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["status"] == "ok"
    assert corps["service"] == "__NAME__"


def test_correlation_fourni_est_renvoye(client):
    reponse = client.get("/health", headers={"X-Correlation-ID": "abc-123"})
    assert reponse.headers["X-Correlation-ID"] == "abc-123"


def test_correlation_genere_si_absent(client):
    reponse = client.get("/health")
    assert reponse.headers.get("X-Correlation-ID")


def test_echo_sans_jeton_refuse(client):
    assert client.get("/internal/echo").status_code == 401


def test_pret_sans_jeton_refuse(client):
    assert client.get("/ready").status_code == 401


def test_echo_avec_jeton_et_correlation(client):
    reponse = client.get(
        "/internal/echo", headers={**ENTETE_JETON, "X-Correlation-ID": "corr-42"}
    )
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["service"] == "__NAME__"
    assert corps["correlation_id"] == "corr-42"


def test_erreur_porte_le_correlation_id(client):
    reponse = client.get("/internal/echo", headers={"X-Correlation-ID": "corr-err"})
    assert reponse.status_code == 401
    assert reponse.json()["correlation_id"] == "corr-err"
'''


# ═════════════════════════════ Génération ═════════════════════════════

IMPORT_DEFAUT = '''from m3ak_contracts import (
    ErreurCommune,
    EtatDependance,
    ReponseEcho,
    ReponsePret,
    ReponseSante,
)'''


def contenu_main(role: str, name: str) -> str:
    role_effectif = "conversation" if name == "conversation" else role
    if role_effectif == "publisher":
        imports = MAIN_IMPORTS_PUBLISHER
    elif role_effectif == "consumer":
        imports = MAIN_IMPORTS_CONSUMER
    elif role_effectif == "conversation":
        imports = IMPORT_DEFAUT + "\nfrom httpx import AsyncClient\n"
    else:
        imports = IMPORT_DEFAUT

    duree = {
        "publisher": MAIN_LIFESPAN_PUBLISHER,
        "consumer": MAIN_LIFESPAN_CONSUMER,
        "conversation": MAIN_LIFESPAN_AUCUN,
        "aucun": MAIN_LIFESPAN_AUCUN,
    }[role_effectif]

    routes = {
        "publisher": MAIN_ROUTES_PUBLISHER,
        "conversation": MAIN_ROUTES_CONVERSATION,
    }.get(role_effectif, "")

    return (
        MAIN_PARTIE_COMMUNE.replace(IMPORT_DEFAUT, imports)
        + duree
        + MAIN_CREATE_APP.replace("__ROUTES_ROLE__", routes)
    )


def generer() -> None:
    for name, (prefixe, port, _hote, possede_base, role) in MANIFEST.items():
        racine_service = SERVICES / name
        (racine_service / "app").mkdir(parents=True, exist_ok=True)
        (racine_service / "tests").mkdir(parents=True, exist_ok=True)

        fichiers: dict[Path, str] = {
            racine_service / "pyproject.toml": pyproject(name, possede_base),
            racine_service / "Dockerfile": dockerfile(name, port),
            racine_service / "app" / "__init__.py": APP_INIT.replace("__NAME__", name),
            racine_service / "app" / "config.py": CONFIG.replace("__NAME__", name)
            .replace("__PORT__", str(port))
            .replace("__PREFIX__", prefixe)
            .replace(
                "__CHAMPS_ROLE__",
                (
                    "    url_recherche: str = \"http://recherche:8002\"\n"
                    if name == "conversation"
                    else ""
                ),
            ),
            racine_service / "app" / "logging_setup.py": LOGGING.replace("__NAME__", name),
            racine_service / "app" / "security.py": SECURITY,
            racine_service / "app" / "db.py": (
                DB if possede_base else DB_SANS_BASE
            ).replace("__NAME__", name),
            racine_service / "app" / "messaging.py": MESSAGING.replace("__NAME__", name),
            racine_service / "app" / "main.py": contenu_main(role, name).replace(
                "__NAME__", name
            ),
            racine_service / "app" / "__main__.py": __MAIN__,
            racine_service / "tests" / "conftest.py": CONFTEST.replace(
                "__NAME__", name
            ).replace("__PREFIX__", prefixe),
            racine_service / "tests" / "test_sante.py": TESTS.replace("__NAME__", name),
        }
        for chemin, texte in fichiers.items():
            chemin.write_text(texte, encoding="utf-8")
        print(f"genere : services/{name}/ ({len(fichiers)} fichiers)")


if __name__ == "__main__":
    generer()
