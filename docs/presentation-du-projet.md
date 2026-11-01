# M3ak — Présentation du projet

## 1. Objet

M3ak est une plateforme numérique marocaine gratuite d'assistance juridique et administrative. Elle répond en langage naturel — en arabe ou en français — aux questions portant sur le droit marocain et les démarches administratives, et explique les documents administratifs. La plateforme est accessible par interface web responsive et, à terme, par WhatsApp.

## 2. Principes fondateurs

- **Réponses fondées sur un corpus contrôlé.** Chaque réponse est produite par génération augmentée par récupération (RAG) : le système s'appuie exclusivement sur des passages d'un corpus documentaire constitué et maîtrisé.
- **Citations vérifiées mécaniquement.** Chaque renvoi vers une source et chaque citation verbatim sont contrôlés par programme ; un texte dont la citation ne se retrouve pas mot à mot dans les passages fournis n'est jamais diffusé.
- **Refus explicite plutôt qu'invention.** Lorsque les sources ne permettent pas de répondre de façon fiable, la plateforme le dit, et oriente vers les passages consultés ou vers un professionnel.
- **Hiérarchie des sources visible.** Un texte officiel, une décision de justice et une procédure administrative ne sont jamais présentés comme équivalents.
- **Bilinguisme natif.** Les interfaces et réponses existent en arabe (ar-MA) et en français (fr-MA) ; les citations sont reproduites dans leur langue d'origine, l'explication est fournie dans la langue de l'utilisateur.
- **Informez et orientez, ne remplacez pas.** La plateforme ne produit pas de stratégie juridique personnalisée et ne remplace ni l'avocat, ni le juge, ni l'administration.
- **Gratuité et accès sans compte.** Aucune création de compte n'est requise ; les données personnelles ne sont ni stockées inutilement ni partagées (anonymisation avant tout envoi à un fournisseur externe).

## 3. Sources de données

| Source | Rôle |
|---|---|
| Adala (portail du ministère de la Justice) | Corpus principal des textes normatifs (dahirs, lois, décrets, conventions) |
| Idarati (portail des procédures administratives) | Fiches de démarches : pièces, conditions, délais, frais, administrations — avec autorisation écrite de réutilisation |
| Jurisprudence.ma | Décisions de justice : résumés en français, textes intégraux en arabe — avec autorisation écrite de réutilisation |

La collecte est polie (requêtes espacées, agent identifiable), tracée par manifestes, et organisée en trois couches de stockage : **brute** (documents tels que récupérés, jamais modifiés), **normalisée** (un enregistrement structuré par document), **index** (unités recherchables et vecteurs). Toute donnée affichée porte sa source et son lien vers l'original.

## 4. Architecture

Le système est composé de services à frontières fortes (microservices allégés), chaque service étant l'unique propriétaire de sa base de données — les autres y accèdent exclusivement par son API :

| Service | Responsabilité |
|---|---|
| **corpus** | Collecte des sources, stockage brut (MinIO), normalisation, segmentation des textes |
| **recherche** | Index hybride (plein texte arabe/français + vectoriel pgvector), reranking, embeddings auto-hébergés |
| **conversation** | Orchestrateur côté client : sessions, langue par message, vérification des citations, diffusion en flux |
| **llm** | Génération avec fournisseurs commutables (cloud ou local), gestion des quotas |
| **documents** | Import et explication de documents utilisateurs (OCR), suppression automatique après traitement |
| **whatsapp** | Adaptateur du canal Meta WhatsApp Cloud API |

L'infrastructure comprend Traefik (passerelle unique), RabbitMQ (files de travaux asynchrones), Redis (cache), MinIO (stockage d'objets) et une base PostgreSQL par service. Les échanges inter-services reposent sur des contrats versionnés (OpenAPI, schémas partagés).

## 5. Le parcours d'une question

1. **Langue** : la langue du dernier message détermine la langue de travail.
2. **Anonymisation** : les noms de personnes détectés dans la question sont remplacés avant tout traitement.
3. **Recherche hybride** : plein texte et recherche vectorielle combinés, puis re-classement (reranking) multilingue des meilleurs candidats.
4. **Génération encadrée** : le modèle reçoit des passages strictement délimités et une instruction invariable ; il peut répondre avec citations, ou décrire ce que contiennent les passages lorsqu'aucun ne répond à la question.
5. **Vérification mécanique** : chaque phrase est contrôlée avant diffusion ; la réponse arrive en flux continu, phrase vérifiée après phrase vérifiée.
6. **Issues possibles** : réponse complète vérifiée ; réponse partielle (extraits vérifiés servis si un échec survient en cours de génération) ; orientation vers les passages consultés quand aucun ne répond ; refus explicite s'il n'existe aucune source.

## 6. Technologies principales

- **Backend** : Python / FastAPI ; **Frontend** : Next.js (App Router, TypeScript).
- **Index** : PostgreSQL + pgvector, recherche hybride (RRF), embeddings BGE-M3 et reranker BGE auto-hébergés.
- **Génération** : interface d'abstraction multi-fournisseurs (Z.ai, Google, serveur local compatible OpenAI).
- **Infrastructure** : Docker Compose, conteneurs OCI, journaux structurés, métriques Prometheus.
- **Qualité** : OpenAPI 3.x, OWASP, WCAG 2.1 AA, tests automatisés (pytest, Vitest) et intégration continue.

## 7. Phases de construction

| Phase | Contenu |
|---|---|
| 0 — Fondations | Ossature multi-conteneurs, contrats d'API, corpus pilote, validation de bout en bout |
| 1 — Plateforme de données | Connecteurs des trois sources, couches de stockage, qualité et quarantaine, tableau de bord, veille |
| 2 — Moteur de réponse RAG | Recherche hybride, reranking, génération vérifiée, mesures sur jeu d'évaluation doré |
| 3 — Produit V1 | Interface web (chat, sources, checklist de démarche), canal WhatsApp, tests utilisateurs |
| 4 — OCR et documents | Import et explication de documents, conditionnés au feu vert de la commission nationale de protection des données |
| 5 — Industrialisation | Veille automatisée, monitoring, durcissement sécurité, ouverture progressive |
| 6 — Évolutions | Darija simplifiée, amazigh à l'étude, espace professionnels, application native si besoin |
