"""Tests des points d'entrée — db et orchestrateur remplacés, sans infrastructure."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from m3ak_contracts import EvenementConversation, ReponseQuestion

from app import db
from app.main import create_app
from app.orchestrateur import ErreurGeneration, ErreurRecherche


def _reponse(session="sess-1", message="msg-1", refus=False, **ecarts) -> ReponseQuestion:
    return ReponseQuestion(
        session_id=session,
        message_id=message,
        reponse="Réponse vérifiée [1]." if not refus else "Refus digne.",
        langue_question="fr",
        refus=refus,
        **ecarts,
    )


class OrchestrateurFactice:
    def __init__(self, reponse=None, erreur=None, evenements=None):
        self.reponse = reponse or _reponse()
        self.erreur = erreur
        self.evenements = evenements or [
            EvenementConversation(type="statut", statut="recherche"),
            EvenementConversation(type="fragment", texte="Réponse vérifiée"),
            EvenementConversation(type="fin", reponse=self.reponse),
        ]
        self.questions = []

    async def poser(self, question, session_id):
        self.questions.append(question)
        if self.erreur is not None:
            raise self.erreur
        return self.reponse

    async def flux(self, question, session_id):
        self.questions.append(question)
        for evenement in self.evenements:
            yield evenement
        if self.erreur is not None:
            raise self.erreur


def _client(monkeypatch, orchestrateur, session_existe=True, fabrique=None):
    async def creer_session():
        return "sess-1"

    async def existe(session_id):
        return session_existe

    async def ajouter_message(*args, **kwargs):
        return "msg-1"

    async def historique(session_id, limite=100):
        return [
            {"role": "user", "contenu": "Question ?", "langue": "fr",
             "cree_le": "2026-10-02T10:00:00+00:00", "refus": False, "signale": False},
            {"role": "assistant", "contenu": "Réponse [1].", "langue": "fr",
             "cree_le": "2026-10-02T10:00:10+00:00", "refus": False, "signale": False,
             "metadonnees": {
                 "sources": [{
                     "unite_id": "seg-1", "famille": "segment", "document_id": "doc-1",
                     "titre": "Code des obligations", "source": "adala",
                     "collection": "textes_normatifs", "langue": "fr", "score": 0.9,
                 }],
                 "avertissements": ["Un avertissement."],
                 "repli_interlingue": False,
                 "texte_partiel": False,
                 "sans_reponse": False,
             }},
        ]

    async def signaler(message_id, motif):
        return message_id == "msg-1"

    async def supprimer_session(session_id):
        return session_id == "sess-1"

    monkeypatch.setattr(db, "creer_session", creer_session)
    monkeypatch.setattr(db, "session_existe", existe)
    monkeypatch.setattr(db, "ajouter_message", ajouter_message)
    monkeypatch.setattr(db, "historique", historique)
    monkeypatch.setattr(db, "signaler", signaler)
    monkeypatch.setattr(db, "supprimer_session", supprimer_session)

    application = create_app()
    application.state.fabrique_orchestrateur = fabrique or (
        lambda session_id, fournisseur=None: orchestrateur
    )
    return TestClient(application)


def test_creer_session(monkeypatch):
    async def creer_session():
        return "sess-42"

    monkeypatch.setattr(db, "creer_session", creer_session)
    client = TestClient(create_app())
    reponse = client.post("/sessions")
    assert reponse.status_code == 200
    assert reponse.json()["session_id"] == "sess-42"


def test_question_bloc_renvoie_la_reponse_verifiee(monkeypatch):
    orchestrateur = OrchestrateurFactice()
    client = _client(monkeypatch, orchestrateur)
    reponse = client.post(
        "/sessions/sess-1/messages", json={"question": "Quel est le délai ?"}
    )
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["reponse"] == "Réponse vérifiée [1]."
    assert orchestrateur.questions == ["Quel est le délai ?"]


def test_session_inconnue_repond_404(monkeypatch):
    client = _client(monkeypatch, OrchestrateurFactice(), session_existe=False)
    reponse = client.post(
        "/sessions/inconnue/messages", json={"question": "Question ?"}
    )
    assert reponse.status_code == 404


def test_recherche_indisponible_repond_503(monkeypatch):
    client = _client(
        monkeypatch, OrchestrateurFactice(erreur=ErreurRecherche("boom"))
    )
    reponse = client.post(
        "/sessions/sess-1/messages", json={"question": "Question ?"}
    )
    assert reponse.status_code == 503


def test_quota_llm_est_releve_tel_quel(monkeypatch):
    client = _client(
        monkeypatch, OrchestrateurFactice(erreur=ErreurGeneration("429 amont", 429))
    )
    reponse = client.post(
        "/sessions/sess-1/messages", json={"question": "Question ?"}
    )
    assert reponse.status_code == 429


def _lire_evenements(reponse):
    evenements = []
    type_courant = None
    for ligne in reponse.iter_lines():
        if ligne.startswith("event: "):
            type_courant = ligne[len("event: ") :]
        elif ligne.startswith("data: ") and type_courant:
            evenements.append(json.loads(ligne[len("data: ") :]))
            type_courant = None
    return evenements


def test_flux_sse_evenements_ordonnes(monkeypatch):
    client = _client(monkeypatch, OrchestrateurFactice())
    with client.stream(
        "POST", "/sessions/sess-1/messages/flux", json={"question": "Question ?"}
    ) as reponse:
        assert reponse.status_code == 200
        evenements = _lire_evenements(reponse)
    assert evenements[0]["type"] == "statut"
    assert any(e["type"] == "fragment" for e in evenements)
    assert evenements[-1]["type"] == "fin"
    assert evenements[-1]["reponse"]["reponse"] == "Réponse vérifiée [1]."


def test_flux_sse_erreur_apres_ouverture(monkeypatch):
    client = _client(
        monkeypatch, OrchestrateurFactice(erreur=ErreurRecherche("boom"))
    )
    with client.stream(
        "POST", "/sessions/sess-1/messages/flux", json={"question": "Question ?"}
    ) as reponse:
        assert reponse.status_code == 200
        evenements = _lire_evenements(reponse)
    assert evenements[-1]["type"] == "erreur"


def test_historique_de_la_session(monkeypatch):
    client = _client(monkeypatch, OrchestrateurFactice())
    reponse = client.get("/sessions/sess-1")
    assert reponse.status_code == 200
    corps = reponse.json()
    assert [m["role"] for m in corps["messages"]] == ["user", "assistant"]
    # L'affichage restaure à l'identique : sources citées et marqueurs.
    assistant = corps["messages"][1]
    assert assistant["sources"][0]["document_id"] == "doc-1"
    assert assistant["avertissements"] == ["Un avertissement."]
    assert assistant["texte_partiel"] is False and assistant["sans_reponse"] is False


def test_signalement_et_message_inconnu(monkeypatch):
    client = _client(monkeypatch, OrchestrateurFactice())
    ok = client.post("/messages/msg-1/signaler", json={"motif": "réponse erronée"})
    assert ok.status_code == 200 and ok.json()["signale"] is True
    inconnu = client.post("/messages/autre/signaler", json={"motif": "x"})
    assert inconnu.status_code == 404


def test_suppression_de_session_et_session_inconnue(monkeypatch):
    client = _client(monkeypatch, OrchestrateurFactice())
    ok = client.delete("/sessions/sess-1")
    assert ok.status_code == 204
    inconnue = client.delete("/sessions/autre")
    assert inconnue.status_code == 404


def test_option_fournisseur_acceptee_et_transmise(monkeypatch):
    # L'option de test/évaluation traverse DemandeQuestion puis la fabrique.
    recues = []

    class Sonde:
        async def poser(self, question, session_id):
            return _reponse()

    def fabrique(session_id, fournisseur=None):
        recues.append(fournisseur)
        return Sonde()

    client = _client(monkeypatch, OrchestrateurFactice(), fabrique=fabrique)
    reponse = client.post(
        "/sessions/sess-1/messages",
        json={"question": "Question ?", "fournisseur": "local"},
    )
    assert reponse.status_code == 200
    assert recues == ["local"]
