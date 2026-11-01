# ADR-0004 — Turborepo comme orchestrateur de tâches du monorepo

- **Statut** : accepté et **mis en œuvre** (jalon B, phase 0) — turbo 2.10.13
- **Date** : septembre 2026
- **Décideurs** : porteur du projet

## Contexte

Le monorepo grandit : six services Python, un paquet de contrats, une
infrastructure Compose à quinze conteneurs, des scripts de vérification, et
bientôt la chaîne documentaire du jalon B (collecte pilote, segmentation,
rapports de qualité). Les commandes utiles se multiplient (build, up, logs,
tests, lint, export de schémas) et leur orchestration à la main devient un
fardeau : dépendances entre tâches, ordre d'exécution, exécution répétée
de travaux inchangés.

## Décision

Adopter **Turborepo** comme orchestrateur de tâches unique du monorepo,
mis en place au début du jalon B :

- un `package.json` racine + `turbo.json` définissant le graphe de tâches ;
- chaque tâche est un script (dans les `package.json` des espaces de
  travail ou du racine) qui encapsule les commandes réelles —
  `docker compose build/up/logs`, `pytest`, `ruff`, l'export des JSON
  Schema, les vérifications d'intégration, et les futurs scripts du
  pipeline documentaire ;
- utilisation systématique via `turbo run <tache> [--filter=...]` :
  exécution parallèle selon le graphe de dépendances, cache local des
  sorties (les travaux inchangés ne se rejouent pas), filtrage par service ;
- la règle de collaboration reste inchangée : **le porteur lance les
  conteneurs** ; turbo fournit les commandes, il ne les exécute pas à sa
  place.

## Conséquences

**Positives** : une seule entry point pour toutes les opérations du dépôt ;
parallélisation et cache (précieux sur les suites de tests et les builds) ;
filtrage par service (`--filter=corpus`) aligné avec l'architecture ;
outillage standard de l'écosystème, installé sur le poste du porteur
(Node 24 déjà présent).

**Négatives / acceptées** : dépendance à une chaîne d'outils Node alors que
le cœur du projet est Python — le coût est contenu (turbo n'exécute que des
scripts wrappés, aucune logique métier en JS) mais il faudra l'installer
dans la CI ; les commandes brutes (`docker compose`, `pytest`) restent
documentées dans le runbook pour ne pas rendre l'outillage opaque ;
`turbo.json` et les `package.json` deviennent des fichiers de configuration
à maintenir.

## Notes de mise en œuvre (jalon B)

Turborepo 2.10.13 est installé (devDependency racine, verrouillé par
`package-lock.json`). Deux familles de commandes coexistent :

- **Tâches par package** (`lint`, `test`, `schemas`), définies dans les
  `package.json` de `contracts/` et `services/*` : lancées par
  `turbo run lint test schemas` ou `npm run qualite` — exécution parallèle,
  cache local des résultats (un second passage sans modification s'exécute
  en moins d'une seconde).
- **Commandes globales** (`docker:build/up/down/ps/logs`, `verifier`,
  `demo:rabbitmq`) : simples scripts npm racine, car turbo n'exécute pas les
  scripts du package racine (il n'adresse que les espaces de travail). Ces
  commandes n'ont de toute façon rien à gagner du cache ou du parallélisme.
  Elles basculeront sous `turbo run` si un jour elles deviennent des tâches
  par package (par exemple un build d'images par service).

Prérequis constaté : l'environnement conda `m3ak` doit être sur le `PATH`
pour que les tâches `test` résolvent `python` (les scripts utilisent
`python -m pytest`). `node_modules/` et `.turbo/` sont ignorés par git.

## Références

- Turborepo — https://turborepo.com (task runner pour monorepos,
  exécution parallèle et mise en cache incrémentale).
- ADR-0003 pour la stack applicative (Python/FastAPI + Next.js — le
  frontend Next.js de la phase 3 rendra turbo doublement pertinent).
