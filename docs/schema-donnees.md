# Schéma de données M3ak — version 1.0

> Source de vérité : le paquet `contracts/` (modèles Pydantic). Les exports
> JSON Schema correspondants vivent dans `contracts/schemas/`. Ce document
> les explique ; en cas d'écart, le code fait foi.

## 1. Document normalisé

Tout document du corpus porte des **métadonnées communes**, complétées par
des champs spécifiques à sa collection.

### Métadonnées communes

| Champ | Type | Description |
|---|---|---|
| `identifiant.numero_officiel` | str | Numéro officiel (loi, dahir…) ou identifiant de la source (fiche, décision) |
| `identifiant.langue` | `ar` \| `fr` | Langue du document — décidée par le pipeline à l'ingestion (ADR-0005 : titre arabe prioritaire, détection du contenu gardée par densité, annonce de la source en secours) |
| `identifiant.version` | str | `originale` ou `consolidée-AAAA-MM` |
| `identifiant.cle()` | — | Clé canonique `numero\|langue\|version` — la clé de déduplication |
| `collection` | enum | `textes_normatifs`, `procedures`, `jurisprudence`, `donnees_complementaires` |
| `source` | str | `adala`, `sgg`, `idarati`, `jurisprudence_ma`, `data_gov` |
| `titre`, `langue` | str | Intitulé complet ; langue du contenu |
| `url_source`, `chemin_local` | str | Lien d'origine ; emplacement dans la couche brute. `url_source` n'est pas renseigné par la collecte en V1 — la couche affichage le reconstruira après la V1 à partir des identifiants sources |
| `dates.date_publication` | date? | Publication/adoption, si connue |
| `dates.date_recuperation` | datetime | **Date de consultation** — obligatoire, affichée à l'utilisateur |
| `empreinte` | {algorithme, valeur} | sha256 du contenu — détection des modifications silencieuses |
| `statut_juridique` | enum | **Réservé** ; `inconnu` en V1 (jamais inventé) |

Note : il n'existe volontairement **pas** de champ « statut d'autorisation »
(décision du porteur) ; les autorisations de réutilisation sont gérées dans
le dossier de conformité du projet.

### Champs spécifiques (extraits)

- **Texte normatif** : `type_texte` (dahir, loi, décret, arrêté…),
  `numero_loi`, `theme`, `reference_bo` (numéro + date du Bulletin
  officiel), `version_consolidee` (booléen), `mots_cles`.
- **Procédure** : `prix`, `delai_moyen_jours`, `administration_charge`,
  `administrations_receptrices`, `formulaires`, `url_procedure`.
- **Décision** : `juridiction`, `ville`, `numero_decision`,
  `numero_dossier`, `date_decision`, `themes`, `mots_cles`,
  `source_publication`, `a_resume_francais`, `texte_integral_arabe`.
- **Donnée complémentaire** : `organisme_producteur`, `licence`,
  `date_mise_a_jour`.

## 2. Segment d'article (unité d'indexation)

Le découpage suit la structure réelle des textes (jamais un découpage à
taille fixe) :

| Champ | Rôle |
|---|---|
| `segment_id` | Identifiant stable — **clé d'idempotence** de l'indexation |
| `document_id` | Clé canonique du document parent |
| `articles` | Liste des numéros d'articles contenus (regroupement d'articles courts adjacents uniquement) |
| `alineas` | Plage d'alinéas pour un article long sous-découpé (ex. `1-4`) |
| `texte` | Contenu du segment |
| `langue` | Langue du segment |
| `contexte` | Héritage : `texte_identifiant`, `texte_titre`, `livre`, `chapitre`, `section` |
| `variantes_linguistiques` | Clés canoniques des variantes AR/FR du même texte — support du repli interlingue |

Principe « **chercher fin, restituer entier** » : la recherche s'effectue
sur les segments ; la citation restitue toujours l'article complet.

## 3. Fiche de procédure (unité d'indexation des procédures)

Les fiches (Idarati) sont des documents structurés sans articles : leur
unité d'indexation est dédiée (`FicheAIndexer`), gérée à part des
segments d'articles — chaque famille évolue sans toucher à l'autre.

| Champ | Rôle |
|---|---|
| `fiche_id` | Identifiant stable — **clé d'idempotence** de l'indexation |
| `document_id` | Clé canonique du document parent |
| `titre` | Intitulé de la procédure |
| `langue` | Langue de la fiche |
| `texte` | Contenu textuel concaténé (titre, description, délai, frais, administrations) |
| `thematique_id` | Identifiant de la thématique source, si connue |
| `administration` | Administration en charge de la procédure |

## 4. Décision de justice (unité d'indexation de la jurisprudence)

Une décision (Jurisprudence.ma) est bilingue : résumé français, texte
intégral arabe. Son unité d'indexation est dédiée (`DecisionAIndexer`),
gérée à part des segments et des fiches — les deux langues sont portées
par des champs distincts, débarrassés du balisage HTML, pour que la
recherche interroge chaque langue séparément (repli interlingue).

| Champ | Rôle |
|---|---|
| `decision_id` | Identifiant stable — **clé d'idempotence** de l'indexation |
| `document_id` | Clé canonique du document parent |
| `titre`, `langue` | Intitulé ; langue du texte intégral (ar), sinon le résumé (fr) |
| `thematiques` | Slugs des thématiques de la source |
| `juridiction`, `ville`, `type_decision` | Métadonnées de la décision |
| `numero_decision`, `numero_dossier`, `date_decision` | Références de la décision |
| `resume_francais`, `resume_arabe` | Résumés, sans balisage |
| `texte_integral_arabe`, `texte_integral_francais` | Textes intégraux, sans balisage |

## 5. Messages inter-services (RabbitMQ)

Toute charge utile voyage dans une **enveloppe versionnée** :
`schema_version`, `message_id` (UUID), `correlation_id`, `emis_le`,
`emis_par`, `type_message`, `charge`.

Files de travail : `segments.to_index`, `fiches.to_index` et
`decisions.to_index` (corpus -> recherche), `ocr.jobs` (documents,
phase 4). Chaque file possède sa dead-letter queue `.dlq`.

## 6. Exemple complet

```json
{
  "identifiant": {
    "numero_officiel": "loi-49-16",
    "langue": "fr",
    "version": "originale"
  },
  "collection": "textes_normatifs",
  "source": "adala",
  "titre": "Loi n° 49-16 relative aux baux d'immeubles ou de locaux loués à usage commercial",
  "langue": "fr",
  "url_source": null,
  "chemin_local": "corpus/adala/raw/2026/loi-49-16_fr.pdf",
  "dates": {
    "date_publication": "2017-06-16",
    "date_recuperation": "2026-09-17T00:00:00Z"
  },
  "empreinte": { "algorithme": "sha256", "valeur": "…" },
  "statut_juridique": "inconnu",
  "specifics": {
    "type_texte": "loi",
    "numero_loi": "49-16",
    "theme": "Matière civile",
    "version_consolidee": false,
    "mots_cles": ["baux", "commercial"]
  }
}
```

## 7. Évolutions prévues

- `statut_juridique` : alimentation par rapprochement Bulletin officiel
  (champ déjà réservé — aucune refonte) ;
- `url_source` : renseignement par la couche affichage après la V1,
  à partir des identifiants sources.
