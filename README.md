# M3ak — Plateforme d'assistance juridique et administrative

M3ak est une plateforme marocaine gratuite d'assistance juridique et administrative fondée sur l'intelligence artificielle et des sources officielles vérifiables. Elle aide les étudiants en droit à apprendre et les citoyens à comprendre leurs documents et leurs démarches. Chaque réponse cite ses sources ; la plateforme informe et oriente, elle ne remplace ni l'avocat, ni le juge, ni l'administration.

## État du projet

Phases 1 et 2 terminées ; phase 3 (produit V1) en cours. La plateforme de données tourne de bout en bout : connecteurs Adala (7 thèmes majeurs, 2 881 documents — collectes gelées pour le prototypage), Idarati (1 034 fiches de procédures) et Jurisprudence.ma (4 397 décisions de justice) ; ingestion, segmentation arabe/français, qualité A/B/C avec quarantaine ; trois files d'indexation (segments, fiches, décisions) ; tableau de bord de volumétrie/qualité (`/tableau-de-bord`) et veille des sources. Le moteur de réponse RAG est opérationnel et mesuré : recherche hybride (plein texte + vectoriel) avec reranking multilingue, génération à fournisseurs commutables (quota global, file d'attente, streaming SSE), citations vérifiées mécaniquement, refus explicite quand le corpus ne suffit pas — sur un jeu doré de 290 questions : recall 72,4 %, MRR 0,608, taux de refus 20,7 %, 100 % des citations vérifiées. L'interface web est opérationnelle : chat branché sur le flux du service conversation (citations [n] cliquables, sources hiérarchisées par nature, refus dignes, avertissements contextuels, signalement), bilingue ar-MA/fr-MA avec RTL natif, servie sur `m3ak.localhost`.

## Démarrage rapide

Prérequis : Docker Desktop démarré ; Git configuré.

```bash
cd infra
cp .env.example .env        # puis adapter les mots de passe locaux
docker compose build
docker compose up -d
```

Vérifications usuelles (voir le [runbook](docs/runbook.md) pour le détail) :

```bash
docker compose ps                                  # tous les conteneurs doivent être "healthy"
curl -H "Host: conversation.m3ak.localhost" http://localhost/health
```

Interfaces locales : tableau de bord Traefik (`http://localhost:8080`), RabbitMQ (`http://localhost:15672`), console MinIO (`http://localhost:9001`).

### Commandes d'orchestration (racine du dépôt)

```bash
npm install                    # la première fois (installe Turborepo)
npm run qualite                # lint + tests + export des schémas (8 packages, avec cache)
npm run docker:ps          # état des conteneurs (wrappers : build/up/down/logs aussi)
npm run verifier               # 18 contrôles de santé de l'ossature
```

Le détail (tâches turbo par service, filtres, prérequis conda) est dans le [runbook §11](docs/runbook.md).

## Structure du dépôt

| Chemin | Contenu |
|---|---|
| `docs/` | Documentation : architecture, décisions (ADR), conventions, runbook, schéma de données |
| `contracts/` | Paquet Python partagé `m3ak-contracts` : schémas des documents, segments, fiches et décisions, messages |
| `infra/` | Docker Compose, configuration Traefik, variables d'environnement |
| `services/` | Les services : `corpus`, `recherche`, `conversation`, `llm` (opérationnels), `documents`, `whatsapp` (squelettes), `frontend` (interface web — phase 3) |
| `package.json`, `turbo.json` | Orchestration des tâches du monorepo (Turborepo — ADR-0004) |
| `tools/` | Outillage : vérification de l'ossature, collecte/pilotage du corpus (Adala, Idarati, Jurisprudence.ma), re-segmentation, analyse de quarantaine, classement de revue, rejeu d'indexation |

## Les services

| Service | Rôle | Possède |
|---|---|---|
| `corpus` | Pipeline documentaire (collecte, normalisation, extraction, segmentation) | Stockage brut + base des documents normalisés |
| `recherche` | Index et recherche hybride (plein texte + vectoriel) | Base d'index (PostgreSQL + pgvector) |
| `conversation` | Orchestration des échanges avec les utilisateurs | Base des sessions, messages, signalements |
| `documents` | Import et explication de documents (OCR) | Zone de stockage éphémère + base des traitements |
| `llm` | Génération via LLM (Z.ai GLM) avec gestion des quotas | Base de consommation des quotas |
| `whatsapp` | Canal WhatsApp (webhook Meta) | Sans base en V1 |
| `frontend` | Interface web bilingue (chat, sources, checklist) — entrée publique `m3ak.localhost` | Sans base — consomme l'API conversation |

Principe architectural : **chaque service est le seul à accéder à sa base** ; les autres passent par son API interne.

## Documentation

- [Architecture cible](docs/architecture.md)
- [Décisions d'architecture (ADR)](docs/adr/)
- [Conventions de développement](docs/conventions.md)
- [Runbook opérationnel](docs/runbook.md)
- [Schéma de données](docs/schema-donnees.md)
- [Stratégie du corpus pilote](docs/corpus-pilote.md)
