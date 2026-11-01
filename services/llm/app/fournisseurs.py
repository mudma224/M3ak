"""Fournisseurs de génération (phase 2, décision actée : commutables).

Z.ai (GLM), Google AI Studio (couche OpenAI de Gemini) et serveur local
(LM Studio en développement ; Ollama ou llama.cpp server à l'hébergement)
parlent le même protocole compatible OpenAI : une seule classe, trois
instances — la bascule est une variable de configuration, aucune
réécriture. Génération en un bloc ou en flux (étape 3bis). Toute
indisponibilité est convertie en exception typée : le quota et la file
d'attente décident de la suite.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

import httpx
from m3ak_contracts import MessageGeneration

# Modèles à raisonnement (Gemma 4 hybride…) : le bloc de réflexion peut
# arriver enbalisé dans le contenu — il est retiré de la réponse finale.
_BLOC_REFLEXION = re.compile(r"<think>.*?</think>", re.DOTALL)


def complet_depuis_motif(fin_motif: str | None) -> bool | None:
    """None : le fournisseur ne signale pas de motif ; True : fin naturelle ;
    False : budget de jetons atteint ou fin anormale (réponse tronquée)."""
    if fin_motif is None:
        return None
    return fin_motif == "stop"


class DonneesGeneration:
    """Résultat brut d'une génération, avant journalisation."""

    def __init__(
        self,
        texte: str,
        modele: str,
        jetons_entree: int | None = None,
        jetons_sortie: int | None = None,
        complet: bool | None = None,
    ) -> None:
        self.texte = texte
        self.modele = modele
        self.jetons_entree = jetons_entree
        self.jetons_sortie = jetons_sortie
        self.complet = complet


class ErreurFournisseur(RuntimeError):
    """Le fournisseur ne répond pas ou répond une charge invalide."""


class ErreurQuotaFournisseur(ErreurFournisseur):
    """Le fournisseur a répondu 429 — son propre quota est épuisé."""


class MorceauFlux:
    """Élément interne d'un flux de fournisseur : soit le modèle résolu,
    soit un fragment (contenu et/ou réflexion), soit l'usage final portant
    le motif de fin (stop, length — troncature…)."""

    def __init__(
        self,
        texte: str | None = None,
        raisonnement: str | None = None,
        modele: str | None = None,
        jetons_entree: int | None = None,
        jetons_sortie: int | None = None,
        fin_motif: str | None = None,
    ) -> None:
        self.texte = texte
        self.raisonnement = raisonnement
        self.modele = modele
        self.jetons_entree = jetons_entree
        self.jetons_sortie = jetons_sortie
        self.fin_motif = fin_motif


_BALISE_OUVERTURE = "<think>"
_BALISE_FERMETURE = "</think>"


def _longueur_suffixe_balise(texte: str, balise: str) -> int:
    """Longueur du suffixe de `texte` qui est un préfixe propre de `balise`
    (une balise peut être coupée entre deux fragments du flux)."""
    for longueur in range(min(len(texte), len(balise) - 1), 0, -1):
        if texte.endswith(balise[:longueur]):
            return longueur
    return 0


class FiltrePensee:
    """Retire les blocs <think>…</think> d'un flux de fragments, balise
    coupée entre deux morceaux comprise — l'équivalent en continu de
    `_BLOC_REFLEXION` utilisé pour les réponses en un bloc."""

    def __init__(self) -> None:
        self._dans_reflexion = False
        self._memoire = ""

    def nourrir(self, fragment: str) -> str:
        self._memoire += fragment
        emis: list[str] = []
        while self._memoire:
            if self._dans_reflexion:
                index = self._memoire.find(_BALISE_FERMETURE)
                if index == -1:
                    garde = _longueur_suffixe_balise(self._memoire, _BALISE_FERMETURE)
                    self._memoire = self._memoire[len(self._memoire) - garde :] if garde else ""
                    break
                self._memoire = self._memoire[index + len(_BALISE_FERMETURE) :]
                self._dans_reflexion = False
            else:
                index = self._memoire.find(_BALISE_OUVERTURE)
                if index != -1:
                    emis.append(self._memoire[:index])
                    self._memoire = self._memoire[index + len(_BALISE_OUVERTURE) :]
                    self._dans_reflexion = True
                    continue
                garde = _longueur_suffixe_balise(self._memoire, _BALISE_OUVERTURE)
                if garde:
                    emis.append(self._memoire[:-garde])
                    self._memoire = self._memoire[-garde:]
                else:
                    emis.append(self._memoire)
                    self._memoire = ""
                break
        return "".join(emis)

    def vider(self) -> str:
        """Fin de flux : restitue la mémoire retenue, sauf réflexion inachevée."""
        if self._dans_reflexion:
            return ""
        reste, self._memoire = self._memoire, ""
        return reste


