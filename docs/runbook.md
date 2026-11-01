# Runbook opérationnel M3ak (développement local)

## 1. Démarrage

```bash
cd infra
cp .env.example .env        # la première fois ; adapter les mots de passe
docker compose build
docker compose up -d
docker compose ps           # attendre : tous les conteneurs "healthy"
```

Vérification rapide :

```bash
curl -H "Host: conversation.m3ak.localhost" http://127.0.0.1/health
bash tools/verifier_sante.sh        # depuis la racine du dépôt
```

Depuis la racine du dépôt, des wrappers équivalents existent (voir §11) :
`npm run docker:build && npm run docker:up`, puis `npm run verifier`.

## 2. Interfaces locales

| Interface | URL | Identifiants |
|---|---|---|
| Tableau de bord Traefik | http://localhost:8080 | — (développement) |
| RabbitMQ (files, DLQ) | http://localhost:15672 | `RABBITMQ_USER` / `RABBITMQ_PASSWORD` du `.env` |
| Console MinIO | http://localhost:9001 | `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` |

## 3. Arrêt et nettoyage

```bash
cd infra
docker compose down            # arrêt, données conservées (volumes)
docker compose down -v         # ⚠️ supprime aussi les bases et les files
```

## 4. Diagnostic d'un service

```bash
docker compose ps corpus                     # état + healthcheck
docker compose logs -f corpus                # journaux JSON (correlation_id inclus)
docker compose exec corpus python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8001/health').read())"
```

Routes utiles : `/health` (ouvert), `/ready` (jeton requis),
`/internal/echo` (jeton requis, renvoie le correlation ID).

## 5. Files RabbitMQ et dead-letter queues

Points d'attention :

- `segments.to_index` — consommée par `recherche` ; profondeur durablement
  > 0 = consommateur arrêté ou en erreur ;
- `segments.to_index.dlq` — messages rejetés (invalide ou échec de
  traitement) : **inspecter avant tout rejeu** ;
- `ocr.jobs` / `ocr.jobs.dlq` — à partir de la phase 4.

Procédure d'inspection d'une DLQ (via l'UI) : ouvrir la file `.dlq`,
récupérer le message, vérifier le `correlation_id` dans les journaux du
service consommateur, corriger la cause, puis rejeter le message
(Get/Requeue) ou le publier à nouveau après correction du format.

## 6. Démo de résilience (phase 0)

```bash
# depuis la racine, avec les identifiants de infra/.env
python tools/publier_message_test.py \
  --url "amqp://UTILISATEUR:MOTDEPASSE@127.0.0.1:5672/%2F" \
  --management "http://UTILISATEUR:MOTDEPASSE@127.0.0.1:15672"
```

Attendu : message valide consommé (journaux de `recherche`), message
invalide dans `segments.to_index.dlq`.

## 7. Bases de données

Chaque base appartient à un service ; l'accès de diagnostic se fait par
`docker compose exec` dans le conteneur du service correspondant :

```bash
docker compose exec corpus-db psql -U u_corpus -d db_corpus -c '\dt'
docker compose exec recherche-db psql -U u_recherche -d db_recherche -c '\dx'
```

Aucun autre service ne doit détenir ces identifiants — c'est le principe
database-per-service (ADR-0001).

## 8. Sauvegardes

En développement : les volumes Docker (`docker compose down` sans `-v`)
suffisent. La stratégie de sauvegarde formelle (fréquence, rétention,
test de restauration) est établie en phase 5 avec l'industrialisation.

## 9. Après modification du code

Le porteur rebuild et relance : `docker compose up -d --build corpus`
(concerné uniquement), puis `bash tools/verifier_sante.sh`.

## 10. Dépannage connu

### Traefik : "Error response from daemon: " (message vide)

Symptôme : les journaux de Traefik répètent `Provider error ... Error
response from daemon: ` avec un message vide ; aucun routeur n'apparaît
dans le tableau de bord. Le conteneur paraît pourtant « healthy » (le
healthcheck teste le ping interne, pas le fournisseur Docker).

Cause : sur Docker Desktop Windows/WSL2, le socket Docker est proxié et
peut renvoyer des réponses tronquées aux clients conteneurisés.

Contournement (déjà câblé dans ce dépôt) :

1. Docker Desktop → Settings → Advanced → cocher **Expose daemon on TCP
   without TLS** → Apply & Restart ;
2. vérifier que `infra/.env` contient
   `TRAEFIK_DOCKER_ENDPOINT=tcp://host.docker.internal:2375` ;
3. `docker compose up -d traefik` (recréation pour prendre en compte la
   variable).

⚠ Sécurité : le port 2375 est sans authentification — usage strictement
local et en développement ; à désactiver hors développement. Alternatives
si ce contournement ne convient pas : piloter `docker compose` depuis
WSL 2 directement, ou rebasculer Docker Desktop sur le backend Hyper-V.

## 11. Orchestration des tâches du monorepo (npm + turbo, ADR-0004)

Deux familles de commandes, toutes lancées **depuis la racine du dépôt**.

### Tâches par package — turbo (cache + parallélisme)

```bash
npm run qualite                        # turbo run lint test schemas (7 packages)
npx turbo run lint test schemas       # équivalent direct
npx turbo run lint --filter=corpus    # un seul service
```

- `lint` : `ruff check .` par package ; `test` : `pytest` par package ;
  `schemas` : export des JSON Schema depuis `m3ak-contracts`.
- Le cache local évite de rejouer ce qui n'a pas changé : un second passage
  sans modification s'exécute en moins d'une seconde (« FULL TURBO »).
- Prérequis : l'environnement conda `m3ak` sur le `PATH` (les scripts de
  test appellent `python -m pytest`).

### Commandes globales — scripts npm racine

```bash
npm run docker:build | up | down | ps | logs
npm run verifier                       # 18 contrôles de santé (§1)
npm run demo:rabbitmq                  # démo files + DLQ (§6)
```

Turbo n'exécute pas les scripts du package racine (il n'adresse que les
espaces de travail `contracts/` et `services/*`) : ces commandes globales
restent donc de simples wrappers npm autour de `docker compose` et des
scripts `tools/`. Détail et justification : ADR-0004.

