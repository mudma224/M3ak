"""Tests du flux SSE (étape 3bis) — filtre de réflexion, décodage du
protocole, fournisseur mocké et point de génération en flux."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
from fastapi.testclient import TestClient

from app import fournisseurs
from app.fournisseurs import (
    ErreurFournisseur,
    FiltrePensee,
    FournisseurOpenAI,
    MorceauFlux,
    complet_depuis_motif,
    morceau_depuis_charge,
)
from app.main import _taches_fond, create_app
from app.quota import GestionnaireQuota

ENTETES = {"X-Internal-Token": "jeton-test"}
DEMANDE = {
    "messages": [{"role": "user", "contenu": "Question de test ?"}],
    "max_jetons": 64,
    "fournisseur": "fictif",
}


# ── Filtre de réflexion (fonction pure) ──────────────────────────────────


def test_filtre_pensee_bloc_complet_en_un_fragment():
    filtre = FiltrePensee()
    assert filtre.nourrir("<think>secret</think>Réponse.") == "Réponse."
    assert filtre.vider() == ""


def test_filtre_pensee_balise_coupee_entre_fragments():
    filtre = FiltrePensee()
    assert filtre.nourrir("Voici <th") == "Voici "
    assert filtre.nourrir("ink>caché</think>OK") == "OK"
    assert filtre.vider() == ""


def test_filtre_pensee_reflexion_inachevee_supprimee():
    filtre = FiltrePensee()
    assert filtre.nourrir("Début <think>jamais fermé") == "Début "
    assert filtre.vider() == ""


def test_filtre_pensee_texte_sain_intact():
    filtre = FiltrePensee()
    assert filtre.nourrir("L'article 3 prévoit ") == "L'article 3 prévoit "
    assert filtre.nourrir("un délai de 30 jours.") == "un délai de 30 jours."
    assert filtre.vider() == ""


# ── Décodage du protocole (fonction pure) ────────────────────────────────


def test_morceau_depuis_charge_extrait_contenu():
    morceau = morceau_depuis_charge({"choices": [{"delta": {"content": "Bonjour"}}]})
    assert morceau is not None
    assert morceau.texte == "Bonjour"


def test_morceau_depuis_charge_extrait_raisonnement():
    morceau = morceau_depuis_charge(
        {"choices": [{"delta": {"reasoning_content": "réflexion"}}]}
    )
    assert morceau is not None
    assert morceau.raisonnement == "réflexion"


def test_morceau_depuis_charge_extrait_usage_final():
    morceau = morceau_depuis_charge(
        {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    )
    assert morceau is not None
    assert (morceau.jetons_entree, morceau.jetons_sortie) == (10, 5)


def test_morceau_depuis_charge_vide_renvoie_none():
    assert morceau_depuis_charge({"choices": [{"delta": {}}]}) is None


def test_morceau_depuis_charge_extrait_le_motif_de_fin():
    morceau = morceau_depuis_charge(
        {"choices": [{"delta": {}, "finish_reason": "length"}]}
    )
    assert morceau is not None
    assert morceau.fin_motif == "length"


def test_complet_depuis_motif():
    assert complet_depuis_motif("stop") is True
    assert complet_depuis_motif("length") is False
    assert complet_depuis_motif(None) is None


# ── generer_flux contre un serveur mocké ─────────────────────────────────


def _brancher_transport(monkeypatch, gestionnaire) -> None:
    """Remplace le client HTTP du fournisseur par un transport mocké."""

    def fabrique(*args, **kwargs):
        kwargs.pop("trust_env", None)
        kwargs["transport"] = httpx.MockTransport(gestionnaire)
        return _client_reel(**kwargs)

    monkeypatch.setattr(fournisseurs.httpx, "AsyncClient", fabrique)


# La vraie classe, capturée avant tout patch (la fabrique ci-dessus
# remplace l'attribut du module partagé).
_client_reel = httpx.AsyncClient


def _serveur_sse(lignes: list[str]):
    corps = ("\n".join(lignes) + "\n").encode()

    def gestionnaire(requete: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=corps, headers={"content-type": "text/event-stream"}
        )

    return gestionnaire


def _collecter(fournisseur: FournisseurOpenAI) -> list[MorceauFlux]:
    messages = [fournisseurs.MessageGeneration(role="user", contenu="Question ?")]

    async def parcourir() -> list[MorceauFlux]:
        return [m async for m in fournisseur.generer_flux(messages, 0.0, 128)]

    return asyncio.run(parcourir())


def test_generer_flux_filtre_reflexion_et_rapporte_usage(monkeypatch):
    _brancher_transport(
        monkeypatch,
        _serveur_sse(
            [
                'data: {"choices":[{"delta":{"role":"assistant"}}]}',
                'data: {"choices":[{"delta":{"content":"<think>secret</think>Ré"}}]}',
                'data: {"choices":[{"delta":{"content":"ponse."}}]}',
                'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":10,"completion_tokens":5}}',
                "data: [DONE]",
            ]
        ),
    )
    morceaux = _collecter(
        FournisseurOpenAI(nom="fictif", url_base="http://fictif", modele="modele-test")
    )
    assert [m.modele for m in morceaux if m.modele] == ["modele-test"]
    assert [m.texte for m in morceaux if m.texte] == ["Ré", "ponse."]
    final = morceaux[-1]
    assert (final.jetons_entree, final.jetons_sortie) == (10, 5)


def test_generer_flux_repli_sur_raisonnement(monkeypatch):
    _brancher_transport(
        monkeypatch,
        _serveur_sse(
            [
                'data: {"choices":[{"delta":{"reasoning_content":"raisonnement interne"}}]}',
                'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":10,"completion_tokens":20}}',
                "data: [DONE]",
            ]
        ),
    )
    morceaux = _collecter(
        FournisseurOpenAI(nom="fictif", url_base="http://fictif", modele="modele-test")
    )
    assert [m.texte for m in morceaux if m.texte] == ["raisonnement interne"]


def test_generer_flux_reprend_sans_stream_options_refuse(monkeypatch):
    def gestionnaire(requete: httpx.Request) -> httpx.Response:
        charge = json.loads(requete.content)
        if "stream_options" in charge:
            return httpx.Response(400, json={"error": "stream_options inconnu"})
        corps = (
            'data: {"choices":[{"delta":{"content":"Réponse estimée."}}]}\n'
            "data: [DONE]\n"
        ).encode()
        return httpx.Response(
            200, content=corps, headers={"content-type": "text/event-stream"}
        )

    _brancher_transport(monkeypatch, gestionnaire)
    morceaux = _collecter(
        FournisseurOpenAI(nom="fictif", url_base="http://fictif", modele="modele-test")
    )
    assert [m.texte for m in morceaux if m.texte] == ["Réponse estimée."]
    final = morceaux[-1]
    # usage estimé : entrée ~ caracteres/4, sortie ~ caracteres émis/4
    assert final.jetons_entree == len("Question ?") // 4
    assert final.jetons_sortie == len("Réponse estimée.") // 4


def test_generer_flux_contenu_et_usage_dans_un_meme_evenement(monkeypatch):
    # Google en charge regroupe le contenu et l'usage dans un seul
    # événement — l'usage ne doit pas masquer le texte qu'il porte.
    _brancher_transport(
        monkeypatch,
        _serveur_sse(
            [
                'data: {"choices":[{"delta":{"content":"Réponse groupée."}}],"usage":{"prompt_tokens":7,"completion_tokens":4}}',
                "data: [DONE]",
            ]
        ),
    )
    morceaux = _collecter(
        FournisseurOpenAI(nom="fictif", url_base="http://fictif", modele="modele-test")
    )
    assert [m.texte for m in morceaux if m.texte] == ["Réponse groupée."]
    assert (morceaux[-1].jetons_entree, morceaux[-1].jetons_sortie) == (7, 4)


# ── Point de génération en flux ──────────────────────────────────────────


class FournisseurFluxFactice:
    def __init__(
        self,
        fragments: tuple[str, ...] = ("Bonjour ", "le monde"),
        echec: Exception | None = None,
        fin_motif: str | None = "stop",
    ) -> None:
        self.fragments = fragments
        self.echec = echec
        self.fin_motif = fin_motif
        self.appels = 0

    async def verifier(self) -> bool:
        return True

    async def generer_flux(self, messages, temperature, max_jetons):
        self.appels += 1
        yield MorceauFlux(modele="modele-fictif")
        for texte in self.fragments:
            yield MorceauFlux(texte=texte)
            if self.echec is not None:
                raise self.echec
        yield MorceauFlux(
            jetons_entree=8, jetons_sortie=12, fin_motif=self.fin_motif
        )


def _application(fournisseur, requetes_minute: int = 100) -> TestClient:
    application = create_app()
    application.state.fournisseurs = {"fictif": fournisseur}
    application.state.quota = GestionnaireQuota(
        None, limite_requetes_minute=requetes_minute, limite_jetons_jour=100_000
    )
    return TestClient(application)


def _evenements(reponse) -> list[dict]:
    """Décode les événements SSE d'une réponse TestClient."""
    evenements: list[dict] = []
    type_courant: str | None = None
    for ligne in reponse.iter_lines():
        if ligne.startswith("event: "):
            type_courant = ligne[len("event: ") :]
        elif ligne.startswith("data: ") and type_courant:
            charge = json.loads(ligne[len("data: ") :])
            assert charge["type"] == type_courant
            evenements.append(charge)
            type_courant = None
    return evenements


