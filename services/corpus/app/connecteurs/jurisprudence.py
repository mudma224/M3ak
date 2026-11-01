"""Connecteur Jurisprudence.ma — décisions de justice (phase 1).

Parcours allégé du référentiel (``_fields=id,class_list``, ~250 requêtes)
pour sélectionner les décisions par slugs de thématiques — les
taxonomies n'étant pas exposées en REST, les slugs sont portés par la
``class_list`` de chaque décision — puis fiche complète par identifiant.

Une décision est un document bilingue (résumé français, texte intégral
arabe) : elle est conservée telle quelle en couche brute
(``jurisprudence/<empreinte>.json``), enregistrée en classe A dans la
couche normalisée (collection ``jurisprudence``), puis publiée sur la
file dédiée ``decisions.to_index`` (contrat ``DecisionAIndexer``) avec
les deux langues portées par des champs distincts, débarrassées de leur
balisage HTML. La langue du document est celle du texte intégral (ar),
sinon le résumé (fr).

Le champ ``modified`` de la source porte les mises à jour : une décision
modifiée arrive avec une autre empreinte et est refusée — la veille
gèrera l'évolution (AGENTS.md §5).

Règles de collecte (AGENTS.md §3.2) : rythme raisonnable (une requête
par ~1,2 s), agent utilisateur identifiable, manifeste de session en
base. L'idempotence (empreinte du JSON) fait qu'une re-collecte ne
retraite jamais une décision déjà ingérée.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from m3ak_contracts import FILE_DECISIONS_TO_INDEX, ChargeDecisionsAIndexer, envelopper

from .. import base
from ..messaging import BusRabbitMQ
from ..normalisation import cle_document
from ..stockage import StockageBrut

logger = logging.getLogger(__name__)

HOTE = "jurisprudence.ma"
AGENT_UTILISATEUR = "M3ak-connecteur/0.1 (projet educatif gratuit; contact via le depot public)"
DELAI_SECONDES = 1.2
TAILLE_PAGE = 100
TAILLE_MAX_OCTETS = 5 * 1024 * 1024
JOURNAL_MAX_LIGNES = 60
PREFIXE_THEMES = "mbp_decision_categories-"


@dataclass
class EtatCollecte:
    """État partagé de la dernière collecte (suivi par /statut)."""

    en_cours: bool = False
    thematiques: list[str] | None = None
    dry_run: bool = False
    debut: str | None = None
    fin: str | None = None
    statuts: dict[str, int] = field(default_factory=dict)
    journal: list[str] = field(default_factory=list)

    def noter(self, message: str) -> None:
        horodatage = datetime.now(UTC).strftime("%H:%M:%S")
        self.journal.append(f"{horodatage} {message}")
        del self.journal[:-JOURNAL_MAX_LIGNES]


ETAT = EtatCollecte()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=f"https://{HOTE}",
        headers={"User-Agent": AGENT_UTILISATEUR},
        timeout=120,
        follow_redirects=False,
        trust_env=False,
    )


def _slugs_de(decision_legere: dict) -> list[str]:
    """Slugs de thématiques portés par une décision du parcours allégé."""
    return [
        classe[len(PREFIXE_THEMES):]
        for classe in decision_legere.get("class_list") or []
        if classe.startswith(PREFIXE_THEMES)
    ]


async def parcourir_references(
    client: httpx.AsyncClient, etat: EtatCollecte
) -> list[dict]:
    """Parcours allégé du référentiel (id + class_list) — la source
    clôt la pagination par une erreur 400 au-delà de la dernière page."""
    references: list[dict] = []
    page = 1
    while True:
        reponse = await client.get(
            "/wp-json/wp/v2/mbp_decision",
            params={"per_page": TAILLE_PAGE, "page": page, "_fields": "id,class_list"},
        )
        if reponse.status_code == 400:
            etat.noter(f"fin du referentiel a la page {page}")
            break
        reponse.raise_for_status()
        decisions = reponse.json()
        if not decisions:
            break
        references.extend(decisions)
        if page % 50 == 0:
            etat.noter(f"parcours : page {page} ({len(references)} decisions)")
        page += 1
        await asyncio.sleep(DELAI_SECONDES)
    return references


def selectionner(
    references: list[dict], thematiques: list[str], limites: dict[str, int]
) -> list[int]:
    """Identifiants à collecter : par thématique, les premières décisions
    du référentiel (les plus récentes) dans la limite fixée ; une décision
    portant plusieurs thématiques visées n'est retenue qu'une fois."""
    choisies: dict[int, None] = {}
    for slug in thematiques:
        plafond = limites.get(slug)
        retenues = 0
        for reference in references:
            if plafond is not None and retenues >= plafond:
                break
            if slug in _slugs_de(reference) and reference["id"] not in choisies:
                choisies[reference["id"]] = None
                retenues += 1
    return list(choisies)


async def recuperer_decision(client: httpx.AsyncClient, identifiant: int) -> bytes:
    """Décision complète, conservée telle que servie par l'API."""
    reponse = await client.get(f"/wp-json/wp/v2/mbp_decision/{identifiant}")
    reponse.raise_for_status()
    contenu = reponse.content
    if len(contenu) > TAILLE_MAX_OCTETS:
        raise ValueError(f"decision trop volumineuse : {identifiant}")
    detail = json.loads(contenu)
    if not isinstance(detail, dict) or detail.get("id") != identifiant:
        raise ValueError(f"reponse inattendue pour {identifiant}")
    return contenu