## 12. Corpus pilote : publier les segments (jalon B)

Chaîne de bout en bout : segments du pilote (`pilot/segments/`) →
`SegmentArticle` du contrat (`services/corpus/app/normalisation.py`) →
file `segments.to_index` → persistance idempotente du service recherche
(table `segments`, clé `segment_id`).

Prérequis : conteneurs `rabbitmq` et `recherche` lancés — le service
recherche crée la table au démarrage.

```bash
python tools/publier_segments_pilote.py --dry-run   # voir sans publier
python tools/publier_segments_pilote.py             # publier (79 messages, ~6 400 segments)

# volumétrie de l'index (jeton interne depuis infra/.env)
JETON=$(grep '^INTERNAL_TOKEN=' infra/.env | cut -d= -f2)
curl -H "Host: recherche.m3ak.localhost" -H "X-Internal-Token: $JETON" \
     http://127.0.0.1/internal/segments/compte
```

Attendu : `{"nb_segments": 6394, "nb_documents": 24}`.

Rejouer la publication : les mêmes messages sont consommés mais aucun
nouveau segment n'est créé (« deja presents » dans les journaux du service
recherche, compte inchangé) — c'est l'idempotence par `segment_id` qui
absorbe les rejouements de la livraison at-least-once. Les messages
invalides partent en DLQ (§6).

## 13. Ingestion de documents par le service corpus (phase 1)

Le service corpus ingère un document de bout en bout : couche brute
(MinIO, bucket `m3ak-corpus-brut`) → extraction → segmentation → scoring
qualité A/B/C (C = quarantaine, jamais indexée) → couche normalisée
(table `documents`) → publication des segments sur `segments.to_index`.

```bash
JETON=$(grep '^INTERNAL_TOKEN=' infra/.env | cut -d= -f2)
curl -s -H "Host: corpus.m3ak.localhost" -H "X-Internal-Token: $JETON" \
     -F "fichier=@pilot/raw/adala/A1-Constitution-de-2011/<pdf>.pdf" \
     -F "reference=1-11-91" -F "titre=Constitution de 2011" \
     http://127.0.0.1/internal/documents/ingester

# volumétrie et qualité du corpus
curl -s -H "Host: corpus.m3ak.localhost" -H "X-Internal-Token: $JETON" \
     http://127.0.0.1/internal/documents/stats
```

Statuts retournés : `insere`, `insere_quarantaine` (classe C),
`deja_present` (même contenu — idempotence, y compris inter-sources),
`version_differente` (autre empreinte pour une clé connue : refusé, la
gestion multi-versions appartient à la veille).

## 14. Connecteur Adala — collecte par thème (phase 1)

Le connecteur vit dans le service corpus (`app/connecteurs/adala.py`) :
parcours d'un thème via la recherche Adala (métadonnées `__NEXT_DATA__`),
choix d'un document par référence (version consolidée à privilégier,
sinon la plus récente), téléchargement des PDF (~1 requête/1,2 s), puis
ingestion par le pipeline — l'idempotence par empreinte fait qu'une
re-collecte ne retraite jamais un document déjà ingéré.

```bash
JETON=$(grep '^INTERNAL_TOKEN=' infra/.env | cut -d= -f2)
CORPUS='Host: corpus.m3ak.localhost'

# thèmes racine avec volumétrie
curl -s -H "$CORPUS" -H "X-Internal-Token: $JETON" \
     http://127.0.0.1/internal/connecteurs/adala/themes

# aperçu sans rien collecter (dry-run), puis collecte réelle en tâche de fond
curl -s -X POST -H "$CORPUS" -H "X-Internal-Token: $JETON" \
     -H "Content-Type: application/json" \
     -d '{"theme": 7, "limite": 50, "dry_run": true}' \
     http://127.0.0.1/internal/connecteurs/adala/executer
curl -s -X POST ... -d '{"theme": 7, "limite": 273}' ...

# suivi (journal borné + compteur de statuts) ; 409 si une collecte tourne déjà
curl -s -H "$CORPUS" -H "X-Internal-Token: $JETON" \
     http://127.0.0.1/internal/connecteurs/adala/statut
```

Chemin simple : `python tools/piloter_collecte.py --theme <id>` lance la
collecte et la suit jusqu'à son terme (statuts + journal borné).

**Collectes Adala gelées depuis le 2026-09-19** (décision du porteur,
esprit prototypage : 7 thèmes majeurs couverts, 2 881 documents). Toute
reprise se fait par thème entier — l'idempotence par empreinte fait
qu'un re-parcours ne retraite jamais un document déjà ingéré ; les
manifestes en base (`SELECT id, methode, volume FROM manifestes`)
tracent chaque passage.

Note : Adala mélange documents arabes et français dans un même thème —
la langue de l'identifiant stable (`numéro|langue|version`) est décidée
par le pipeline (ADR-0005 : titre arabe prioritaire, détection gardée
par densité, annonce de la source en secours, « inconnue » sinon —
jamais de valeur par défaut).

## 15. Vue de classement des PDF (revue manuelle, amélioration grammaire)

Où vivent les fichiers :
- **couche brute** (source de vérité, immuable) : bucket MinIO
  `m3ak-corpus-brut`, clé `<source>/<empreinte>.pdf` — jamais déplacée ni
  renommée (c'est la clé de l'idempotence). Le pilote du jalon B garde
  aussi ses PDF d'origine dans `pilot/raw/`.
- **vue de classement** (régénérable, pour vérifier à la main) :

```bash
# UNE seule commande : vue MinIO (classe/<langue>/<classe>/) + copie locale
# (pilot/classement/<langue>/<A-fiable|B-marque|C-quarantaine>/<référence>-<empreinte12>.pdf)
# — les deux vues sont purgées puis régénérées, les PDF sont posés
# automatiquement, rien à faire à la main.
python tools/classement.py

# pour cibler un seul sous-ensemble en local :
python tools/exporter_classement.py --langue ar --classe C
```

