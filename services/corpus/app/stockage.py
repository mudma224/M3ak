"""Couche brute du service corpus — stockage fichier des documents tels
que récupérés, jamais modifiés, jamais supprimés.

Deux implémentations derrière la même interface : MinIO (production,
conteneur) et un dossier local (tests et développement sans MinIO).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .config import Settings


class StockageBrut(Protocol):
    """Dépose un document brut et retourne son chemin de localisation ;
    ``lire`` le relit (re-segmentation sans re-téléchargement) ; ``copier``
    et ``supprimer_prefixe`` servent à la vue de classement (revue manuelle)."""

    def deposer(self, cle: str, contenu: bytes) -> str: ...

    def lire(self, cle: str) -> bytes: ...

    def copier(self, cle_source: str, cle_destination: str) -> None: ...

    def supprimer_prefixe(self, prefixe: str) -> int: ...


class StockageDossier:
    """Stockage brut dans un dossier local — la clé est confinée au dossier
    (pas de traversée de chemin), le chemin retourné est file:// relatif
    au dossier."""

    def __init__(self, racine: Path) -> None:
        self._racine = racine.resolve()
        self._racine.mkdir(parents=True, exist_ok=True)

    def _resoudre(self, cle: str) -> Path:
        cible = (self._racine / cle).resolve()
        if not cible.is_relative_to(self._racine):
            raise ValueError(f"cle de stockage hors racine : {cle!r}")
        return cible

    def deposer(self, cle: str, contenu: bytes) -> str:
        cible = self._resoudre(cle)
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.write_bytes(contenu)
        return f"file://{cible.relative_to(self._racine).as_posix()}"

    def lire(self, cle: str) -> bytes:
        cible = self._resoudre(cle)
        if not cible.is_file():
            raise FileNotFoundError(cle)
        return cible.read_bytes()

    def copier(self, cle_source: str, cle_destination: str) -> None:
        destination = self._resoudre(cle_destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.lire(cle_source))

    def supprimer_prefixe(self, prefixe: str) -> int:
        cible = self._resoudre(prefixe)
        if not cible.is_dir():
            return 0

        def _vider(dossier: Path) -> int:
            supprimes = 0
            for enfant in dossier.iterdir():
                if enfant.is_dir():
                    supprimes += _vider(enfant)
                    enfant.rmdir()
                else:
                    enfant.unlink()
                    supprimes += 1
            return supprimes

        total = _vider(cible)
        cible.rmdir()
        return total


class StockageMinio:
    """Stockage brut dans MinIO — bucket dédié à la couche brute du corpus."""

    def __init__(self, point_acces: str, cle_acces: str, cle_secrete: str, bucket: str) -> None:
        from minio import Minio

        self._client = Minio(point_acces, access_key=cle_acces, secret_key=cle_secrete, secure=False)
        self._bucket = bucket
        if not self._client.bucket_exists(bucket):
            self._client.make_bucket(bucket)

    def deposer(self, cle: str, contenu: bytes) -> str:
        from io import BytesIO

        self._client.put_object(
            self._bucket,
            cle,
            BytesIO(contenu),
            length=len(contenu),
            content_type="application/pdf",
        )
        return f"minio://{self._bucket}/{cle}"

    def lire(self, cle: str) -> bytes:
        reponse = self._client.get_object(self._bucket, cle)
        try:
            return reponse.read()
        finally:
            reponse.close()
            reponse.release_conn()

    def copier(self, cle_source: str, cle_destination: str) -> None:
        from minio.commonconfig import CopySource

        self._client.copy_object(
            self._bucket,
            cle_destination,
            CopySource(self._bucket, cle_source),
        )

    def supprimer_prefixe(self, prefixe: str) -> int:
        supprimes = 0
        objets = list(self._client.list_objects(self._bucket, prefix=prefixe, recursive=True))
        for objet in objets:
            self._client.remove_object(self._bucket, objet.object_name)
            supprimes += 1
        return supprimes


def creer_stockage(settings: Settings) -> StockageBrut:
    """Fabrique le stockage brut selon la configuration du service."""
    if settings.storage_backend == "minio":
        return StockageMinio(
            settings.minio_endpoint,
            settings.minio_access_key,
            settings.minio_secret_key,
            settings.minio_bucket,
        )
    return StockageDossier(Path(settings.storage_dossier))
