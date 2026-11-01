"""Collecteur idempotent du corpus pilote (jalon B, phase 0).

Télécharge les documents listés dans pilot/corpus_pilote.json depuis Adala,
en sautant tout document déjà présent au registre (pilot/registre.json).
Chaque session produit un manifeste append-only (pilot/manifestes/).

Stratégie documentée dans docs/corpus-pilote.md :
- la couche brute est définitive (jamais modifiée, jamais supprimée) ;
- l'idempotence repose sur le registre (clé = chemin PDF source) ;
- la détection de modification silencieuse relève de la veille, pas d'ici.

Usage :
    python tools/collecte_pilote.py                    # collecte complète
    python tools/collecte_pilote.py --dry-run          # montrer sans télécharger
    python tools/collecte_pilote.py --cles A1,B1       # sous-ensemble
    python tools/collecte_pilote.py --familles textes_anciens
    python tools/collecte_pilote.py --limite 3         # démonstration
    python tools/collecte_pilote.py --reconstruire     # registre depuis les manifestes
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import socket
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
DOSSIER_PILOTE = RACINE / "pilot"
FICHIER_LISTE = DOSSIER_PILOTE / "corpus_pilote.json"
FICHIER_REGISTRE = DOSSIER_PILOTE / "registre.json"
DOSSIER_MANIFESTES = DOSSIER_PILOTE / "manifestes"
DOSSIER_BRUT = DOSSIER_PILOTE / "raw"

HOTE_AUTORISE = "adala.justice.gov.ma"
PREFIXE_CHEMIN = "uploads/"
DELAI_SECONDES = 1.2
TAILLE_MAX_OCTETS = 80 * 1024 * 1024
AGENT_UTILISATEUR = "M3ak-pilote/0.1 (projet educatif gratuit; contact via le depot public)"

CARACTERES_INTERDITS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def journal(message: str) -> None:
    print(message, flush=True)


def slugifier(texte: str, limite: int = 40) -> str:
    propre = CARACTERES_INTERDITS.sub("", texte).strip().replace(" ", "-")
    return propre[:limite].rstrip(".-") or "sans-titre"


def nom_fichier_local(chemin_pdf_adala: str, limite: int = 60) -> str:
    """Nom de fichier tronqué (limite MAX_PATH de Windows) dont l'unicité est
    garantie par le suffixe horodatage qu'Adala appose à chaque PDF."""
    nom = CARACTERES_INTERDITS.sub("", Path(chemin_pdf_adala).name) or "document.pdf"
    racine, extension = nom[:-4], nom[-4:]
    marque = re.search(r"-(\d{10,})$", racine)
    horodatage = marque.group(1) if marque else hashlib.sha256(nom.encode()).hexdigest()[:13]
    coupe = racine[:limite].rstrip(". -")
    return f"{coupe}-{horodatage}{extension}"


def charger_json(chemin: Path, defaut):
    if not chemin.exists():
        return defaut
    return json.loads(chemin.read_text(encoding="utf-8"))