Reprise après perte de fiches : la couche brute étant la source de
vérité, `tools/restaurer_ar.py` reconstruit les fiches à partir de la
liaison référence-empreinte portée par les noms de fichiers de la vue
locale ; tout document non couvert revient par re-collecte idempotente
du thème concerné.

Boucle d'amélioration de la grammaire : ouvrir les PDF de
`C-quarantaine` (et `B-marque`), repérer les formes de frontières non
détectées → corriger `app/segmentation.py` + tests → rebuild corpus →
`python tools/resegmenter_langue.py ar|fr` (rejoue le pipeline sur la
couche brute, sans re-télécharger ; aucune langue n'y est ré-imposée,
le pipeline redécide seul) → réexporter le classement.

Avant d'ouvrir les PDF, `python tools/analyser_quarantaine.py --langue
fr --classe C --nombre 10` montre ce que l'extraction produit vraiment
par document (pages, lettres ar/lat, densité/page, lignes les plus
fréquentes) — c'est l'outil qui a servi à fixer les seuils de langue :
la détection du contenu exige ≥ 50 lettres ET ≥ 100 caractères/page
(mesuré : vrai texte ≥ 243 car./page, junk de scan ≤ 60), sinon la
langue vient de l'annonce de la source, puis du titre, sinon
« inconnue » (quarantaine).

## 16. Connecteur Idarati — fiches de procédures (phase 1)

Le connecteur vit dans le service corpus (`app/connecteurs/idarati.py`)
et interroge le portail citoyen (`procedures.idarati.ma`, AGENTS.md
§3.1) : menu des catégories aplati (catégorie → public → thématique),
parcours paginé d'une thématique via `thematicId`, fiche complète par
identifiant. Une fiche est un document structuré (titre, description,
délai, frais, administrations, formulaires) : pas d'extraction PDF ni de
segmentation par articles — le JSON est conservé tel quel en couche
brute (`idarati/<empreinte>.json`) et la fiche est enregistrée en classe
A (collection `procedures`). L'idempotence par empreinte fait qu'une
re-collecte ne retraite jamais une fiche déjà ingérée.

```bash
JETON=$(grep '^INTERNAL_TOKEN=' infra/.env | cut -d= -f2)
CORPUS='Host: corpus.m3ak.localhost'

# thématiques du menu (id, titre, public)
curl -s -H "$CORPUS" -H "X-Internal-Token: $JETON" \
     http://127.0.0.1/internal/connecteurs/idarati/thematiques

# collecte d'une thématique (dry-run possible), puis suivi
curl -s -X POST -H "$CORPUS" -H "X-Internal-Token: $JETON" \
     -H "Content-Type: application/json" \
     -d '{"thematiques": ["<id>"], "limite": 1000, "dry_run": false}' \
     http://127.0.0.1/internal/connecteurs/idarati/executer
curl -s -H "$CORPUS" -H "X-Internal-Token: $JETON" \
     http://127.0.0.1/internal/connecteurs/idarati/statut

# ou, en une commande (ids figés dans pilot/idarati_pilote.json) :
python tools/piloter_collecte.py --connecteur idarati --thematiques <id1,id2,…>
```

Périmètre pilote : les thématiques validées par le porteur sont figées
dans `pilot/idarati_pilote.json` (5 domaines couvrant 9 identifiants de
thématique, ~1 000 fiches). La collecte est polie (~1 requête/1,2 s,
agent identifiable) et chaque session laisse un manifeste en base.

Chaque fiche ingérée est publiée sur la file dédiée `fiches.to_index`
(contrat `FicheAIndexer`) ; le service recherche la persiste de façon
idempotente dans sa table `fiches` — gestion séparée des segments
d'articles. Volumétrie : `GET /internal/fiches/compte` côté recherche.
Les liens vers les fiches originales sont ajoutés par la couche
affichage (après la V1) ; les identifiants sources permettent de les
reconstruire.

## 17. Connecteur Jurisprudence.ma — décisions de justice (phase 1)

Le connecteur vit dans le service corpus (`app/connecteurs/jurisprudence.py`)
et interroge l'API WordPress du site (`jurisprudence.ma`, AGENTS.md
§3.1) : parcours allégé du référentiel (`_fields=id,class_list`, ~250
requêtes — la source clôt la pagination par une erreur 400), sélection
des décisions par slugs de thématiques (les taxonomies ne sont pas
exposées en REST, les slugs sont portés par la `class_list`), puis
décision complète par identifiant.

Une décision est un document bilingue (résumé français, texte intégral
arabe) : JSON conservé tel quel en couche brute
(``jurisprudence/<empreinte>.json``), enregistrement en classe A
(collection `jurisprudence`, langue = celle du texte intégral, sinon le
résumé), puis publication sur la file dédiée `decisions.to_index`
(contrat `DecisionAIndexer`) avec les deux langues portées par des
champs distincts, débarrassées de leur balisage HTML.

```bash
python tools/piloter_collecte.py --connecteur jurisprudence \
    --thematiques <slug1,slug2,…> \
    --limites '{"<slug>": 1000}'      # borne par thématique, intégral si absent

