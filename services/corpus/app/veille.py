"""Première veille — interrogation légère des sources (phase 1).

La veille parcourt les mêmes périmètres que les collectes (7 thèmes
Adala, 9 thématiques Idarati, référentiel complet Jurisprudence.ma) sans
rien télécharger : elle compare ce que la source liste aujourd'hui avec
ce que le corpus connaît et produit un rapport de diff :

- **nouveaux** : listés par la source, absents du corpus — à collecter ;
- **disparus** : connus du corpus, absents du périmètre listé — à
  examiner (une référence peut aussi changer de thématique côté source) ;
- **modifiés** (jurisprudence) : le champ ``modified`` de la source est
  postérieur à notre date d'ingestion — le contenu a changé depuis ;
  la réconciliation est manuelle en V1 (la consolidation appartient à
  une étape ultérieure, AGENTS.md §5).

Le rapport est tracé en manifeste (source ``veille``) et apparaît dans
le tableau de bord avec les autres sessions. Le déclenchement est
manuel (endpoint dédié) ; l'automatisation périodique est du ressort
de la phase 5. Périmètre de scan figé dans le module (cf. fichiers
pilotes de ``pilot/``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from . import base
from .connecteurs import adala, idarati, jurisprudence

logger = logging.getLogger(__name__)

AGENT_UTILISATEUR = adala.AGENT_UTILISATEUR
DELAI_SECONDES = 1.2
JOURNAL_MAX_LIGNES = 60
ENTREES_MAX = 200

# Périmètre de scan (mêmes périmètres que les collectes figées).
THEMES_ADALA = [7, 149, 103, 166, 116, 110, 114]
THEMATIQUES_IDARATI = [
    "3f1eb3c6-2412-47a5-9f06-6be6c28f0d1d", "1c7bde0b-89fd-4259-a25c-e58d7b047a7f",
    "35aeb784-b5e0-41c7-bd72-b042802dffcd", "27f734c1-797d-400c-98bf-853bf6205b5c",
    "9568cabb-3058-4359-b11a-9c411acc399c", "3939a059-1e71-41e8-91d6-0daec1d6c481",
    "780e2a00-ce2f-46e5-883a-65a00bcc71f7", "98f8b970-8d30-4d24-9caa-b9a20895c38f",
    "4d17e604-3b0a-4fca-af3e-4878cbd3d30d",
]
PAGE_MAX = 300
PAGE_MAX_JURISPRUDENCE = 400


@dataclass
class EtatVeille:
    """État partagé de la dernière veille (suivi par /statut)."""

    en_cours: bool = False
    debut: str | None = None
    fin: str | None = None
    rapport: dict | None = None
    journal: list[str] = field(default_factory=list)

    def noter(self, message: str) -> None:
        horodatage = datetime.now(UTC).strftime("%H:%M:%S")
        self.journal.append(f"{horodatage} {message}")
        del self.journal[:-JOURNAL_MAX_LIGNES]


ETAT = EtatVeille()


def differ(connus: set[str], listes: set[str]) -> dict:
    """Diff entre les références connues du corpus et celles listées par
    la source : nouveaux, disparus, nombre d'inchangés."""
    return {
        "nouveaux": sorted(listes - connus),
        "disparus": sorted(connus - listes),
        "connus": len(listes & connus),
    }


async def scanner_adala(client, themes: list[int], etat: EtatVeille) -> set[str]:
    """Références listées par Adala sur les thèmes du périmètre."""
    references: set[str] = set()
    for theme in themes:
        page = 1
        while page <= PAGE_MAX:
            resultats, total = await adala.chercher_par_theme(client, theme, page)
            if not resultats:
                break
            references.update(r.reference for r in resultats)
            if page * len(resultats) >= total:
                break
            page += 1
            await asyncio.sleep(DELAI_SECONDES)
        etat.noter(f"adala theme {theme} : scan termine ({total} documents listes)")
    return references


async def scanner_idarati(client, thematiques: list[str], etat: EtatVeille) -> set[str]:
    """Identifiants de fiches listés par Idarati sur le périmètre."""
    identifiants: set[str] = set()
    for thematique_id in thematiques:
        page = 0
        while page <= PAGE_MAX:
            resultats, _total, derniere = await idarati.chercher_par_thematique(
                client, thematique_id, page
            )
            identifiants.update(r.identifiant for r in resultats)
            if derniere or not resultats:
                break
            page += 1
            await asyncio.sleep(DELAI_SECONDES)
        etat.noter(f"idarati thematique {thematique_id[:8]} : scan termine")
    return identifiants


async def scanner_jurisprudence(client, etat: EtatVeille) -> dict[str, str]:
    """Identifiants de décisions listés par Jurisprudence.ma, avec leur
    date de dernière modification côté source."""
    references: dict[str, str] = {}
    page = 1
    while page <= PAGE_MAX_JURISPRUDENCE:
        reponse = await client.get(
            "/wp-json/wp/v2/mbp_decision",
            params={"per_page": jurisprudence.TAILLE_PAGE, "page": page,
                    "_fields": "id,modified"},
        )
        if reponse.status_code == 400:
            break
        reponse.raise_for_status()
        decisions = reponse.json()
        if not decisions:
            break
        for decision in decisions:
            references[f"jp-{decision['id']}"] = decision.get("modified") or ""
        if page % 50 == 0:
            etat.noter(f"jurisprudence : page {page} ({len(references)} decisions)")
        page += 1
        await asyncio.sleep(DELAI_SECONDES)
    return references