def _attendre_consignation(delai: float = 2.0) -> None:
    """La consignation du quota part en tâche de fond — on l'attend."""
    echeance = time.time() + delai
    while _taches_fond and time.time() < echeance:
        time.sleep(0.01)
    assert not _taches_fond, "consignation de fond non terminée"


def test_flux_sequence_complete_et_quota_consigne():
    fournisseur = FournisseurFluxFactice()
    client = _application(fournisseur)
    with client.stream("POST", "/internal/generation/stream", headers=ENTETES, json=DEMANDE) as reponse:
        assert reponse.status_code == 200
        assert reponse.headers["content-type"].startswith("text/event-stream")
        evenements = _evenements(reponse)
    assert evenements[0] == {
        "type": "debut",
        "fournisseur": "fictif",
        "modele": "modele-fictif",
    }
    assert [e["texte"] for e in evenements[1:-1]] == ["Bonjour ", "le monde"]
    fin = evenements[-1]
    assert fin["type"] == "fin"
    assert (fin["jetons_entree"], fin["jetons_sortie"]) == (8, 12)
    assert fin["attendu"] is False
    _attendre_consignation()
    etat = client.get("/internal/generation/quota", headers=ENTETES).json()
    assert etat["requetes_minute"]["utilisees"] == 1
    assert etat["jetons_jour"]["utilises"] == 12