# piloter_collecte suit la collecte jusqu'au terme ; l'état est aussi
# visible sur /internal/connecteurs/jurisprudence/statut
```

Périmètre pilote validé : 5 thématiques (`procedure-civile` bornée à
1 000, `commercial` à 800, `baux` à 600, `civil` et `administratif` en
intégral — ≈ 4 400 décisions, ~1 Go). Le référentiel des 316 thématiques
et leur volumétrie vivent dans `pilot/jurisprudence_volumetrie.json` ;
la sélection prend les décisions les plus récentes dans chaque borne.
Le champ `modified` de la source porte les mises à jour — une décision
modifiée est refusée à la re-collecte : la veille gèrera l'évolution.
La volumétrie indexée se lit sur `/internal/decisions/compte` côté
recherche.

## 18. Tableau de bord de volumétrie et de qualité (phase 1)

Le service corpus expose une page HTML en lecture seule, régénérée à
chaque chargement. Depuis un navigateur :

```
http://corpus.m3ak.localhost/tableau-de-bord
```

(les navigateurs résolvent `*.localhost` vers 127.0.0.1 ; Traefik route
par l'en-tête Host). En ligne de commande, même adresse passée à curl :

```bash
curl -s http://corpus.m3ak.localhost/tableau-de-bord -o tableau.html
```

Elle montre : le total des documents et des articles segmentés, la
répartition par source (documents, classes de qualité, langues,
articles), la distribution des classes A/B/C et la quarantaine, les
volumes d'indexation portés par le service recherche (segments, fiches,
décisions — « indisponible » si le service ne répond pas, timeout de
3 s) et les cinq dernières sessions de collecte (manifestes).

La page ne porte que des agrégats — aucune donnée de contenu — et est
ouverte (comme /health) pour pouvoir être ouverte depuis un navigateur ;
les données détaillées restent derrière `/internal/documents/stats` et
les compteurs du service recherche, protégés par jeton interne. L'URL du
service index se règle par `CORPUS_INDEX_SERVICE_URL` (défaut :
`http://recherche:8002`, le nom du conteneur sur le réseau Compose).

## 19. Première veille — diff des sources (phase 1)

Le service corpus embarque une veille légère (`app/veille.py`) : scan
des mêmes périmètres que les collectes (7 thèmes Adala, 9 thématiques
Idarati, référentiel complet Jurisprudence.ma) **sans aucun
téléchargement**, puis diff avec le corpus — nouveaux (à collecter),
disparus (à examiner), et, pour la jurisprudence, modifiés (champ
`modified` de la source postérieur à notre ingestion ; la réconciliation
est manuelle en V1). Le rapport est tracé en manifeste (source
`veille`) et les sessions apparaissent dans le tableau de bord.

```bash
JETON=$(grep '^INTERNAL_TOKEN=' infra/.env | cut -d= -f2)

# lancer un scan (~10 min, requêtes polies, aucun téléchargement)
curl -s -X POST -H "Host: corpus.m3ak.localhost" -H "X-Internal-Token: $JETON" \
     http://127.0.0.1/internal/veille/executer

# suivre puis lire le rapport
curl -s -H "Host: corpus.m3ak.localhost" -H "X-Internal-Token: $JETON" \
     http://127.0.0.1/internal/veille/statut
```

Le déclenchement est manuel ; l'automatisation périodique (hebdomadaire
textes et jurisprudence, mensuel procédures — AGENTS.md §5) est du
ressort de la phase 5.

## 20. Recherche hybride (phase 2, étape 1)

Le service recherche combine plein texte (PostgreSQL : stemmer français
+ normalisation arabe applicative) et vectoriel (BGE-M3 auto-hébergé,
1024 dimensions), fusionnés par RRF. Le serveur d'embeddings est le
conteneur d'infrastructure `m3ak-tei` (Text Embeddings Inference, CPU) ;
son absence dégrade gracieusement en plein texte seul (mode « texte »,
visible dans la réponse).

### Profils mémoire de la machine (32 Go, inférence CPU)

Depuis le passage à 32 Go, la stack complète **avec** les deux serveurs
d'inférence (embeddings + reranker, ~4-5 Go chacun dans la VM) tient en
résidence : `vmmemWSL` ~8 Go, ~9 Go libres, Windows à l'aise. Deux profils :

- **Quotidien** : les 16 conteneurs up, `.wslconfig` facultatif (le défaut
  de WSL2 sur 32 Go — 16 Go pour la VM — est équilibré ; pour un plafond
  explicite, `memory=14GB` puis `wsl --shutdown` Docker Desktop fermé).
- **Machine chargée** (génération LM Studio + stack simultanées, ou
  applications lourdes) : réduire aux conteneurs utiles, comme validé en
  exploitation — garder `recherche`, `recherche-db`, `rabbitmq`, `redis`,
  `traefik`, `tei-embeddings` (+ `tei-reranker` si reranking), arrêter le
  reste (`corpus`, `corpus-db`, `minio`, `conversation*`, `documents*`,
  `llm*`, `whatsapp` — les files attendent dans RabbitMQ, rien ne se perd) :

  ```bash
  docker compose stop corpus corpus-db minio conversation conversation-db \
      documents documents-db llm llm-db whatsapp
  ```

- Backfill et mesures du jeu doré : opérations CPU longues — se lancer
  avec LM Studio fermé ; ils sont idempotents et reprennent seuls.

### Premier démarrage du serveur d'embeddings

```bash
docker compose up -d tei-embeddings
docker logs -f m3ak-tei        # télécharge BGE-M3 (~2,3 Go) puis "Ready"
```

L'état du serveur est exposé par le service recherche :

```bash
curl -s -H "Host: recherche.m3ak.localhost" -H "X-Internal-Token: $JETON" \
     http://127.0.0.1/ready
```

### Migration du schéma d'index (une fois, à la phase 2)

Les colonnes plein texte et vectorielles des tables existantes sont
ajoutées par le fichier de migration versionné, appliqué via psql
(dans le dossier du dépôt) :

```bash
docker exec -i m3ak-recherche-db psql -U u_recherche -d db_recherche \
  < services/recherche/app/migrations/001-index-phase2.sql
```

Deux erreurs `relation "passages" does not exist` sont attendues au
premier passage si le service recherche n'a pas encore démarré : la
table `passages` est créée complète (avec ses colonnes tsv/embedding)
par le service lui-même. Les migrations suivantes vivront dans le même
dossier (`app/migrations/`), jamais assemblées dans le code.

### Backfill des embeddings (une fois)

Remplit les vecteurs des unités indexées avant la phase 2, génère les
passages des décisions et crée les index HNSW. Idempotent : re-lancer ne
traite que ce qui manque.

⚠️ **Recherche approximative HNSW** : par défaut pgvector explore
`ef_search = 40` candidats — une requête LIMIT 100 ne rendait que ~40
résultats et les vrais plus proches voisins pouvaient manquer (constaté
en étape 6 : un attendu au rang 1 exact absent du résultat du service).
Le service pose désormais `SET hnsw.ef_search = 200` (deux fois la
limite) sur chaque requête vectorielle ; le cache des recherches est
versionné (`v2`) pour ne jamais resservir les listes tronquées d'avant
correction.

