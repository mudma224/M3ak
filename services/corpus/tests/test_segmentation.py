"""Tests de la grammaire de segmentation arabe/français (jalon B)."""

from __future__ import annotations

from app.segmentation import classer, normaliser, segmenter

# ---------------------------------------------------------------- français ---

TEXTE_FR = """ROYAUME DU MAROC
Loi n° 49-16 relative aux baux
Titre PREMIER : Dispositions générales
Article premier
La présente loi régit les baux d'immeubles.
Article 2
Les dispositions du présent titre s'appliquent.
Chapitre II : Du loyer
Article 3
Le loyer est fixé d'un commun accord.
Article 4
Le bail prend fin.
Article 6
Dispositions finales.
"""


def test_preambule_dedie():
    resultat = segmenter(TEXTE_FR)
    assert resultat.preambule.startswith("ROYAUME DU MAROC")
    assert "Titre PREMIER" in resultat.preambule


def test_articles_detectes_dans_l_ordre():
    resultat = segmenter(TEXTE_FR)
    assert [a["numero"] for a in resultat.articles] == [1, 2, 3, 4, 6]


def test_contexte_hierarchique_propage():
    resultat = segmenter(TEXTE_FR)
    troisieme = resultat.articles[2]
    assert troisieme["contexte"]["chapitre"].startswith("II")
    assert "Du loyer" in troisieme["contexte"]["chapitre"]


def test_titre_structure_hors_texte_article():
    """Un titre de chapitre n'appartient jamais au texte de l'article."""
    resultat = segmenter(TEXTE_FR)
    assert "Chapitre II" not in resultat.articles[1]["texte"]
    assert resultat.articles[1]["texte"].endswith("s'appliquent.")


def test_trou_faible_tolere_trou_fort_averti():
    """Écart ±1 (article supprimé d'une version consolidée) : gardé, sans
    avertissement. Saut borné plus grand : gardé par re-synchronisation,
    mais signalé."""
    resultat = segmenter(TEXTE_FR)  # numéros 1, 2, 3, 4, 6
    assert [a["numero"] for a in resultat.articles] == [1, 2, 3, 4, 6]
    assert resultat.score_numerotation == 1.0
    texte = "Article 1\nUn.\nArticle 2\nDeux.\nArticle 7\nSept.\n"
    autre = segmenter(texte)
    assert [a["numero"] for a in autre.articles] == [1, 2, 7]
    assert any("saut" in a.message and "7" in a.message for a in autre.avertissements)


def test_article_insere_bis_non_signale():
    texte = (
        "Article 1\nPremier.\nArticle 2\nSecond.\nArticle 3\nTroisième.\n"
        "Article 2 bis\nInséré par une loi modificative.\nArticle 4\nQuatrième.\n"
    )
    resultat = segmenter(texte)
    numeros = [a["numero_brut"] for a in resultat.articles]
    assert numeros == ["1", "2", "3", "2 bis", "4"]
    assert not any("2 bis" in a.message for a in resultat.avertissements)
    assert resultat.score_numerotation == 1.0


def test_numeraux_composes():
    texte = "Article 2-12\nVersion consolidée.\nArticle 2-13\nSuite.\n"
    resultat = segmenter(texte)
    assert [a["numero"] for a in resultat.articles] == [2, 2]


def test_numero_parasite_repare():
    """« Article 7 » lu « 72 » (folio collé) : réparé si la suite est propre."""
    texte = (
        "Article 5\nCinq.\nArticle 6\nSix.\nArticle 72\nSept malgré le folio.\n"
        "Article 8\nHuit.\n"
    )
    resultat = segmenter(texte)
    assert [a["numero"] for a in resultat.articles] == [5, 6, 7, 8]
    assert any("parasité" in a.message for a in resultat.avertissements)


def test_serie_parallele_ecartee():
    """Titres marginaux d'une mise en page à colonnes : numéros d'une autre
    série intercalés — écartés, sans créer de segments."""
    texte = (
        "Article 1\nPremier.\nArticle 34: \n"
        "Article 2\nDeuxième.\nArticle 36: \nArticle 3\nTroisième.\n"
    )
    resultat = segmenter(texte)
    assert [a["numero"] for a in resultat.articles] == [1, 2, 3]
    ecartees = [a.message for a in resultat.avertissements if "écartée" in a.message]
    assert len(ecartees) == 2


