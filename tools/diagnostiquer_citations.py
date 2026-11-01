"""Diagnostic des échecs de vérification de citations (phase 2, étape 6).

Rejoue le pipeline exact de la conversation (mêmes modules que le
service) pour les questions fournies, jusqu'à la sortie BRUTE du modèle
local, puis applique la vérification mécanique en montrant précisément
ce qui a échoué : renvois [n] absents, citation « … » déformée (avec la
distance au texte du passage), ou réflexion seule. Le texte refusé par
le service n'étant pas persisté, c'est l'outil d'investigation pour
régler la discipline de citation.

Usage :
    python tools/diagnostiquer_citations.py --ids SEG-FR-09 FIC-AR-29
    python tools/diagnostiquer_citations.py --toutes_non_verifiables
"""

from __future__ import annotations

import argparse
import difflib
import ipaddress
import json
import re
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "services" / "conversation"))

from app.anonymisation import anonymiser
from app.citations import normaliser_pour_citation, verifier_citations
from app.langue import detecter_langue
from app.prompt import assembler_messages

FICHIER_QUESTIONS = RACINE / "pilot" / "jeu_dore" / "questions.json"
URL_CONVERSATION = "http://127.0.0.1"
HOST_CONVERSATION = "conversation.m3ak.localhost"
URL_RECHERCHE = "http://127.0.0.1/internal/recherche"
HOST_RECHERCHE = "recherche.m3ak.localhost"
URL_LLM = "http://127.0.0.1/internal/generation"
HOST_LLM = "llm.m3ak.localhost"
NB_SOURCES = 8
MAX_CARA_PASSAGE = 2000
MAX_JETONS = 2560


def journal(message: str) -> None:
    print(message, flush=True)


def jeton_interne() -> str:
    for ligne in (RACINE / "infra" / ".env").read_text(encoding="utf-8").splitlines():
        if ligne.startswith("INTERNAL_TOKEN="):
            return ligne.split("=", 1)[1].strip()
    raise SystemExit("INTERNAL_TOKEN introuvable dans infra/.env")


def valider_url_bouclage(url: str) -> None:
    adresse = urlparse(url).hostname
    if not all(
        ipaddress.ip_address(information[4][0]).is_loopback
        for information in socket.getaddrinfo(adresse, None)
    ):
        raise SystemExit(f"{url} doit être une adresse de bouclage")


def _fragment_le_plus_proche(citation: str, passages: dict[int, str]) -> tuple[float, str]:
    """Le passage normalisé contenant la plus longue sous-chaîne commune
    avec la citation — pour voir de combien la copie de Gemma dévie."""
    cible = normaliser_pour_citation(citation)
    meilleur_ratio, meilleur_extrait = 0.0, ""
    for texte in passages.values():
        ratio = difflib.SequenceMatcher(None, cible, normaliser_pour_citation(texte)).ratio()
        if ratio > meilleur_ratio:
            meilleur_ratio, meilleur_extrait = ratio, texte
    return meilleur_ratio, meilleur_extrait[:200]


def _mots_en_commun(citation: str, passages: dict[int, str]) -> list[str]:
    """Les mots de la citation qui existent dans les passages — pour voir
    si le modèle reformule avec le bon vocabulaire."""
    corpus = " ".join(normaliser_pour_citation(t) for t in passages.values()).lower()
    return [mot for mot in re.findall(r"\w{4,}", citation.lower()) if mot in corpus]


def diagnostiquer(client: httpx.Client, entetes: dict, question: dict) -> None:
    journal(f"\n=== {question['id']} [{question['langue']}] {question['question'][:70]}")
    recherche = client.post(
        URL_RECHERCHE,
        headers={"Host": HOST_RECHERCHE, "X-Internal-Token": entetes["X-Internal-Token"]},
        json={"question": anonymiser(question["question"])[0], "top_k": NB_SOURCES},
        timeout=90.0,
    )
    if recherche.status_code != 200:
        journal(f"  recherche HTTP {recherche.status_code} — arrêt")
        return
    resultats = recherche.json()["resultats"]
    if not resultats:
        journal("  aucune source — ce cas est côté recherche, pas côté citation")
        return
    passages = {
        rang: r["texte"][:MAX_CARA_PASSAGE] for rang, r in enumerate(resultats, start=1)
    }
    bloc = [
        (rang, r["titre"], r["source"], passages[rang]) for rang, r in enumerate(resultats, start=1)
    ]
    messages = assembler_messages(question["question"], bloc, detecter_langue(question["question"]))
    generation = client.post(
        URL_LLM,
        headers={"Host": HOST_LLM, "X-Internal-Token": entetes["X-Internal-Token"]},
        json={
            "messages": [m.model_dump() for m in messages],
            "max_jetons": MAX_JETONS,
            "fournisseur": "local",
        },
        timeout=900.0,
    )
    if generation.status_code != 200:
        journal(f"  génération HTTP {generation.status_code}")
        return
    texte = generation.json()["texte"].strip()
    verification = verifier_citations(texte, passages)

    journal(f"  complet={generation.json().get('complet')} | longueur={len(texte)} car.")
    journal(f"  renvois [n] trouvés : {sorted(int(m) for m in re.findall(r'\\[(\\d{{1,2}})\\]', texte))}")
    citations = re.findall(r"«([^»]+)»", texte)
    journal(f"  citations « » : {len(citations)}")
    if not verification.valide:
        journal(f"  ÉCHEC : {verification.motif}")
        for citation in citations:
            ratio, _extrait = _fragment_le_plus_proche(citation, passages)
            journal(f"    «{citation[:90]}» → proximité max {ratio:.2f}")
            if ratio < 1.0:
                communs = _mots_en_commun(citation, passages)
                journal(f"      mots du passage retrouvés : {communs[:8]}")
    journal(f"  sortie brute (900 premiers caractères) :\n{texte[:900]}")


def principale() -> int:
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--ids", nargs="*", default=[])
    analyseur.add_argument(
        "--toutes_non_verifiables", action="store_true",
        help="reprend les ids generation_non_verifiable de la dernière mesure",
    )
    arguments = analyseur.parse_args()
    valider_url_bouclage(URL_CONVERSATION)

    questions = {
        q["id"]: q
        for q in json.loads(FICHIER_QUESTIONS.read_text(encoding="utf-8"))["questions"]
    }
    if arguments.toutes_non_verifiables:
        dernier = max(RACINE.joinpath("pilot/jeu_dore/resultats").glob("bout-en-bout-2*.json"))
        rapport = json.loads(dernier.read_text(encoding="utf-8"))
        cibles = [
            d["id"] for d in rapport["detail"] if d.get("motif_refus") == "generation_non_verifiable"
        ]
        journal(f"dernier rapport : {dernier.name} — {len(cibles)} question(s) à diagnostiquer")
    else:
        cibles = arguments.ids
    for identifiant in cibles:
        if identifiant not in questions:
            journal(f"{identifiant} : inconnu du jeu doré")
            continue
    entetes = {"Host": HOST_CONVERSATION, "X-Internal-Token": jeton_interne()}
    with httpx.Client(trust_env=False) as client:
        for identifiant in cibles:
            diagnostiquer(client, entetes, questions[identifiant])
    return 0


if __name__ == "__main__":
    raise SystemExit(principale())