```bash
# lancer (~1-3 h sur CPU ; réponse immédiate, tâche de fond)
curl -s -X POST -H "Host: recherche.m3ak.localhost" -H "X-Internal-Token: $JETON" \
     http://127.0.0.1/internal/index/backfill

# suivre la progression
curl -s -H "Host: recherche.m3ak.localhost" -H "X-Internal-Token: $JETON" \
     http://127.0.0.1/internal/index/backfill/statut
```

### Interroger la recherche

```bash
curl -s -X POST -H "Host: recherche.m3ak.localhost" -H "X-Internal-Token: $JETON" \
     -H "Content-Type: application/json" \
     -d '{"question":"Comment résilier un bail commercial ?","top_k":5}' \
     http://127.0.0.1/internal/recherche
```

Les résultats portent `famille` (segment / fiche / passage), `source` et
`collection` — la hiérarchie des sources sera appliquée par la couche
conversation (étape 4).

### Mesurer la récupération sur le jeu doré

```bash
python tools/mesurer_recherche.py --k 5
```

Le rapport (recall@k, MRR, par langue et par famille, questions ratées)
est versionné dans `pilot/jeu_dore/resultats/`. Pour re-mesurer avec un
cache froid : `docker exec m3ak-redis redis-cli FLUSHDB` avant.

### Reranking (phase 2, étape 2)

Le moteur envoie le pool entrelacé (40 candidats × 800 caractères, choix
du porteur) au reranker croisé `m3ak-tei-reranker` (BGE-reranker-v2-m3,
ONNX int8) qui re-classe par pertinence question-passage ; les scores
sigmoïdes (0-1) remplacent les scores RRF dans la réponse (`rerank: true`).
Indisponible → ordre d'entrelacement conservé, sans erreur.

Points de fonctionnement mesurés sur le jeu doré (290 questions) :

| Configuration | recall@5 | MRR | Latence moy. |
|---|---|---|---|
| Sans rerank (mode dégradé) | 59,7 % | 0,439 | 0,5 s |
| Rerank pool 40, annotations v2.0 | 66,2 % | 0,531 | 11,1 s |
| **Rerank pool 40, annotations v2.1 + max-pooling + thématiques (en place)** | **76,9 %** | 0,571 | 9,6 s |

Familles en v2.1 : segments 96,5 %, fiches 78,5 %, **passages 52,6 %**
(MRR 0,296) — la jurisprudence est rattrapée par la combinaison :
annotations multi-attendus (mesure juste — l'utilisateur veut une réponse
valable, pas la décision n° X), max-pooling des passages par décision
(une décision = son meilleur passage, résumé préféré en score proche ;
les fenêtres ne se concurrencent plus), thématiques intégrées au plein
texte des passages. Restent 8 questions DEC-AR réellement difficiles
(polysemie « وكالة », cas sans parent thématique proche).

Points d'exploitation connus :
- ~11 s par question sur CPU (recalibrage possible à l'étape 6) ;
- le modèle int8 vit dans le snapshot BAAI du volume TEI
  (`onnx/model.onnx`, conversion BGLAW) — si le volume est recréé,
  retélécharger l'original puis replacer l'ONNX ;
- seuil de refus calibré sur le jeu doré : **0,75** (score rerank du
  meilleur résultat ; pertinent médian 0,948, non pertinent médian
  0,438) — la couche conversation (étape 4) le consommera.

## 21. Service llm — génération à fournisseurs commutables (phase 2, étape 3)

Brique interne « messages → texte généré » : trois fournisseurs parlant
le protocole OpenAI, commutables par configuration — **Z.ai (GLM)**,
**Google AI Studio** (couche OpenAI de Gemini) et **serveur local**
(LM Studio en dev, Ollama/llama.cpp à l'hébergement). Quota global
plateforme (fenêtres glissantes Redis : requêtes/minute + jetons/jour),
file d'attente (la requête patiente au lieu d'échouer), journal de
consommation en base.

### Configuration (infra/.env)

```bash
LLM_FOURNISSEUR_DEFAUT=zai              # zai | google | local
ZAI_API_KEY=...                         # z.ai model
LLM_ZAI_URL=https://api.z.ai/api/paas/v4  # Coding Plan : .../api/coding/paas/v4
LLM_ZAI_MODELE=glm-5.3-flash
GOOGLE_API_KEY=...                      # aistudio.google.com/apikey (formats AIza ou AQ. récents)
LLM_GOOGLE_MODELE=gemini-3.8-flash
LLM_LOCAL_URL=http://host.docker.internal:1234/v1
LLM_LOCAL_MODELE=                       # vide = premier modèle de /models
LLM_QUOTA_REQUETES_MINUTE=20
LLM_QUOTA_JETONS_JOUR=500000
```

Après modification : `docker compose up -d llm` (recrée le conteneur).
Basculer de fournisseur = changer `LLM_FOURNISSEUR_DEFAUT` (+ la clé du
fournisseur choisi) — aucune réécriture de code. Une demande peut aussi
forcer un fournisseur ponctuel (`"fournisseur":"google"`).

### Utiliser LM Studio comme fournisseur local

1. LM Studio → charger **Gemma 4 E4B** (déjà téléchargé) ;
2. Onglet **Developer** → **Start server** (port 1234) ;
3. Vérifier depuis l'hôte : `curl http://localhost:1234/v1/models`.

Le service interroge `host.docker.internal:1234` depuis son conteneur
(Docker Desktop Windows le résout nativement). Modèle vide dans
`LLM_LOCAL_MODELE` = auto-détection du premier modèle chargé.

⚠️ **Modèles à raisonnement hybride** (Gemma 4 en local comme Gemini
3.8 Flash côté Google) : ils consomment des jetons en réflexion avant de
répondre — prévoir un budget généreux (à 128-300 `max_jetons`, tout part
en réflexion et le contenu arrive vide) ; si le contenu reste vide, le
service restitue le raisonnement plutôt que rien. Gemma sur CPU :
~6 jetons/s, soit 1-2 min par réponse. Ces modèles généralistes
hallucinent sur le droit marocain sans passages du corpus injectés
(rôle de la couche conversation, étape 4) — Gemini 3.8 Flash connaît les
grandes lois marocaines mais reste soumis au refus si source
insuffisante.

Limites Google : aucune en-tête de quota exposée ; le quota plateforme
du service (20 req/min, 500 k jetons/jour — ajustables dans `.env`)
protège en amont. Le catalogue varie selon l'âge du projet : un projet
neuf peut se voir refuser gemini-2.5-flash (« no longer available to
new users ») au profit de gemini-3.8-flash.

### Génération en flux SSE (étape 3bis)

`POST /internal/generation/stream` — même demande que la génération en
un bloc, réponse en `text/event-stream` (contrat `EvenementGeneration`,
contrats v0.6.0) :

- `debut` (un seul, en tête) : fournisseur et modèle résolus ;
- `fragment` (répété) : morceaux de texte au fil de la production ;
- `fin` : usage final (jetons, latence, attente en file) ;
- `erreur` : incident fournisseur après ouverture (le statut HTTP est
  déjà parti) — sinon les erreurs restent HTTP propres (401, 400, 429).

Le quota, la file d'attente et la journalisation s'appliquent à
l'identique. Les blocs de réflexion des modèles hybrides (Gemma,
Gemini) sont filtrés en continu, et le repli sur le raisonnement reste
actif si le contenu arrive vide.