def test_flux_tronque_signale_complet_false():
    fournisseur = FournisseurFluxFactice(fin_motif="length")
    client = _application(fournisseur)
    with client.stream("POST", "/internal/generation/stream", headers=ENTETES, json=DEMANDE) as reponse:
        assert reponse.status_code == 200
        evenements = _evenements(reponse)
    fin = evenements[-1]
    assert fin["type"] == "fin"
    assert fin["complet"] is False


def test_flux_erreur_fournisseur_apres_debut():
    fournisseur = FournisseurFluxFactice(fragments=("Début…",), echec=ErreurFournisseur("coupure"))
    client = _application(fournisseur)
    with client.stream("POST", "/internal/generation/stream", headers=ENTETES, json=DEMANDE) as reponse:
        assert reponse.status_code == 200
        evenements = _evenements(reponse)
    assert evenements[0]["type"] == "debut"
    assert evenements[1] == {"type": "fragment", "texte": "Début…"}
    assert evenements[-1]["type"] == "erreur"
    assert "coupure" in evenements[-1]["erreur"]


def test_flux_quota_plateforme_429_avant_ouverture():
    client = _application(FournisseurFluxFactice(), requetes_minute=0)
    reponse = client.post("/internal/generation/stream", headers=ENTETES, json=DEMANDE)
    assert reponse.status_code == 429
    assert "quota plateforme" in reponse.json()["detail"]


def test_flux_protege_par_jeton():
    client = _application(FournisseurFluxFactice())
    reponse = client.post("/internal/generation/stream", json=DEMANDE)
    assert reponse.status_code == 401


def test_flux_fournisseur_inconnu_rejete():
    client = _application(FournisseurFluxFactice())
    reponse = client.post(
        "/internal/generation/stream",
        headers=ENTETES,
        json={**DEMANDE, "fournisseur": "inconnu"},
    )
    assert reponse.status_code == 400
