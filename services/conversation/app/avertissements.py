"""Avertissements contextuels — droits, argent ou liberté en jeu (contrainte §7.6).

Détection locale par mots-clés sur la question, restitution dans la
langue de travail. Les libellés sont sobres et n'affirment rien : ils
orientent vers un professionnel, sans promesse de certitude.
"""

from __future__ import annotations

# (mots-clés, libellé arabe, libellé français)
_CATEGORIES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (
        ("divorce", "طلاق", "زواج", "ميراث", "succession", "héritage", "garde", "حضانة"),
        "تنطوي مسائل الأسرة والزواج والوراثة على حقوق قانونية هامة؛ استشر محاميا أو عدلا مختصا قبل أي خطوة.",
        (
            "Les questions de famille, divorce et succession engagent des droits importants ; "
            "consultez un avocat ou un adoul avant toute démarche."
        ),
    ),
    (
        ("licenci", "فصل", "salaire", "أجور", "اجور", "travail", "العمل", "شغل"),
        (
            "قد تكون حقوقك في العمل (فصل، أجور، تعويضات) موضع مسائل قانونية دقيقة؛ "
            "استشر مفتشية العمل أو محاميا قبل أي تنازل."
        ),
        (
            "Vos droits au travail (licenciement, salaires, indemnités) soulèvent des règles "
            "précises ; rapprochez-vous de l'inspection du travail ou d'un avocat avant tout renoncement."
        ),
    ),
    (
        ("argent", "crédit", "قرض", "impôt", "ضريبة", "pénal", "prison", "حبس", "سرقة", "vol", "دين"),
        (
            "تنطوي هذه المسألة على حقوق أو التزامات مالية أو جنائية؛ لا تعتمد على معلومة عامة وحدها، "
            "واستشر مختصا في أقرب وقت."
        ),
        (
            "Cette question touche des engagements financiers ou des matières pénales ; "
            "ne vous fiez pas à une information générale seule, consultez un professionnel rapidement."
        ),
    ),
)


def detecter_avertissements(question: str, langue: str) -> list[str]:
    """Avertissements applicables à la question, dans la langue de travail."""
    question_basse = question.lower()
    en_francais = langue == "fr"
    avertissements: list[str] = []
    for mots_cles, arabe, francais in _CATEGORIES:
        if any(mot in question_basse for mot in mots_cles):
            avertissements.append(francais if en_francais else arabe)
    return avertissements