def _decisions_modifiees(
    modifiees_source: dict[str, str], ingestion: dict[str, datetime | None]
) -> list[str]:
    """Décisions dont la modification côté source est postérieure à notre
    ingestion (horodatages comparés en UTC)."""
    modifies = []
    for reference, modified in modifiees_source.items():
        ingestion_ts = ingestion.get(reference)
        if ingestion_ts is None or not modified:
            continue
        try:
            modification = datetime.fromisoformat(modified)
        except ValueError:
            continue
        if modification.tzinfo is None:
            # WordPress sert des horodatages sans fuseau : ils sont UTC.
            modification = modification.replace(tzinfo=UTC)
        if modification > ingestion_ts:
            modifies.append(reference)
    return sorted(modifies)


async def executer_veille(etat: EtatVeille | None = None) -> dict:
    """Scan léger des trois sources et rapport de diff (aucun
    téléchargement). Le rapport est tracé en manifeste."""
    suivi = etat if etat is not None else ETAT
    suivi.en_cours = True
    suivi.debut = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        async with adala._client() as client_adala:
            listes_adala = await scanner_adala(client_adala, THEMES_ADALA, suivi)
        connus_adala = set(await base.references_par_source("adala"))
        diff_adala = differ(connus_adala, listes_adala)
        await asyncio.sleep(DELAI_SECONDES)

        async with idarati._client() as client_idarati:
            listes_idarati = await scanner_idarati(client_idarati, THEMATIQUES_IDARATI, suivi)
        connus_idarati = set(await base.references_par_source("idarati"))
        diff_idarati = differ(connus_idarati, listes_idarati)
        await asyncio.sleep(DELAI_SECONDES)

        async with jurisprudence._client() as client_jurisprudence:
            listes_jurisprudence = await scanner_jurisprudence(client_jurisprudence, suivi)
        connus_jurisprudence = await base.references_par_source("jurisprudence_ma")
        diff_jurisprudence = differ(set(connus_jurisprudence), set(listes_jurisprudence))
        modifiees = _decisions_modifiees(listes_jurisprudence, connus_jurisprudence)

        rapport = {
            "date": suivi.debut,
            "adala": {
                "listes": len(listes_adala),
                "nouveaux": len(diff_adala["nouveaux"]),
                "disparus": len(diff_adala["disparus"]),
                "connus": diff_adala["connus"],
            },
            "idarati": {
                "listes": len(listes_idarati),
                "nouveaux": len(diff_idarati["nouveaux"]),
                "disparus": len(diff_idarati["disparus"]),
                "connus": diff_idarati["connus"],
            },
            "jurisprudence": {
                "listes": len(listes_jurisprudence),
                "nouveaux": len(diff_jurisprudence["nouveaux"]),
                "disparus": len(diff_jurisprudence["disparus"]),
                "connus": diff_jurisprudence["connus"],
                "modifies": len(modifiees),
            },
        }
        entrees = [
            {"source": "adala", "type": "nouveau", "reference": r}
            for r in diff_adala["nouveaux"][:ENTREES_MAX]
        ] + [
            {"source": "adala", "type": "disparu", "reference": r}
            for r in diff_adala["disparus"][:ENTREES_MAX]
        ] + [
            {"source": "idarati", "type": "nouveau", "reference": r}
            for r in diff_idarati["nouveaux"][:ENTREES_MAX]
        ] + [
            {"source": "idarati", "type": "disparu", "reference": r}
            for r in diff_idarati["disparus"][:ENTREES_MAX]
        ] + [
            {"source": "jurisprudence", "type": "nouveau", "reference": r}
            for r in diff_jurisprudence["nouveaux"][:ENTREES_MAX]
        ] + [
            {"source": "jurisprudence", "type": "disparu", "reference": r}
            for r in diff_jurisprudence["disparus"][:ENTREES_MAX]
        ] + [
            {"source": "jurisprudence", "type": "modifie", "reference": r}
            for r in modifiees[:ENTREES_MAX]
        ]
        await base.enregistrer_manifestes([
            base.ManifesteCollecte(
                source="veille",
                methode="scan leger des sources (aucun telechargement) + diff nouveaux/disparus/modifies",
                agent_utilisateur=AGENT_UTILISATEUR,
                date_debut=datetime.fromisoformat(suivi.debut),
                date_fin=datetime.now(UTC),
                volume=rapport,
                entrees=entrees,
            )
        ])
        suivi.rapport = rapport
        suivi.noter(f"rapport : {json.dumps(rapport, ensure_ascii=False)[:120]}…")
        return rapport
    finally:
        suivi.en_cours = False
        suivi.fin = datetime.now(UTC).isoformat(timespec="seconds")