def _texte_depuis_html(brut: str | None) -> str | None:
    """Texte sans balisage ni caractères de contrôle (PostgreSQL refuse
    notamment les octets NUL) : la couche index ne conserve que du
    contenu exploitable."""
    if not brut:
        return None
    sans_balisage = re.sub(r"<[^>]+>", " ", brut)
    imprimables = "".join(c for c in html.unescape(sans_balisage) if c >= " " or c == "\t")
    lisse = re.sub(r"\s+", " ", imprimables).strip()
    return lisse or None


def _date_iso(brut: str | None) -> str | None:
    if brut and (match := re.fullmatch(r"(\d{4})(\d{2})(\d{2})", brut.strip())):
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return None


def construire_charge(detail: dict) -> dict:
    """Charge ``DecisionAIndexer`` construite depuis la décision complète —
    partagée par l'ingestion et le rejeu des publications depuis la couche
    brute."""
    acf = detail.get("acf") or {}
    titre = re.sub(r"\s+", " ", html.unescape(
        (detail.get("title") or {}).get("rendered") or ""
    )).strip() or "sans-titre"
    return {
        "decision_id": "",  # renseigné par l'appelant (clé d'identifiant stable)
        "document_id": "",
        "titre": titre,
        "langue": "",  # renseigné par l'appelant (langue du texte intégral)
        "thematiques": _slugs_de(detail),
        "juridiction": (acf.get("juridiction") or "").strip() or None,
        "ville": (acf.get("ville") or "").strip() or None,
        "type_decision": (acf.get("type_de_decision") or "").strip() or None,
        "numero_decision": str(acf.get("numero_de_decision") or "").strip() or None,
        "numero_dossier": str(acf.get("numero_de_dossier") or "").strip() or None,
        "date_decision": _date_iso(acf.get("date_de_decision")),
        "resume_francais": _texte_depuis_html(acf.get("resume_en_francais")),
        "resume_arabe": _texte_depuis_html(acf.get("resume_en_arabe")),
        "texte_integral_arabe": _texte_depuis_html(acf.get("version_arabe_de_la_decision")),
        "texte_integral_francais": _texte_depuis_html(acf.get("version_francaise_de_la_decisin")),
    }