def test_insere_compose_dans_la_serie():
    """« 43-1 » et suivants : insérés dans la série du texte cible."""
    texte = (
        "Article 42\nQuarante-deux.\nArticle 43\nQuarante-trois.\n"
        "Article 43-1\nInséré un.\nArticle 43-2\nInséré deux.\n"
        "Article 44\nQuarante-quatre.\n"
    )
    resultat = segmenter(texte)
    bruts = [a["numero_brut"] for a in resultat.articles]
    assert bruts == ["42", "43", "43-1", "43-2", "44"]
    assert resultat.score_numerotation == 1.0


def test_reprise_numerotation_apres_partie():
    texte = "Article 1\nUn.\nArticle 2\nDeux.\nPartie II\nArticle 1\nReprise.\n"
    resultat = segmenter(texte)
    assert [a["numero"] for a in resultat.articles] == [1, 2, 1]


def test_faux_positifs_exclus():
    texte = (
        "Article 1\nIl est créé un cadre.\n"
        "Conformément à l'article 2 ci-après, le délai court.\n"
    )
    resultat = segmenter(texte)
    assert len(resultat.articles) == 1


# ------------------------------------------------------------------ arabe ---

TEXTE_AR = """المملكة المغربية
القانون رقم 49.16
الباب الأول: أحكام عامة
المادة ١
يهدف هذا القانون إلى تحديد القواعد
المادة ٢
تطبق أحكام هذا القانون على
القسم الأول: الأحكام المالية
المادة ٤
تحدد هذه المادة القواعد
"""


def test_articles_arabes_chiffres_orientaux():
    resultat = segmenter(TEXTE_AR)
    assert resultat.langue == "ar"
    assert [a["numero"] for a in resultat.articles] == [1, 2, 4]


def test_contexte_hierarchique_arabe():
    resultat = segmenter(TEXTE_AR)
    assert resultat.articles[2]["contexte"]["section"].startswith("الأول")
    assert resultat.articles[0]["contexte"]["chapitre"].startswith("الأول")


def test_fasl_article_quand_pas_de_madda():
    """Dahirs anciens : « الفصل » est l'unité d'article (pas de « المادة »)."""
    texte = "ظهير شريف\nالفصل ١\nينفذ هذا الظهير\nالفصل ٢\nيدخل حيز التنفيذ\n"
    resultat = segmenter(texte)
    assert [a["numero"] for a in resultat.articles] == [1, 2]


def test_fasl_structure_quand_madda_presente():
    """Texte moderne : « الفصل » numéroté est structurel, pas un article."""
    texte = "المادة ١\nأول نص.\nالفصل الثاني: الأحكام\nالمادة ٢\nثاني نص.\n"
    resultat = segmenter(texte)
    assert [a["numero"] for a in resultat.articles] == [1, 2]
    assert resultat.articles[1]["contexte"]["chapitre"].startswith("الثاني")


# ---------------------- corpus réel ( raisonné septembre 2026) ----------------------

DAHIR_ANCIEN = """بسم الله الرحمان الرحيم
ظهير شريف رقم 1.60.305
الفصل الاول 2
إن تنفيذ جميع الأحكام أو القرارات النهائية الصادرة بأداء مبلغ مالي يمكن أن يتابع عن طريق الإكراه البدني. غير أنه لا يجوز إيداع شخص بالسجن على أساس عدم قدرته على الوفاء التزام تعاقدي فقط.
الفصل الثاني 3
يطبق الإكراه البدني وفق القواعد والكيفيات المحددة في المواد من 633 إلى 647.
الفصل الثالث
يلغى المقطع الخامس من الفقرة الثانية من الفصل 676.
وحرر بالرباط في 4 رمضان عام 1380.
"""


def test_dahir_ancien_fasl_ordinal_en_lettres():
    """Dahirs anciens : الفصل + ordinal en LETTRES (avec ou sans hamza),
    numéro de note de bas de page collé toléré — reproduit le dahir
    1.60.305 réel (corpus Adala, septembre 2026)."""
    resultat = segmenter(DAHIR_ANCIEN)
    assert resultat.langue == "ar"
    assert [a["numero_brut"] for a in resultat.articles] == ["الاول", "الثاني", "الثالث"]
    assert [a["numero"] for a in resultat.articles] == [1, 2, 3]
    assert resultat.score_numerotation == 1.0
    assert resultat.articles[0]["texte"].startswith("إن تنفيذ")