```bash
# le -N désactive la mémoire tampon de curl : les fragments arrivent en direct
curl -N -X POST -H "Host: llm.m3ak.localhost" -H "X-Internal-Token: $JETON" \
     -H "Content-Type: application/json" \
     -d '{"messages":[{"role":"user","contenu":"Dis bonjour en une phrase"}],"max_jetons":512}' \
     http://127.0.0.1/internal/generation/stream
```

⚠️ Google peut être momentanément saturé (503 « high demand ») ou
tronquer un flux en pleine phrase ; le 503 arrive alors en événement
`erreur` propre. Les réponses tronquées ne sont pas détectables dans le
flux : c'est le rôle des vérifications de la couche conversation (étape
4) de ne pas servir un texte incomplet.

### Tester

```bash
JETON=$(grep '^INTERNAL_TOKEN=' infra/.env | cut -d= -f2)

# santé + fournisseur actif
curl -s -H "Host: llm.m3ak.localhost" -H "X-Internal-Token: $JETON" \
     http://127.0.0.1/ready

# génération (fournisseur par défaut)
curl -s -X POST -H "Host: llm.m3ak.localhost" -H "X-Internal-Token: $JETON" \
     -H "Content-Type: application/json" \
     -d '{"messages":[{"role":"user","contenu":"Dis bonjour en une phrase"}],"max_jetons":512}' \
     http://127.0.0.1/internal/generation

# forcer un fournisseur : ajouter "fournisseur":"zai" | "google" | "local"

# état du quota et journal de consommation
curl -s -H "Host: llm.m3ak.localhost" -H "X-Internal-Token: $JETON" \
     http://127.0.0.1/internal/generation/quota
curl -s -H "Host: llm.m3ak.localhost" -H "X-Internal-Token: $JETON" \
     http://127.0.0.1/internal/generation/compte
```

Erreurs propres attendues : 429 « quota du fournisseur … épuisé » (le
free tier Mistral peut descendre à 0 requête/minute quand son quota est
épuisé), 429 « quota plateforme » (patience dépassée), 502 (fournisseur
injoignable — LM Studio fermé), 400 (clé invalide — ex. clé Google
mal copiée). L'orchestration conversationnelle (citations, refus,
anonymisation) est l'étape 4.

## 22. Service conversation — orchestrateur RAG (phase 2, étape 4)

Le cœur métier. Pipeline d'une question : langue du message →
anonymisation locale (jetons `[PERSONNE_n]`, sans re-substitution —
détecteur heuristique V1 : formules de présentation et titres ; un NER
prendra le relais en phase 5) → recherche hybride (question anonymisée,
top 8) → **refus** si aucune source ou meilleur score sous le seuil
(zéro appel LLM) → prompt sous délimiteurs stricts `<passage>` +
instruction système invariable (citations [n] obligatoires, verbatim
entre « », hiérarchie des sources, échappatoire `INSUFFISANT`) →
génération **via le flux du service llm** (§21) → **vérification
mécanique** des citations (renvois [n] existants, citation verbatim
retrouvée à espaces normalisés dans les passages cités) → avertissements
contextuels (droits, argent, liberté). Un texte invérifiable est refusé,
jamais servi.

### Points d'entrée (publics, accès sans compte en V1)

```bash
SID=$(curl -s -X POST -H "Host: conversation.m3ak.localhost" \
     http://127.0.0.1/sessions | python -c "import sys,json;print(json.load(sys.stdin)['session_id'])")

# mode bloc — harnais du jeu doré (temperature 0, réponse vérifiée ou refus)
# "fournisseur" (option de test/évaluation) : zai | google | local — absent = défaut plateforme
curl -s -X POST -H "Host: conversation.m3ak.localhost" -H "Content-Type: application/json" \
     -d '{"question":"Comment obtenir une copie intégrale de l'"'"'acte de naissance au Maroc ?"}' \
     http://127.0.0.1/sessions/$SID/messages
curl -s -X POST -H "Host: conversation.m3ak.localhost" -H "Content-Type: application/json" \
     -d '{"question":"...","fournisseur":"google"}' \
     http://127.0.0.1/sessions/$SID/messages

# mode flux SSE — statut (recherche/generation), sources, fragment*, fin
curl -N -X POST -H "Host: conversation.m3ak.localhost" -H "Content-Type: application/json" \
     -d '{"question":"..."}' http://127.0.0.1/sessions/$SID/messages/flux

# historique et signalement
curl -s -H "Host: conversation.m3ak.localhost" http://127.0.0.1/sessions/$SID
curl -s -X POST -H "Host: conversation.m3ak.localhost" -H "Content-Type: application/json" \
     -d '{"motif":"réponse erronée"}' http://127.0.0.1/messages/<message_id>/signaler
```