def morceau_depuis_charge(corps: dict) -> MorceauFlux | None:
    """Extrait d'un événement SSE du protocole OpenAI ce qu'il porte :
    un fragment de contenu, un fragment de réflexion (modèles à
    raisonnement), le motif de fin (stop / length — troncature) et/ou
    l'usage final (dernier événement quand stream_options.include_usage
    est honoré)."""
    usage = corps.get("usage") or {}
    choix = (corps.get("choices") or [{}])[0]
    delta = choix.get("delta") or {}
    fin_motif = choix.get("finish_reason")
    if not (delta.get("content") or delta.get("reasoning_content") or usage or fin_motif):
        return None
    return MorceauFlux(
        texte=delta.get("content"),
        raisonnement=delta.get("reasoning_content"),
        jetons_entree=usage.get("prompt_tokens"),
        jetons_sortie=usage.get("completion_tokens"),
        fin_motif=fin_motif,
    )


def extraire_reponse(corps: dict, modele_defaut: str) -> DonneesGeneration:
    """Extrait la réponse d'une charge chat/completions — fonction pure
    (testée) : gère les modèles à raisonnement (content vide, réflexion
    déposée à part ou enbalisée) et la variante de clés d'usage."""
    choix = corps["choices"][0]
    message = choix.get("message") or {}
    texte = (message.get("content") or "").strip()
    if not texte:
        # Gemma 4 hybride : le budget de jetons peut être entièrement
        # consommé par le raisonnement — on restitue ce raisonnement
        # plutôt qu'une réponse vide (max_jetons plus élevé recommandé).
        texte = (message.get("reasoning_content") or "").strip()
    texte = _BLOC_REFLEXION.sub("", texte).strip()
    usage = corps.get("usage") or {}
    return DonneesGeneration(
        texte=texte,
        modele=corps.get("model", modele_defaut),
        jetons_entree=usage.get("prompt_tokens"),
        jetons_sortie=usage.get("completion_tokens"),
        complet=complet_depuis_motif(choix.get("finish_reason")),
    )