async def ingester_decision(
    contenu: bytes, bus: BusRabbitMQ, stockage: StockageBrut
) -> str:
    """Couche brute (JSON) + couche normalisée (collection jurisprudence,
    classe A) + publication sur la file dédiée ``decisions.to_index``.
    Idempotence : même empreinte n'importe sous quelle clé → déjà présent ;
    contenu différent sous la même clé → refusé (la veille gère l'évolution)."""
    empreinte = hashlib.sha256(contenu).hexdigest()
    carte = await base.carte_documents()
    if empreinte in carte.values():
        return "deja_present"

    detail = json.loads(contenu)
    acf = detail.get("acf") or {}
    identifiant = detail["id"]
    reference = f"jp-{identifiant}"

    titre = re.sub(r"\s+", " ", html.unescape(
        (detail.get("title") or {}).get("rendered") or ""
    )).strip() or "sans-titre"
    resume_francais = _texte_depuis_html(acf.get("resume_en_francais"))
    resume_arabe = _texte_depuis_html(acf.get("resume_en_arabe"))
    texte_arabe = _texte_depuis_html(acf.get("version_arabe_de_la_decision"))
    texte_francais = _texte_depuis_html(acf.get("version_francaise_de_la_decisin"))

    # La langue du document est celle du texte intégral (ar), sinon le
    # résumé (fr) — champs structurés de la source, pas de détection floue.
    if texte_arabe:
        langue = "ar"
    elif resume_francais or texte_francais:
        langue = "fr"
    else:
        langue = "inconnue"
    if langue in ("ar", "fr"):
        document_id = cle_document(reference, langue)
    else:
        document_id = f"{reference}|inconnue|originale"

    empreinte_connue = carte.get(document_id)
    if empreinte_connue == empreinte:
        return "deja_present"
    if empreinte_connue is not None:
        logger.warning(
            "version differente refusee : %s (connue %s…, nouvelle %s…)",
            document_id, empreinte_connue[:12], empreinte[:12],
        )
        return "version_differente"

    chemin_brut = await asyncio.to_thread(
        stockage.deposer, f"jurisprudence/{empreinte}.json", contenu
    )
    caracteres = sum(
        len(t or "") for t in (resume_francais, resume_arabe, texte_arabe, texte_francais)
    )
    fiche = base.DocumentNormalise(
        document_id=document_id,
        empreinte_sha256=empreinte,
        source="jurisprudence_ma",
        titre=titre,
        langue=langue,
        chemin_brut=chemin_brut,
        pages=1,
        caracteres=caracteres,
        nb_articles=0,
        score_numerotation=0.0,
        classe_qualite="A",
        en_quarantaine=False,
        avertissements=[],
        date_ingestion=datetime.now(UTC),
    )
    insere = await base.enregistrer_documents([fiche])

    nb_messages = 0
    if insere and langue in ("ar", "fr"):
        charge = construire_charge(detail)
        charge["decision_id"] = document_id
        charge["document_id"] = document_id
        charge["langue"] = langue
        enveloppe = envelopper(
            ChargeDecisionsAIndexer(decisions=[charge]),
            correlation_id=f"ingestion-decision-{empreinte[:12]}",
            emis_par="corpus",
        )
        await bus.publier(FILE_DECISIONS_TO_INDEX, enveloppe)
        nb_messages = 1

    logger.info(
        "ingestion decision %s : langue=%s caracteres=%d inseres=%d messages=%d",
        document_id, langue, caracteres, insere, nb_messages,
    )
    return "insere" if insere else "deja_present"


async def executer_collecte(
    bus: BusRabbitMQ,
    stockage: StockageBrut,
    thematiques: list[str],
    limites: dict[str, int] | None = None,
    dry_run: bool = False,
    etat: EtatCollecte | None = None,
) -> dict:
    """Parcourt le référentiel, sélectionne les décisions par thématique
    (bornées par ``limites``, intégral pour les thématiques absentes de la
    table) et ingère chaque décision. En dry-run : recense sans rien
    télécharger ni ingérer. Retourne le manifeste de session (aussi
    conservé en base hors dry-run)."""
    limites = limites or {}
    suivi = etat if etat is not None else ETAT
    suivi.en_cours = True
    suivi.thematiques = list(thematiques)
    suivi.dry_run = dry_run
    suivi.debut = datetime.now(UTC).isoformat(timespec="seconds")
    suivi.statuts = {}
    try:
        async with _client() as client:
            references = await parcourir_references(client, suivi)
            choisies = selectionner(references, thematiques, limites)
            suivi.noter(
                f"selection : {len(choisies)} decisions pour {len(thematiques)} thematiques"
            )
            for identifiant in choisies:
                if dry_run:
                    suivi.statuts["vus"] = suivi.statuts.get("vus", 0) + 1
                    continue
                try:
                    contenu = await recuperer_decision(client, identifiant)
                    statut = await ingester_decision(contenu, bus, stockage)
                    suivi.statuts[statut] = suivi.statuts.get(statut, 0) + 1
                    if suivi.statuts[statut] % 50 == 0:
                        suivi.noter(f"{suivi.statuts[statut]} decisions {statut}s")
                except Exception as erreur:  # noqa: BLE001 — consigné, la collecte poursuit
                    suivi.statuts["echec"] = suivi.statuts.get("echec", 0) + 1
                    suivi.noter(f"echec : {identifiant} — {str(erreur)[:80]}")
                await asyncio.sleep(DELAI_SECONDES)
    finally:
        suivi.en_cours = False
        suivi.fin = datetime.now(UTC).isoformat(timespec="seconds")

    manifeste = base.ManifesteCollecte(
        source="jurisprudence_ma",
        methode=(
            f"parcours allégé + sélection par thematique ({len(thematiques)} slugs, "
            f"limites={limites or '{}'}, dry_run={dry_run}) + decision complete par id"
        ),
        agent_utilisateur=AGENT_UTILISATEUR,
        date_debut=datetime.fromisoformat(suivi.debut or ""),
        date_fin=datetime.fromisoformat(suivi.fin or ""),
        volume=dict(suivi.statuts),
        entrees=[{"reference": f"jp-{i}"} for i in choisies] if not dry_run else [],
    )
    if not dry_run and (suivi.statuts.get("insere") or suivi.statuts.get("insere_quarantaine")):
        await base.enregistrer_manifestes([manifeste])
    return manifeste.model_dump()
