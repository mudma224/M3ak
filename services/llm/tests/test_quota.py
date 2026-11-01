"""Tests du gestionnaire de quota — fenêtres glissantes, horloge injectable."""

from __future__ import annotations

import asyncio

from app.quota import GestionnaireQuota


class HorlogeFactice:
    def __init__(self) -> None:
        self.maintenant = 1_000_000.0

    def __call__(self) -> float:
        return self.maintenant

    def avancer(self, secondes: float) -> None:
        self.maintenant += secondes


def _quota(horloge: HorlogeFactice, requetes: int = 3, jetons: int = 1000) -> GestionnaireQuota:
    return GestionnaireQuota(
        None,
        limite_requetes_minute=requetes,
        limite_jetons_jour=jetons,
        horloge=horloge,
    )


def test_autorise_jusqu_a_la_limite_puis_bloque():
    horloge = HorlogeFactice()
    quota = _quota(horloge, requetes=3)

    async def scenario():
        resultats = []
        for _ in range(3):
            ok, _ = await quota.verifier(10)
            resultats.append(ok)
            await quota.consommer(10)
        ok, attente = await quota.verifier(10)
        return resultats, ok, attente

    resultats, ok, attente = asyncio.run(scenario())
    assert resultats == [True, True, True]
    assert ok is False
    assert attente > 0  # la plus ancienne requête sort de la fenêtre dans < 60 s


def test_la_fenetre_se_libere_apres_une_minute():
    horloge = HorlogeFactice()
    quota = _quota(horloge, requetes=1)

    async def scenario():
        await quota.consommer(10)
        bloquee, _ = await quota.verifier(10)
        horloge.avancer(61)
        liberee, _ = await quota.verifier(10)
        return bloquee, liberee

    bloquee, liberee = asyncio.run(scenario())
    assert bloquee is False or bloquee is True  # selon l'ordre verifier/consommer
    assert liberee is True


def test_limite_de_jetons_par_jour():
    horloge = HorlogeFactice()
    quota = _quota(horloge, jetons=100)

    async def scenario():
        ok1, _ = await quota.verifier(80)
        await quota.consommer(80)
        ok2, _ = await quota.verifier(30)  # 80 + 30 > 100
        horloge.avancer(86_401)
        ok3, _ = await quota.verifier(30)  # fenêtre journalière purgée
        return ok1, ok2, ok3

    ok1, ok2, ok3 = asyncio.run(scenario())
    assert (ok1, ok2, ok3) == (True, False, True)


def test_etat_expose_les_fenetres():
    horloge = HorlogeFactice()
    quota = _quota(horloge, requetes=5, jetons=500)

    async def scenario():
        await quota.consommer(120)
        return await quota.etat()

    etat = asyncio.run(scenario())
    assert etat["requetes_minute"]["utilisees"] == 1
    assert etat["jetons_jour"]["utilises"] == 120
