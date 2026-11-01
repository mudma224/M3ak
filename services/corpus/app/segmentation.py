"""Grammaire de segmentation des textes juridiques marocains (jalon B).

Segmentation structurelle par détection des frontières d'articles, en
français et en arabe, validée par la cohérence de la numérotation.

Règles portées (AGENTS.md §5) :
- frontières d'articles selon les familles de textes marocains ;
- contexte hiérarchique (livre / chapitre / section) porté par chaque segment ;
- préambule en segment dédié (contenu avant le premier article) ;
- un article n'est jamais coupé : tableaux et alinéas restent dans l'article
  (le sous-découpage des articles longs et le regroupement des articles
  courts sont des décisions d'indexation, pas de segmentation) ;
- « chercher fin, restituer entier » : chaque segment porte les numéros
  d'articles nécessaires à la restitution complète.

Ambiguïté connue : « الفصل » désigne l'unité d'article dans les dahirs
anciens mais un niveau structurel dans certains textes modernes. La
désambiguïsation : si le texte contient des « المادة » numérotées, alors
« الفصل » est structurel ; sinon c'est l'unité d'article.

Ce module est pur (aucune entrée-sortie) : l'extraction PDF et l'écriture
des résultats appartiennent aux harnais et au pipeline du service corpus.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import pairwise

CHIFFRES_ORIENTAUX = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
TATWEEL = "\u0640"
ESPACE_INSECABLE = "\u00a0"

NUMERO_FR = r"(?:premier|PREMIER|Premier|\d+(?:\s*er|\s*ère)?(?:\s*[-–]\s*\d+)?(?:\s*(?:bis|ter|quater|quinquies))?)"
ROMAIN_BRUITE = r"[IVXLClm]+"

# Famille stricte : la ligne ne contient QUE la frontière (vraie mise en page).
RE_ARTICLE_FR = re.compile(
    rf"^\s*Article\s+({NUMERO_FR})\s*[:.\-–]?\s*$", re.IGNORECASE
)
# Famille collée : le texte suit sur la même ligne — dahirs anciens (« Art. 4. »),
# lois modificatives (« Article 503-1 - Est puni… »), tables analytiques.
# Utilisée seulement si le document n'a aucune frontière stricte.
RE_ARTICLE_FR_ABRÉGÉ = re.compile(
    rf"^\s*Art\.?\s+({NUMERO_FR})\s*[.\-–:]\s*\S", re.IGNORECASE
)
RE_ARTICLE_FR_LIGNE_COURTE = re.compile(
    rf"^\s*Article\s+({NUMERO_FR})\s*[:.\-–]\s*\S.{{0,60}}$", re.IGNORECASE
)
RE_STRUCTURE_FR = re.compile(
    rf"^\s*(Livre|Titre|Chapitre|Sous[- ]section|Section|Partie)\s+"
    rf"(préliminaire|PREMIER|premier|première|{ROMAIN_BRUITE}|\d+)\s*[:.\-–]?\s*(.{{0,120}})$",
    re.IGNORECASE,
)

# ───────────────────────── Grammaire arabe (raisonnée sur corpus réel) ─────────────────────────
# Formes rencontrées sur Adala (septembre 2026) :
#  - textes modernes : « المادة 12 » / « المادة 12: نص… » (chiffres occidentaux
#    ou orientaux), articles numérotés ;
#  - dahirs anciens et textes courts : « الفصل الاول / الفصل الثاني / الفصل
#    الثالث » — الفصل + ORDINAL EN LETTRES (avec ou sans hamza : الاول/الأول),
#    parfois suivi du numéro de note de bas de page collé (« الفصل الاول 2 ») ;
#  - structures : الكتاب / الباب / القسم / الجزء + ordinal ou chiffre.
# Règle de désambiguïsation : الفصل est l'unité d'article seulement si le
# texte ne numérote pas par المادة ; sinon c'est un niveau structurel.

ORDINAL_AR_VERS_ENTIER = {
    "الأول": 1, "الاول": 1, "الأولى": 1, "الاولى": 1,
    "الثاني": 2, "الثانية": 2,
    "الثالث": 3, "الثالثة": 3,
    "الرابع": 4, "الرابعة": 4,
    "الخامس": 5, "الخامسة": 5,
    "السادس": 6, "السادسة": 6,
    "السابع": 7, "السابعة": 7,
    "الثامن": 8, "الثامنة": 8,
    "التاسع": 9, "التاسعة": 9,
    "العاشر": 10, "العاشرة": 10,
    "الحادي عشر": 11, "الحادية عشر": 11,
    "الثاني عشر": 12, "الثانية عشر": 12,
    "الثالث عشر": 13, "الثالثة عشر": 13,
    "الرابع عشر": 14, "الرابعة عشر": 14,
    "الخامس عشر": 15, "الخامسة عشر": 15,
    "السادس عشر": 16, "السادسة عشر": 16,
    "السابع عشر": 17, "السابعة عشر": 17,
    "الثامن عشر": 18, "الثامنة عشر": 18,
    "التاسع عشر": 19, "التاسعة عشر": 19,
}
ORDINAUX_AR = "|".join(sorted(ORDINAL_AR_VERS_ENTIER, key=len, reverse=True))
NUMERO_AR = r"(?:الوحيدة|واحدة|الأولى|الاولى|[0-9٠-٩]+(?:\s*مكرر)?)"

RE_ARTICLE_MADA = re.compile(rf"^\s*المادّ?ة\s+({NUMERO_AR})\s*[:.\-–]?\s*$")
RE_ARTICLE_MADA_COURT = re.compile(
    rf"^\s*المادّ?ة\s+({NUMERO_AR})\s*[:.\-–]\s*\S.{{0,80}}$"
)
RE_ARTICLE_FASL_CHIFFRE = re.compile(r"^\s*الفصل\s+([0-9٠-٩]+)\s*[:.\-–]?\s*$")
# Ordinal en lettres, numéro de note de bas de page collé toléré (1-2 chiffres).
RE_ARTICLE_FASL_ORDINAL = re.compile(
    rf"^\s*الفصل\s+({ORDINAUX_AR})(?:\s+[0-9]{{1,2}})?\s*[:.\-–]?\s*$"
)
RE_ARTICLE_FASL_ORDINAL_COURT = re.compile(
    rf"^\s*الفصل\s+({ORDINAUX_AR})\s*[:.\-–]\s*\S.{{0,80}}$"
)
RE_MADA_EXISTE = re.compile(r"^\s*المادّ?ة\s+", re.MULTILINE)
RE_STRUCTURE_AR = re.compile(
    rf"^\s*(الكتاب|الباب|القسم|الجزء|الفصل)\s+({ORDINAUX_AR}|[0-9٠-٩]+)\s*[:.\-–]?\s*(.{{0,120}})$"
)

NIVEAUX_AR = {
    "الكتاب": "livre",
    "الجزء": "livre",
    "الباب": "chapitre",
    "الفصل": "chapitre",
    "القسم": "section",
}
NIVEAUX_FR = {
    "livre": "livre",
    "partie": "livre",
    "titre": "chapitre",
    "chapitre": "chapitre",
    "section": "section",
    "sous-section": "section",
    "sous section": "section",
}


def normaliser(texte: str) -> str:
    """Normalisations légères avant segmentation (sans altérer le fond)."""
    texte = texte.translate(CHIFFRES_ORIENTAUX)
    texte = texte.replace(TATWEEL, "").replace(ESPACE_INSECABLE, " ")
    texte = re.sub(r"[ \t]+", " ", texte)
    return texte


def numeral_vers_entier(brut: str) -> int | None:
    """Convertit « premier/12/2-12/XII/الثالث/الاول… » en entier tête de numéro."""
    brut = brut.strip()
    if re.fullmatch(r"(?i)premier|préliminaire", brut):
        return 1
    if brut in ORDINAL_AR_VERS_ENTIER:  # ordinaux arabes, avec ou sans hamza
        return ORDINAL_AR_VERS_ENTIER[brut]
    arabe = {"الوحيدة": 1, "واحدة": 1, "الأولى": 1, "الاولى": 1}
    if brut in arabe:
        return arabe[brut]
    if brut.isdigit():
        return int(brut)
    correspondance = re.match(r"(\d+)", brut)
    if correspondance:
        return int(correspondance.group(1))
    romain = brut.upper().translate(str.maketrans({"l": "I", "m": "M"}))
    valeurs = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    if not romain or any(c not in valeurs for c in romain):
        return None
    total = 0
    for courant, suivant in pairwise(romain):
        if valeurs[courant] < valeurs[suivant]:
            total -= valeurs[courant]
        else:
            total += valeurs[courant]
    return total + valeurs[romain[-1]]


@dataclass
class Frontiere:
    """Une frontière détectée : article ou structure hiérarchique."""

    ligne: int
    genre: str  # "article" | "structure"
    numero: int | None
    numero_brut: str
    niveau: str | None = None  # livre | chapitre | section
    libelle: str = ""
    principale: bool = True  # False pour les frontières d'une série parallèle
    saut: bool = False  # True si prise par re-synchronisation (saut de numérotation)


@dataclass
class Avertissement:
    ligne: int
    message: str


@dataclass
class ResultatSegmentation:
    preambule: str = ""
    articles: list[dict] = field(default_factory=list)
    avertissements: list[Avertissement] = field(default_factory=list)
    score_numerotation: float = 0.0
    langue: str = "fr"

    @property
    def coherent(self) -> bool:
        return self.score_numerotation >= 0.9 and len(self.articles) > 0


def _frontieres_ar(lignes: list[str]) -> list[Frontiere]:
    """Frontières d'un texte ARABE normalisé — grammaire arabe seule.

    Désambiguïsation المادة/الفصل en deux passes : المادة est l'unité
    d'articles si le texte contient au moins une VRAIE frontière « المادة N »
    isolée sur sa ligne — pas une simple citation (les codes anciens à الفصل
    citent abondamment des المادة d'autres lois dans leurs notes, cas réel :
    le Code pénal arabe 1.59.413). Sans frontière المادة réelle, الفصل
    (chiffre ou ordinal en lettres) est l'unité d'article.
    """
    mada_est_unite = any(
        RE_ARTICLE_MADA.match(ligne.strip()) for ligne in lignes if len(ligne.strip()) <= 90
    )
    strictes: list[Frontiere] = []
    collees: list[Frontiere] = []
    structures: list[Frontiere] = []
    for indice, ligne in enumerate(lignes):
        propre = ligne.strip()
        if len(propre) > 90:
            continue

        if mada_est_unite:
            mada = RE_ARTICLE_MADA.match(propre) or RE_ARTICLE_MADA_COURT.match(propre)
            if mada:
                strictes.append(
                    Frontiere(indice, "article", numeral_vers_entier(mada.group(1)), mada.group(1))
                )
                continue
        else:
            # الفصل = article : par chiffre, ou par ordinal en lettres
            # (numéro de note de bas de page collé toléré).
            fasl = RE_ARTICLE_FASL_CHIFFRE.match(propre)
            if fasl:
                strictes.append(
                    Frontiere(indice, "article", int(fasl.group(1)), fasl.group(1))
                )
                continue
            fasl_ordinal = RE_ARTICLE_FASL_ORDINAL.match(propre)
            if fasl_ordinal:
                strictes.append(
                    Frontiere(
                        indice,
                        "article",
                        numeral_vers_entier(fasl_ordinal.group(1)),
                        fasl_ordinal.group(1),
                    )
                )
                continue
            fasl_court = RE_ARTICLE_FASL_ORDINAL_COURT.match(propre)
            if fasl_court:
                collees.append(
                    Frontiere(
                        indice,
                        "article",
                        numeral_vers_entier(fasl_court.group(1)),
                        fasl_court.group(1),
                    )
                )
                continue

        structure = RE_STRUCTURE_AR.match(propre)
        if structure:
            mot, numero_brut, libelle = structure.groups()
            # الفصل structurel seulement si المادة numérote le texte ;
            # الكتاب/الباب/القسم/الجزء le sont toujours.
            if mot != "الفصل" or mada_est_unite:
                structures.append(
                    Frontiere(
                        indice,
                        "structure",
                        numeral_vers_entier(numero_brut),
                        numero_brut,
                        NIVEAUX_AR[mot],
                        libelle.strip(),
                    )
                )
                continue

    # Les frontières collées ne servent qu'en repli : un document qui isole
    # « المادة N » sur sa ligne n'a pas besoin des formes ambiguës.
    articles = strictes if strictes else collees
    return sorted(articles + structures, key=lambda frontiere: frontiere.ligne)


def _frontieres_fr(lignes: list[str]) -> list[Frontiere]:
    """Frontières d'un texte FRANÇAIS normalisé — grammaire française seule
    (« Article N » strict, « Art. N. » abrégé des dahirs de 1913, formes
    collées des lois modificatives ; hiérarchie Livre/Titre/Chapitre…)."""
    strictes: list[Frontiere] = []
    collees: list[Frontiere] = []
    structures: list[Frontiere] = []
    for indice, ligne in enumerate(lignes):
        propre = ligne.strip()
        if len(propre) > 90:
            continue

        article = RE_ARTICLE_FR.match(propre)
        if article:
            strictes.append(
                Frontiere(indice, "article", numeral_vers_entier(article.group(1)), article.group(1))
            )
            continue

        abrégué = RE_ARTICLE_FR_ABRÉGÉ.match(propre)
        if abrégué:
            collees.append(
                Frontiere(indice, "article", numeral_vers_entier(abrégué.group(1)), abrégué.group(1))
            )
            continue

        court = RE_ARTICLE_FR_LIGNE_COURTE.match(propre)
        if court:
            collees.append(
                Frontiere(indice, "article", numeral_vers_entier(court.group(1)), court.group(1))
            )
            continue

        structure = RE_STRUCTURE_FR.match(propre)
        if structure:
            mot, numero_brut, libelle = structure.groups()
            structures.append(
                Frontiere(
                    indice,
                    "structure",
                    numeral_vers_entier(numero_brut),
                    numero_brut,
                    NIVEAUX_FR[mot.lower()],
                    libelle.strip(),
                )
            )

    articles = strictes if strictes else collees
    return sorted(articles + structures, key=lambda frontiere: frontiere.ligne)


def _detecter_langue(texte: str) -> str:
    arabes = len(re.findall(r"[\u0600-\u06FF]", texte))
    latines = len(re.findall(r"[A-Za-zÀ-ÿ]", texte))
    return "ar" if arabes > latines else "fr"


def _reparer_parasites(frontieres: list[Frontiere]) -> list[Avertissement]:
    """Répare les numéros parasités par l'extraction : folio ou numéro de note
    collé au numéro d'article (« Article 7 » lu « 72 », « Article 19 » lu
    « 1911 »). Un numéro est parasité s'il commence par le numéro attendu et
    que la frontière suivante est compatible avec la suite de la séquence.
    """
    avertissements: list[Avertissement] = []
    attendu = 1
    for indice, frontiere in enumerate(frontieres):
        numero = frontiere.numero
        if numero is None:
            continue
        if numero == attendu or numero == 1:
            attendu = numero + 1 if numero != 1 else 2
            continue
        texte_numero = str(numero)
        texte_attendu = str(attendu)
        suivante = frontieres[indice + 1].numero if indice + 1 < len(frontieres) else None
        suite_propre = suivante == attendu + 1 or (
            suivante is not None and str(suivante).startswith(str(attendu + 1))
        )
        if texte_numero.startswith(texte_attendu) and len(texte_numero) > len(texte_attendu) and suite_propre:
            avertissements.append(
                Avertissement(
                    frontiere.ligne,
                    f"numéro parasité par l'extraction : {frontiere.numero_brut} réparé en "
                    f"{attendu} (folio ou note collé)",
                )
            )
            frontiere.numero = attendu
            numero = attendu
        attendu = numero + 1 if numero != 1 else 2
    return avertissements


def _extraire_serie_principale(frontieres: list[Frontiere]) -> tuple[list[Frontiere], list[Frontiere]]:
    """Sépare la série principale des séries parallèles (titres marginaux ou
    table analytique lus en travers d'une mise en page à colonnes).

    Programmation dynamique : la série principale est la plus longue
    sous-séquence qui prolonge la numérotation attendue (n, n+1, reprise à 1,
    écart ±1 toléré, articles insérés « 43-1 »/« 2 bis » comptés sans avancer
    la séquence).
    """
    total = len(frontieres)
    if total == 0:
        return [], []

    def insere(frontiere: Frontiere) -> bool:
        return bool(
            re.search(r"(?i)bis|ter|quater|quinquies|مكرر|-", frontiere.numero_brut)
        )

    def prochain_etat(numero: int) -> int:
        return numero + 1 if numero != 1 else 2

    # États « attendu » possibles — None tant que la série n'a pas démarré :
    # la série principale d'un texte peut commencer à n'importe quel numéro.
    etats: set[int | None] = {None} | {
        prochain_etat(f.numero) for f in frontieres if f.numero is not None
    }

    # Table descendante (itérative) : table[i][attendu] = nombre maximal de
    # frontières prenables parmi i..fin si le prochain numéro attendu vaut
    # « attendu ». Itérative car les codes dépassent la limite de récursion.
    # Compatibilité d'une frontière avec « attendu » : continuité exacte,
    # reprise à 1, trou ±1 (fusion/suppression), saut en avant borné
    # (SAUT_MAX : articles abrogés en bloc d'une version consolidée) — les
    # sauts non bornés sont les numéros parasités par l'extraction (folio
    # collé : 4332 pour l'article 43), traités par _reparer_parasites.
    saut_max = 100
    table: list[dict[int | None, int]] = [{e: 0 for e in etats} for _ in range(total + 1)]
    for i in range(total - 1, -1, -1):
        numero = frontieres[i].numero
        insere_i = numero is not None and insere(frontieres[i])
        for attendu in etats:
            sans = table[i + 1][attendu]
            avec = 0
            if numero is not None:
                compatible = attendu is None or numero == attendu or numero == 1 or (
                    attendu is not None
                    and attendu - 1 <= numero <= attendu + saut_max
                )
                if compatible:
                    avec = 1 + table[i + 1][prochain_etat(numero)]
                elif attendu is not None and numero <= attendu - 1 and insere_i:
                    avec = 1 + table[i + 1][attendu]
            table[i][attendu] = max(avec, sans)

    principales: list[Frontiere] = []
    ecartees: list[Frontiere] = []
    i = 0
    attendu: int | None = None
    while i < total:
        numero = frontieres[i].numero
        pris = False
        if numero is not None:
            if attendu is None or numero == attendu or numero == 1:
                if 1 + table[i + 1][prochain_etat(numero)] >= table[i + 1][attendu]:
                    principales.append(frontieres[i])
                    attendu = prochain_etat(numero)
                    pris = True
            elif attendu is not None and attendu - 1 <= numero <= attendu + saut_max:
                # Saut en avant : pris seulement s'il est strictement plus
                # avantageux que de rester dans la série en cours — en cas
                # d'égalité on préfère la série déjà entamée (la série
                # parallèle d'une table ne doit pas détourner la principale).
                if 1 + table[i + 1][prochain_etat(numero)] > table[i + 1][attendu]:
                    principales.append(frontieres[i])
                    if numero > attendu + 1:
                        frontieres[i].saut = True
                    attendu = prochain_etat(numero)
                    pris = True
            elif attendu is not None and numero <= attendu - 1 and insere(frontieres[i]):
                principales.append(frontieres[i])
                pris = True
        if not pris:
            frontieres[i].principale = False
            ecartees.append(frontieres[i])
        i += 1
    return principales, ecartees


def segmenter(texte: str) -> ResultatSegmentation:
    """Segmente un texte juridique (français ou arabe) par articles.

    Pipeline : détection des frontières → réparation des numéros parasités
    par l'extraction → extraction de la série principale (les frontières de
    séries parallèles — titres marginaux, tables analytiques — sont écartées
    et ne produisent pas de segments) → construction des segments.
    """
    resultat = ResultatSegmentation(langue=_detecter_langue(texte))
    normalise = normaliser(texte)
    lignes = normalise.splitlines()
    # Grammaires séparées : l'arabe et le français obéissent à des règles
    # distinctes (المادة/الفصل d'un côté, Article/Livre de l'autre) — on
    # n'applique jamais les deux à la fois (pas de faux positifs croisés).
    if resultat.langue == "ar":
        frontieres = _frontieres_ar(lignes)
    else:
        frontieres = _frontieres_fr(lignes)
    if not frontieres:
        return resultat

    articles_frontieres = [f for f in frontieres if f.genre == "article"]
    avertissements = _reparer_parasites(articles_frontieres)
    principales, ecartees = _extraire_serie_principale(articles_frontieres)
    for frontiere in articles_frontieres:
        if frontiere.numero is None:
            avertissements.append(
                Avertissement(frontiere.ligne, f"numéro illisible : {frontiere.numero_brut!r}")
            )
    for frontiere in ecartees:
        avertissements.append(
            Avertissement(
                frontiere.ligne,
                f"frontière écartée : article {frontiere.numero_brut} ne prolonge pas la "
                f"numérotation (série parallèle — titre marginal ou table analytique)",
            )
        )
    # Le score mesure la fiabilité de la série principale : proportion de
    # frontières prises en continuité (numéro attendu, ±1, reprise à 1,
    # inséré « 43-1 »/« 2 bis »). Les prises par re-synchronisation (saut de
    # numérotation, déjà averties) dégradent seules le score ; les frontières
    # de séries parallèles sont des déchets identifiés, listés en
    # avertissements. Si les déchets atteignent la moitié des frontières, le
    # score est plafonné : le risque d'articles réels perdus dans les séries
    # écartées devient significatif.
    if principales:
        sauts = sum(1 for f in principales if f.saut)
        score = (len(principales) - sauts) / len(principales)
        if len(ecartees) >= len(principales):
            score = min(score, 0.85)
    else:
        score = 0.0
    resultat.score_numerotation = score
    resultat.avertissements = avertissements + [
        Avertissement(
            f.ligne,
            f"saut de numérotation : article {f.numero} ({f.numero_brut}) pris par "
            f"re-synchronisation — articles abrogés, perdus ou numéro réparé",
        )
        for f in principales
        if f.saut
    ]

    premiere_article = articles_frontieres[0].ligne if articles_frontieres else len(lignes)
    resultat.preambule = "\n".join(lignes[:premiere_article]).strip()

    # Les segments ne sont construits que sur la série principale ; les
    # frontières écartées (série parallèle) ne découpent jamais le texte.
    frontieres_valides = [f for f in frontieres if f.genre != "article" or f.principale]
    contexte = {"livre": None, "chapitre": None, "section": None}
    for indice_frontiere, frontiere in enumerate(frontieres_valides):
        if frontiere.genre == "structure":
            if frontiere.niveau:
                contexte[frontiere.niveau] = (
                    f"{frontiere.numero_brut}"
                    + (f" : {frontiere.libelle}" if frontiere.libelle else "")
                )
            continue

        # Le texte de l'article s'arrête à la frontière suivante, article ou
        # structure : un titre de chapitre n'appartient jamais au texte d'un article.
        fin = (
            frontieres_valides[indice_frontiere + 1].ligne
            if indice_frontiere + 1 < len(frontieres_valides)
            else len(lignes)
        )
        texte_article = "\n".join(lignes[frontiere.ligne + 1: fin]).strip()
        resultat.articles.append(
            {
                "numero_brut": frontiere.numero_brut,
                "numero": frontiere.numero,
                "contexte": dict(contexte),
                "texte": texte_article,
                "longueur": len(texte_article),
            }
        )
    return resultat


def classer(resultat: ResultatSegmentation, caracteres: int, pages: int) -> str:
    """Classification qualité : A fiable, B exploitable mais marqué, C quarantaine."""
    densite = caracteres // max(pages, 1)
    if densite < 200 or caracteres < 800:
        return "C"
    if not resultat.articles:
        return "C"
    if resultat.coherent:
        return "A"
    return "B"