### Réglages (CONVERSATION_*, défauts dans app/config.py)

- `SEUIL_REFUS=0.40` — **l'étalonnage 0,75 de l'étape 2 ne transfère pas**
  : les paires interlingues (question fr → source ar) scorent
  systématiquement plus bas (fiche pertinente mesurée à 0,19) ; 0,40
  reste borné par la médiane « non pertinent » de l'étalonnage (0,438).
  Recalibrage complet sur le pipeline à l'étape 5.
- `DELAI_RECHERCHE_SECONDES=60` — premier appel à froid (embeddings +
  rerank 40×800 car) au-delà de 30 s machine chargée ; ensuite ~31 ms
  (cache).
- `DELAI_LLM_SECONDES=600` — la génération passe par le **flux** du llm :
  pas de timeout de lecture pendant la réflexion ; la borne couvre le
  pire cas local (mesuré : 270 s pour 8 passages + réflexion Gemma).
- `MAX_JETONS_GENERATION=4096` — réflexion des modèles « thinking » incluse (GLM 5.3 consomme jusqu'à ~2 500 jetons de raisonnement avant de rédiger) ; une troncature résiduelle déclenche une régénération à budget doublé (orchestrateur)
  (678 jetons de réflexion et une réponse coupée à 1 536 mesurés pour
  Gemma 4 sur une question de licenciement). Si le budget est atteint
  malgré tout, le motif de fin du fournisseur (`finish_reason`) remonte
  au contrat (`complet`) et la réponse tronquée est **refusée**
  (`generation_tronquee`) — jamais servie à moitié.

### Retours d'expérience réels (2026-10-02)

- **Option fournisseur** : `"fournisseur"` dans la demande de question
  (mistral | google | local) force le fournisseur pour cette question —
  validé : mistral → 429 propre (quota bridé), inconnu → 502, google →
  429 RPD. Destinée au harnais (étape 5 : même question sur chaque
  fournisseur) ; l'interface publique ne l'exposera pas.

- **Réponse vérifiée de bout en bout** (google/gemini-3.8-flash, 8 s) :
  explication en français, citations verbatim arabes entre « », renvois
  [n] vers la fiche Idarati — la stratégie de langue actée fonctionne.
- **Refus propre** hors corpus (meilleur score 0,0004 → refus sans appel
  LLM) et **refus mécanique** quand le texte généré n'est pas
  vérifiable.
- **Gemma 4 en local** : sur les prompts longs (8 passages), la réflexion
  consomme le budget et le contenu arrive vide ou sans citations → refus
  mécanique (comportement sûr). Le tableau comparatif des fournisseurs
  sera dressé à l'étape 5 ; option : désactiver la réflexion étendue de
  Gemma dans LM Studio.
- **Google free tier** : quota quotidien épuisable (429 RPD) → message
  digne « quota de génération atteint — réessayez un peu plus tard ».
- Le timeout fournisseur du service llm est passé à 300 s (§21) : en
  local, le traitement du prompt précède le premier morceau de plusieurs
  minutes — à 120 s, LM Studio voyait « Client disconnected » avant la
  réflexion.

## 23. Mesure de bout en bout sur le jeu doré (phase 2, étape 5)

`tools/mesurer_bout_en_bout.py` — la mesure complète du moteur RAG :
chaque question traverse session → conversation → recherche →
génération → vérification, en mode bloc (température 0, reproductible).
Mesures mécaniques : rappel des attendus dans les **sources retenues
par la réponse** (recall, MRR — l'étape 1 mesurait la recherche seule),
taux de refus par motif, citations vérifiées, latences.

```bash
# validation rapide (3 questions)
python tools/mesurer_bout_en_bout.py --taille 3 --fraiche

# échantillon stratifié de 60 (≈ 4 h en local) — reprise sur interruption
python tools/mesurer_bout_en_bout.py --taille 60
python tools/mesurer_bout_en_bout.py --taille 60 --fraiche   # repart de zéro

# tout le jeu (290 questions — several heures)
python tools/mesurer_bout_en_bout.py
```

Fournisseur : `--fournisseur local` (défaut, décision du porteur — les
API quota-limitées sont écartées de la mesure ; leurs règles s'y
transposent). Point de contrôle :
`resultats/bout-en-bout-point-de-controle.jsonl` (une ligne par
question — suppression = reprise à zéro). Rapports versionnés :
`resultats/bout-en-bout-<date>-<fournisseur>.json`. La fidélité au
corpus est garantie par la vérification verbatim du service ; les
métriques RAGAS à juge LLM (faithfulness, answer relevancy) restent
reportées (sobriété).

## 24. Interface web — frontend (phase 3, étape 1)

`services/frontend` — application Next.js 16 (App Router, TypeScript
strict, Tailwind v4), bilingue ar-MA/fr-MA avec RTL natif : deux pages
statiques prérendues (`/fr`, `/ar`), dictionnaires JSON typés vérifiés
à la compilation (`dico/fr.json`, `dico/ar.json` — une clé manquante
casse le build), redirection de `/` par cookie puis Accept-Language,
`lang`/`dir` portés par `<html>` (BCP 47 : `ar-MA`, `fr-MA`). L'entrée
publique de la plateforme : Traefik route `m3ak.localhost` vers le
conteneur (image standalone, utilisateur non-root, healthcheck `/fr`).

### Commandes locales (sans Docker)

```bash
npm run dev --workspace services/frontend        # http://localhost:3000
npm run lint --workspace services/frontend       # eslint (flat config)
npm run build --workspace services/frontend      # build de production
```

### Conteneur

```bash
npm run docker:build && npm run docker:up
curl -H "Host: m3ak.localhost" http://localhost/fr   # page d'accueil
```

### Chat (étape 2)

Le navigateur passe par le proxy BFF même origine : `/api/conversation/**`
(allowlist stricte des routes publiques, transmission par `node:http`)
vers le service conversation — pas de CORS, une seule origine exposée.

- Conteneur : `URL_CONVERSATION=http://conversation:8003` (déjà dans le
  compose ; le frontend est sur les réseaux edge + internal).
- Développement : le service n'est joignable que via Traefik (routing
  par Host) et le fetch de Node ignore l'en-tête `Host` — d'où le proxy
  en `node:http` et ces variables :

```bash
URL_CONVERSATION=http://127.0.0.1 ENTETE_HOST_CONVERSATION=conversation.m3ak.localhost \
  npm run dev --workspace services/frontend
```

Tests unitaires (parseur SSE, réducteur du fil, parité fr/ar, durées) :
`npm run test --workspace services/frontend` — inclus dans
`npm run qualite` et dans la CI.

Le flux est **réellement progressif** (service conversation 0.3.0) : le
fournisseur transmet ses morceaux au fur et à mesure et chaque phrase
complète est vérifiée contre les passages AVANT diffusion ; si une
phrase échoue (citation hallucinée, troncature), le flux se clôt en
refus et l'interface remplace le texte déjà affiché. L'attente est
matérialisée par un chronomètre ; l'événement `fin` porte la durée
totale. Les réponses sont rendues en Markdown (react-markdown) — les
renvois [n] y sont des boutons qui ouvrent la source correspondante.

**Discussions multiples** : la barre latérale (tiroir sur mobile) liste
les discussions du navigateur (localStorage `m3ak-sessions`,
conversation active `m3ak-session`) — nouvelle discussion (session
créée côté service seulement à la première question), navigation,
suppression en deux temps (`DELETE /sessions/{id}` — service 0.4.0,
messages compris ; route ajoutée à l'allowlist du proxy BFF). Sans
comptes, le service n'expose volontairement AUCUNE liste de sessions.

**Flux et discussions en arrière-plan** : le moteur du chat (état, flux
SSE, stockage local) est un **magasin externe au niveau du module**
(`lib/chat/moteur.ts`, `useSyncExternalStore`, sans dépendance) —
PAS un contexte React : Next remonte l'arbre `[lang]` au changement de
langue, et seul un état hors React y survit. Chaque flux alimente SA
discussion même si une autre est affichée ; la restauration au montage
est idempotente (elle ne recharge jamais une discussion en cours).

**Consultation des textes officiels (PDF, étape 3)** : le bouton
« Consulter le texte intégral » des sources officielles ouvre une modale
pdf.js qui rend les pages en CANVAS — le fichier n'est jamais exposé en
téléchargement direct. Chaîne : navigateur → `/api/sources/pdf/<id>`
(proxy BFF, limitation de débit en mémoire) → corpus
`GET /internal/documents/<id>/pdf` (jeton interne côté serveur).
Protection raisonnable contre le téléchargement occasionnel et le
raclage en masse ; aucune protection absolue n'existe côté client (les
octets transitent par le navigateur — relever toute exigence forte au
cahier des charges).

### Pièges connus

- **Worker pdf.js** : copié dans `public/` par les hooks
  `predev`/`prebuild` (scripts/copier-worker-pdf.mjs) — ne pas l'ajouter
  à git (ignoré) ; sans lui, pdf.js rend en thread principal. Ne JAMAIS
  afficher un PDF par iframe native : les visionneuses navigateur
  offrent un bouton Télécharger.

- **Remontage `[lang]`** : changer de langue (/fr ↔ /ar) REMONTE tout
  l'arbre de la route — tout état React détenu au-dessus des pages
  (contexte, provider dans le layout) est perdu. D'où le magasin
  hors React du moteur de chat.

- **Sémaphore local du service llm** (llm 0.3.1) : la concurrence locale
  est bornée (une génération à la fois) — si le client d'une génération
  disparaît sans que la fermeture se propage, le sémaphore peut rester
  retenu et les questions suivantes patientent en silence (symptôme :
  flux ouvert côté conversation, AUCUN appel à LM Studio dans ses logs).
  Depuis 0.3.1, l'acquisition est bornée (`attente_max_secondes`) :
  erreur propre « fournisseur local occupé » au lieu du blocage infini.
  En cas de doute sur une stack en cours : `docker compose restart llm`.

- **Route handler du proxy** : toute méthode HTTP transmise doit être
  exportée (`export { aiguiller as DELETE }`) — une méthode manquante
  renvoie 405 côté Next, et l'interface l'ignore comme un échec réseau.

- **Polices** : auto-hébergées via les paquets `@fontsource` (paquets
  npm, importés dans le layout) — `next/font/google` est écarté : il
  exige une connexion à fonts.gstatic.com à chaque build/dev (échec
  hors proxy, conteneur compris) et faisait échouer la page en 500.
- **Standalone** : le serveur `server.js` ne sert les chunks CSS/JS et
  les polices que si `.next/static` a été copié à côté de lui — le
  Dockerfile le fait ; en test manuel, copier puis redémarrer le
  serveur (la liste des statiques est lue au démarrage).
- **HOSTNAME** : le serveur standalone lie l'écoute à
  `process.env.HOSTNAME` — Git Bash définit `HOSTNAME=<machine>` :
  en local, forcer `HOSTNAME=127.0.0.1` (le Dockerfile force déjà
  `0.0.0.0` pour le conteneur).
- **proxy.ts** : convention Next 16 (le fichier `middleware.ts` est
  déprécié) — export par défaut `proxy`, matcher en `export const
  config`. L'exclusion des fichiers statiques (segments à point) se
  fait explicitement dans le code : le matcher de Next ne gère pas
  fiablement les regards négatifs à jokers (`.*\..*`).
- **Audit npm** : 5 vulnérabilités « high » résiduelles dans la chaîne
  `eslint-config-next` (braces → fast-glob, amont) — outillage de lint
  uniquement, hors code produit ; suivies. Le passage à Next 16 a
  éliminé celles de postcss (build).
- **Accents de langue** : la langue de l'URL est la source de vérité ;
  le cookie `m3ak-lang` ne sert qu'au choix initial du visiteur.