def ecrire_json(chemin: Path, donnees) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(
        json.dumps(donnees, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def valider_url_pdf(url: str) -> str:
    """Allowlist stricte : https, hote unique, sans identifiants, IP résolue
    strictement publique (anti rebinding/SSRF), redirections interdites."""
    partes = urllib.parse.urlparse(url)
    if partes.scheme != "https" or partes.netloc != HOTE_AUTORISE:
        raise ValueError(f"URL hors allowlist : {url}")
    if partes.username or partes.password:
        raise ValueError("identifiants interdits dans l'URL")
    try:
        infos = socket.getaddrinfo(
            partes.hostname, partes.port or 443, proto=socket.IPPROTO_TCP
        )
    except OSError as erreur:
        raise ValueError(f"resolution DNS impossible pour {HOTE_AUTORISE}") from erreur
    for information in infos:
        adresse = ipaddress.ip_address(information[4][0])
        if not adresse.is_global:
            raise ValueError(
                f"{HOTE_AUTORISE} resout vers une adresse non publique ({adresse})"
            )
    return url


class _SansRedirection(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OUVREUR = urllib.request.build_opener(_SansRedirection)


def url_pdf(chemin_pdf_adala: str) -> str:
    """Construit l'URL de téléchargement depuis le chemin Adala vérifié."""
    if not chemin_pdf_adala.startswith(PREFIXE_CHEMIN):
        raise ValueError(f"chemin PDF inattendu : {chemin_pdf_adala!r}")
    suffixe = chemin_pdf_adala[len(PREFIXE_CHEMIN):]
    return valider_url_pdf(
        f"https://{HOTE_AUTORISE}/api/uploads/" + urllib.parse.quote(suffixe)
    )


def chemin_local(cle: str, intitule: str, chemin_pdf_adala: str) -> Path:
    brut = (
        DOSSIER_BRUT / "adala" / f"{cle}-{slugifier(intitule)}"
        / nom_fichier_local(chemin_pdf_adala)
    )
    resolu = brut.resolve()
    if not str(resolu).startswith(str(DOSSIER_BRUT.resolve())):
        raise ValueError(f"chemin local hors zone pilote : {resolu}")
    return resolu


def telecharger(url: str) -> bytes:
    requete = urllib.request.Request(url, headers={"User-Agent": AGENT_UTILISATEUR})
    with _OUVREUR.open(requete, timeout=120) as reponse:
        donnees = reponse.read(TAILLE_MAX_OCTETS + 1)
    if len(donnees) > TAILLE_MAX_OCTETS:
        raise ValueError("document trop volumineux")
    if not donnees.startswith(b"%PDF-"):
        raise ValueError("la réponse n'est pas un PDF")
    return donnees


def reconstruire_registre() -> int:
    entrees = {}
    manifestes = sorted(DOSSIER_MANIFESTES.glob("collecte-*.json"))
    for manifeste in manifestes:
        session = charger_json(manifeste, {})
        for entree in session.get("telecharges", []):
            entrees[entree["chemin_pdf_adala"]] = {
                "cle_pilote": entree["cle_pilote"],
                "empreinte_sha256": entree["empreinte_sha256"],
                "chemin_local": entree["chemin_local"],
                "octets": entree["octets"],
                "collecte_le": session["date_debut"],
            }
    ecrire_json(FICHIER_REGISTRE, {"schema_version": "1.0", "documents": entrees})
    journal(
        f"registre reconstruit depuis {len(manifestes)} manifestes : "
        f"{len(entrees)} documents"
    )
    return 0


def principale() -> int:
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--cles", help="sous-ensemble de clés pilotes (A1,B2)")
    analyseur.add_argument(
        "--familles", help="filtrer par familles (codes_hierarchie_profonde,...)"
    )
    analyseur.add_argument("--limite", type=int, help="nombre maximum de téléchargements")
    analyseur.add_argument(
        "--dry-run", action="store_true", help="montrer les actions sans télécharger"
    )
    analyseur.add_argument(
        "--reconstruire",
        action="store_true",
        help="reconstruire le registre depuis les manifestes puis quitter",
    )
    arguments = analyseur.parse_args()

    if arguments.reconstruire:
        return reconstruire_registre()

    liste = charger_json(FICHIER_LISTE, None)
    if liste is None:
        journal(f"liste introuvable : {FICHIER_LISTE}")
        return 1
    registre = charger_json(FICHIER_REGISTRE, {"schema_version": "1.0", "documents": {}})
    connus = registre.get("documents", {})
    empreintes_connues = {v["empreinte_sha256"]: v["chemin_local"] for v in connus.values()}

    cibles = arguments.cles.split(",") if arguments.cles else None
    familles = arguments.familles.split(",") if arguments.familles else None

    telecharges, ignores, echecs = [], [], []
    debut_session = datetime.now(timezone.utc).isoformat(timespec="seconds")
    journal(
        f"session {debut_session} — {len(liste['documents'])} documents dans la liste, "
        f"{len(connus)} au registre"
    )

    for document in liste["documents"]:
        cle = document["cle"]
        if document["source"] != "adala":
            continue
        if cibles and cle not in cibles:
            continue
        if familles and document["famille"] not in familles:
            continue

        chemin_pdf = document["chemin_pdf_adala"]
        if chemin_pdf in connus:
            ignores.append(
                {
                    "cle_pilote": cle,
                    "raison": "deja_au_registre",
                    "collecte_le": connus[chemin_pdf]["collecte_le"],
                }
            )
            journal(
                f"  SKIP {cle} {document['intitule']} — déjà collecté le "
                f"{connus[chemin_pdf]['collecte_le']}"
            )
            continue
        if arguments.dry_run:
            journal(f"  A-COLLECTER {cle} {document['intitule']} — {chemin_pdf}")
            continue
        if arguments.limite is not None and len(telecharges) >= arguments.limite:
            journal(f"  STOP {cle} — limite de {arguments.limite} téléchargements atteinte")
            break

        try:
            url = url_pdf(chemin_pdf)
            journal(f"  GET {cle} {document['intitule']}…")
            donnees = telecharger(url)
        except Exception as erreur:  # noqa: BLE001 — consigner et poursuivre
            echecs.append({"cle_pilote": cle, "erreur": str(erreur)[:200]})
            journal(f"  ERREUR {cle} — {erreur}")
            time.sleep(DELAI_SECONDES)
            continue

        empreinte = hashlib.sha256(donnees).hexdigest()
        destination = chemin_local(cle, document["intitule"], chemin_pdf)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(donnees)

        entree = {
            "cle_pilote": cle,
            "famille": document["famille"],
            "intitule": document["intitule"],
            "reference_promulgation": document.get("reference_promulgation"),
            "chemin_pdf_adala": chemin_pdf,
            "empreinte_sha256": empreinte,
            "chemin_local": str(destination.relative_to(RACINE)),
            "octets": len(donnees),
            "collecte_le": debut_session,
        }
        if empreinte in empreintes_connues:
            entree["doublon_de"] = empreintes_connues[empreinte]
            journal(
                f"  OK {cle} — {len(donnees)} octets, sha256 {empreinte[:12]}… "
                f"(⚠ même contenu que {empreintes_connues[empreinte]})"
            )
        else:
            journal(f"  OK {cle} — {len(donnees)} octets, sha256 {empreinte[:12]}…")
        telecharges.append(entree)
        connus[chemin_pdf] = {
            k: entree[k]
            for k in ("cle_pilote", "empreinte_sha256", "chemin_local", "octets", "collecte_le")
        }
        empreintes_connues[empreinte] = entree["chemin_local"]
        time.sleep(DELAI_SECONDES)

    if telecharges and not arguments.dry_run:
        ecrire_json(FICHIER_REGISTRE, {"schema_version": "1.0", "documents": connus})
        horodatage = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        manifeste = {
            "schema_version": "1.0",
            "date_debut": debut_session,
            "date_fin": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": "adala",
            "methode": (
                "GET https://adala.justice.gov.ma/api/uploads/<chemin> — liste figée "
                "pilot/corpus_pilote.json, registre pilot/registre.json"
            ),
            "agent_utilisateur": AGENT_UTILISATEUR,
            "volume": {
                "telecharges": len(telecharges),
                "ignores": len(ignores),
                "echecs": len(echecs),
            },
            "telecharges": telecharges,
            "ignores": ignores,
            "echecs": echecs,
        }
        ecrire_json(DOSSIER_MANIFESTES / f"collecte-{horodatage}.json", manifeste)

    journal(
        f"terminé : {len(telecharges)} téléchargés, {len(ignores)} ignorés "
        f"(déjà au registre), {len(echecs)} échecs"
    )
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(principale())