class FournisseurOpenAI:
    """Un fournisseur de génération parlant /chat/completions (OpenAI)."""

    def __init__(
        self,
        nom: str,
        url_base: str,
        modele: str,
        cle: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.nom = nom
        self._url_base = url_base.rstrip("/")
        self._modele = modele
        self._cle = cle
        self._timeout = timeout
        self._modele_resolu: str | None = None

    def _entetes(self) -> dict[str, str]:
        if not self._cle:
            return {}
        return {"Authorization": f"Bearer {self._cle}"}

    async def verifier(self) -> bool:
        """Vrai si le fournisseur répond à /models (utilisé par /ready)."""
        try:
            async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
                reponse = await client.get(
                    f"{self._url_base}/models", headers=self._entetes()
                )
                return reponse.status_code == 200
        except Exception:
            return False

    async def _resoudre_modele(self, client: httpx.AsyncClient) -> str:
        """Modèle explicite, sinon auto-détection (premier de /models) —
        pratique pour un serveur local mono-modèle."""
        if self._modele:
            return self._modele
        if self._modele_resolu:
            return self._modele_resolu
        reponse = await client.get(f"{self._url_base}/models", headers=self._entetes())
        if reponse.status_code != 200:
            raise ErreurFournisseur(
                f"{self.nom} : /models injoignable (HTTP {reponse.status_code})"
            )
        modeles = reponse.json().get("data", [])
        if not modeles:
            raise ErreurFournisseur(f"{self.nom} : aucun modèle disponible")
        self._modele_resolu = modeles[0]["id"]
        return self._modele_resolu

    async def generer(
        self, messages: list[MessageGeneration], temperature: float, max_jetons: int
    ) -> DonneesGeneration:
        charge = {
            "messages": [{"role": m.role, "content": m.contenu} for m in messages],
            "temperature": temperature,
            "max_tokens": max_jetons,
        }
        async with httpx.AsyncClient(timeout=self._timeout, trust_env=False) as client:
            try:
                modele = await self._resoudre_modele(client)
                charge["model"] = modele
                reponse = await client.post(
                    f"{self._url_base}/chat/completions",
                    headers=self._entetes(),
                    json=charge,
                )
            except httpx.HTTPError as erreur:
                raise ErreurFournisseur(f"{self.nom} : {erreur}") from erreur
        if reponse.status_code == 429:
            raise ErreurQuotaFournisseur(
                f"{self.nom} : quota du fournisseur épuisé (HTTP 429)"
            )
        if reponse.status_code != 200:
            raise ErreurFournisseur(
                f"{self.nom} : HTTP {reponse.status_code} — {reponse.text[:200]}"
            )
        try:
            return extraire_reponse(reponse.json(), modele)
        except (ValueError, KeyError, IndexError) as erreur:
            raise ErreurFournisseur(f"{self.nom} : charge invalide") from erreur

    async def generer_flux(
        self, messages: list[MessageGeneration], temperature: float, max_jetons: int
    ) -> AsyncIterator[MorceauFlux]:
        """Génération en flux (étape 3bis). Cède, dans l'ordre : le modèle
        résolu, les fragments de contenu (blocs <think> filtrés en continu,
        repli sur le raisonnement si rien n'est émis — mêmes règles qu'en
        un bloc), puis l'usage final (estimé si le fournisseur ne le
        rapporte pas)."""
        charge = {
            "messages": [{"role": m.role, "content": m.contenu} for m in messages],
            "temperature": temperature,
            "max_tokens": max_jetons,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        async with httpx.AsyncClient(timeout=self._timeout, trust_env=False) as client:
            try:
                modele = await self._resoudre_modele(client)
            except httpx.HTTPError as erreur:
                raise ErreurFournisseur(f"{self.nom} : {erreur}") from erreur
            charge["model"] = modele
            yield MorceauFlux(modele=modele)

            filtre = FiltrePensee()
            brut: list[str] = []  # contenu reçu, pour le repli final
            reflexion: list[str] = []  # raisonnement reçu, repli ultime
            emis: list[str] = []  # contenu effectivement cédé
            usage: MorceauFlux | None = None
            fin_motif: str | None = None
            try:
                async for morceau in self._ouvrir_flux(client, charge):
                    # Contenu et réflexion AVANT l'usage : certains
                    # fournisseurs (Google en charge) regroupent contenu et
                    # usage dans un même événement — l'usage ne doit pas
                    # masquer le texte qu'il porte.
                    if morceau.raisonnement:
                        reflexion.append(morceau.raisonnement)
                    if morceau.texte:
                        brut.append(morceau.texte)
                        net = filtre.nourrir(morceau.texte)
                        if net:
                            emis.append(net)
                            yield MorceauFlux(texte=net)
                    if morceau.fin_motif:
                        fin_motif = morceau.fin_motif
                    if morceau.jetons_entree is not None or morceau.jetons_sortie is not None:
                        usage = morceau
                reste = filtre.vider()
                if reste:
                    emis.append(reste)
                    yield MorceauFlux(texte=reste)
                if not emis:
                    texte = _BLOC_REFLEXION.sub("", "".join(brut)).strip()
                    if not texte:
                        texte = _BLOC_REFLEXION.sub("", "".join(reflexion)).strip()
                    if texte:
                        emis.append(texte)
                        yield MorceauFlux(texte=texte)
            except httpx.HTTPError as erreur:
                raise ErreurFournisseur(f"{self.nom} : flux interrompu ({erreur})") from erreur

            jetons_entree = (
                usage.jetons_entree
                if usage and usage.jetons_entree is not None
                else sum(len(m.contenu) for m in messages) // 4
            )
            jetons_sortie = (
                usage.jetons_sortie
                if usage and usage.jetons_sortie is not None
                else sum(len(t) for t in emis) // 4
            )
            yield MorceauFlux(
                jetons_entree=jetons_entree,
                jetons_sortie=jetons_sortie,
                fin_motif=fin_motif,
            )

    async def _ouvrir_flux(
        self, client: httpx.AsyncClient, charge: dict
    ) -> AsyncIterator[MorceauFlux]:
        """Ouvre le flux SSE et cède les morceaux bruts du protocole. Si le
        fournisseur refuse stream_options (400 — tous ne le connaissent pas),
        une seule reprise sans : l'usage sera estimé côté service."""
        url = f"{self._url_base}/chat/completions"
        try:
            reponse = await client.send(
                client.build_request("POST", url, headers=self._entetes(), json=charge),
                stream=True,
            )
        except httpx.HTTPError as erreur:
            raise ErreurFournisseur(f"{self.nom} : {erreur}") from erreur
        if reponse.status_code == 400 and "stream_options" in charge:
            await reponse.aread()
            await reponse.aclose()
            charge.pop("stream_options")
            async for morceau in self._ouvrir_flux(client, charge):
                yield morceau
            return
        if reponse.status_code == 429:
            await reponse.aclose()
            raise ErreurQuotaFournisseur(
                f"{self.nom} : quota du fournisseur épuisé (HTTP 429)"
            )
        if reponse.status_code != 200:
            corps = (await reponse.aread()).decode("utf-8", "replace")
            await reponse.aclose()
            raise ErreurFournisseur(f"{self.nom} : HTTP {reponse.status_code} — {corps[:200]}")
        try:
            async for morceau in self._iterer_evenements(reponse):
                yield morceau
        finally:
            await reponse.aclose()

    async def _iterer_evenements(
        self, reponse: httpx.Response
    ) -> AsyncIterator[MorceauFlux]:
        """Décode les lignes SSE du protocole OpenAI (data: {…} / [DONE])."""
        async for ligne in reponse.aiter_lines():
            ligne = ligne.strip()
            if not ligne.startswith("data:"):
                continue
            donnees = ligne[len("data:") :].strip()
            if donnees == "[DONE]":
                return
            try:
                corps = json.loads(donnees)
            except json.JSONDecodeError:
                continue  # ligne parasite (commentaire, keep-alive)
            morceau = morceau_depuis_charge(corps)
            if morceau is not None:
                yield morceau
