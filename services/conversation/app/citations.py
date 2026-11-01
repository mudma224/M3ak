"""Vérification mécanique des citations — aucune réponse ne part sans contrôle.

Chaque renvoi [n] doit désigner un passage effectivement fourni ; chaque
citation verbatim « … » doit se retrouver (à espaces normalisés) dans l'un
des passages cités. Une citation invérifiable rend la réponse entière
invérifiable : elle est refusée plutôt que servie (contrainte §7.1).
Les renvois vers des passages inexistants sont retirés du texte et
comptés comme rejetés.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_MARQUEUR = re.compile(r"\[(\d{1,2})\]")
_VERBATIM = re.compile(r"«([^»]+)»")

# Pli typographique : la vérification porte sur le LIBELLÉ cité, pas sur la
# graphie — les modèles glissent parfois apostrophes courbes, tirets longs
# ou points de suspension là où le corpus porte les formes simples.
_PLI = str.maketrans(
    {
        "\u2019": "'",  # apostrophe typographique
        "\u2018": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u2026": "...",
    }
)


# Unification arabe (règles actées phase 2, comme le plein texte) :
# diacritiques, tanwin, sukun, madda et tatweel retirés ; alef/hamza et
# yāʾ unifiés — GLM peut citer l'arabe sous une orthographe équivalente.
_ARABE_LIGER = re.compile(r"[\u064B-\u0652\u0670\u0640]")


def _normaliser_lettres(texte: str) -> str:
    texte = _ARABE_LIGER.sub("", texte)
    return (
        texte.replace("\u0623", "\u0627")
        .replace("\u0625", "\u0627")
        .replace("\u0622", "\u0627")
        .replace("\u0649", "\u064a")
    )


def normaliser_pour_citation(texte: str) -> str:
    """Pli typographique + espaces (y compris sauts de ligne et insécables)
    réduits à un seul — la comparaison verbatim tolère la coupe de ligne et
    la variante de graphie, rien d'autre."""
    return " ".join(_normaliser_lettres(texte).translate(_PLI).split())


@dataclass
class VerificationCitations:
    texte: str
    ids_valides: set[int] = field(default_factory=set)
    ids_rejetes: set[int] = field(default_factory=set)
    valide: bool = True
    motif: str | None = None


def verifier_citations(
    texte: str,
    passages: dict[int, str],
    exiger_renvoi: bool = True,
    exiger_couverture: bool = False,
) -> VerificationCitations:
    """Contrôle les renvois et les citations verbatim d'un texte généré.

    `passages` : numéro fourni au modèle → texte exact qui lui a été montré.
    `exiger_renvoi=False` (vérification progressive, phrase à phrase) : un
    texte sans aucun renvoi n'est pas encore invalide — tout est revérifié
    cumulativement à chaque phrase, et la vérification stricte finale porte
    toujours sur le texte complet.
    `exiger_couverture=True` (réponse « sans réponse » : description des
    passages consultés) : chaque passage CITÉ doit porter au moins une
    citation retrouvée EN LUI — une description sans citation vérifiable
    serait de la prose libre sur le corpus, exactement ce que ce mode
    doit empêcher.
    """
    cites = {int(m) for m in _MARQUEUR.findall(texte)}
    valides = {n for n in cites if n in passages}
    rejetes = cites - valides

    texte_nettoye = _MARQUEUR.sub(
        lambda m: m.group(0) if int(m.group(1)) in valides else "", texte
    ).strip()

    if not valides:
        if not exiger_renvoi:
            return VerificationCitations(
                texte=texte_nettoye, ids_valides=valides, ids_rejetes=rejetes
            )
        return VerificationCitations(
            texte=texte_nettoye,
            ids_valides=valides,
            ids_rejetes=rejetes,
            valide=False,
            motif="aucune citation vérifiable (aucun renvoi [n] vers un passage fourni)",
        )

    corpus_par_passage = {
        n: normaliser_pour_citation(passages[n]) for n in valides
    }
    corpus = " ".join(corpus_par_passage.values())
    citations = [
        normaliser_pour_citation(citation)
        for citation in _VERBATIM.findall(texte_nettoye)
    ]
    for citation in citations:
        if citation not in corpus:
            return VerificationCitations(
                texte=texte_nettoye,
                ids_valides=valides,
                ids_rejetes=rejetes,
                valide=False,
                motif="citation verbatim introuvable dans les passages cités",
            )
    if exiger_couverture:
        for numero in sorted(valides):
            if not any(citation in corpus_par_passage[numero] for citation in citations):
                return VerificationCitations(
                    texte=texte_nettoye,
                    ids_valides=valides,
                    ids_rejetes=rejetes,
                    valide=False,
                    motif=(
                        f"passage [{numero}] décrit sans citation vérifiable en lui — "
                        "une description du corpus se cite, elle ne se résume pas librement"
                    ),
                )

    return VerificationCitations(
        texte=texte_nettoye, ids_valides=valides, ids_rejetes=rejetes, valide=True
    )