def test_fasl_ordinal_avec_hamza_aussi_reconnu():
    texte = "الفصل الأول\nنص أول.\nالفصل الثاني\nنص ثانٍ.\n"
    resultat = segmenter(texte)
    assert [a["numero"] for a in resultat.articles] == [1, 2]


def test_circulaire_administrative_sans_articles():
    """Circulaires (منشور/دورية) : prose sans numérotation — zéro article,
    c'est la quarantaine honnête, pas un échec de la grammaire."""
    texte = (
        "الرباط في 1 سبتمبر 2006\nمنشور عدد: 38 س2\nمن وزير العدل\nإلى السادة:\n"
        "الوكلاء العامين للملك لدى محاكم الاستئناف\n"
        "وإحالة على الظهير الشريف رقم 1.06.23 وخاصة المادة 12 منه.\n"
        + "وبعد، فمن المعلوم أن الفصل 37 من قانون المسطرة المدنية أعطى للأطراف إمكانية الاختيار. " * 6
    )
    resultat = segmenter(texte)
    assert resultat.langue == "ar"
    assert resultat.articles == []
    # المادة 12 و الفصل 37 cités en RÉFÉRENCE dans la prose :
    # pas en début de ligne, ils ne créent jamais d'article.
    assert resultat.score_numerotation == 0.0


def test_code_penal_arabe_citations_mada_ne_bloquent_pas_les_fasl():
    """Cas réel 1.59.413 (Code pénal arabe) : le texte NUMÉROTE par الفصل
    (ordinaux isolés) mais CITE des المادة d'autres lois dans ses notes —
    « 22- المادة 756 من القانون رقم22.01… ». Une citation n'est pas une
    frontière : الفصل reste l'unité d'articles."""
    texte = (
        "ظهير شريف رقم 1.59.413 بمثابة قانون الجنائي\n"
        "22- المادة 756 من القانون رقم 22.01 المتعلق بالمسطرة الجنائية\n"
        "27- المادة 733 من القانون رقم 15.95 المتعلق بمدونة التجارة\n"
        "الفصل الأول\n" + ("يكون جريمة كل فعل يعاقب عليه القانون. " * 20)
        + "\nالفصل الثاني\n" + ("تطبق العقوبات وفق القواعد الآتية. " * 20)
        + "\nالفصل الثالث\n" + ("يلغى كل نص مخالف لظهيرنا الشريف هذا. " * 20)
    )
    resultat = segmenter(texte)
    assert resultat.langue == "ar"
    numeros = [a["numero"] for a in resultat.articles]
    assert numeros == [1, 2, 3]
    # les citations « المادة 756 » ne créent pas d'articles
    assert all(a["numero_brut"] in ("الأول", "الاول", "الثاني", "الثالث") for a in resultat.articles)


def test_grammaires_separees_pas_de_faux_positifs_croises():
    """Un texte français contenant des lignes arabes (et réciproquement) :
    seule la grammaire de la langue du texte s'applique."""
    texte_francais_avec_arabe = (
        "Article 1\nPremier.\nالمادة ٥\nنص عربي داخل un texte français majoritairement latin.\nArticle 2\nSecond.\n"
    )
    resultat = segmenter(texte_francais_avec_arabe)
    assert resultat.langue == "fr"
    assert [a["numero"] for a in resultat.articles] == [1, 2]


def test_madda_wahida():
    texte = "المادة الوحيدة\nهذا القانون يعدل المادة 4.\n"
    resultat = segmenter(texte)
    assert len(resultat.articles) == 1
    assert resultat.articles[0]["numero"] == 1


# ------------------------------------------------------------- qualités ---

def test_normalisation_chiffres_et_espaces():
    assert normaliser("المادة ١٢\u0640٣") == "المادة 123"
    assert normaliser("a\u00a0b") == "a b"


def test_classification():
    dense = "Article 1\n" + ("Texte. " * 200) + "\nArticle 2\nSuite.\n"
    resultat = segmenter(dense)
    assert classer(resultat, caracteres=5000, pages=2) == "A"
    assert classer(resultat, caracteres=100, pages=10) == "C"
    assert classer(segmenter("aucune frontière ici"), caracteres=5000, pages=2) == "C"
