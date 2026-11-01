# ADR-0001 — Architecture par services avec base de données par service

- **Statut** : accepté
- **Date** : septembre 2026
- **Décideurs** : porteur du projet

## Contexte

La plateforme M3ak démarre avec un effectif d'une personne et un périmètre
réduit (six services, faible trafic attendu en V1). Deux options s'offraient :
un monolithe modulaire (un processus, modules internes) ou une architecture
par services (plusieurs processus, frontières réseau). Le porteur a exprimé
une exigence explicite de résilience : une panne d'un composant ne doit pas
emporter les autres, et chaque service doit être le seul à accéder à sa
propre base de données, les autres passant par des appels internes.

## Décision

Adopter une **architecture par services à frontières fortes**, orchestrée
par Docker Compose (pas de Kubernetes à ce stade), avec le principe
**database-per-service** :

- six services : corpus, recherche, conversation, documents, llm, whatsapp ;
- une base PostgreSQL par service qui possède des données (cinq bases ;
  whatsapp sans base en V1), identifiants distincts détenus par un seul
  service ;
- communication inter-services exclusivement par API interne authentifiée
  (jeton partagé) pour le synchrone, et par RabbitMQ pour l'asynchrone ;
- aucune bibliothèque partagée de logique métier : seul le paquet
  `m3ak-contracts` (schémas) est commun.

## Conséquences

**Positives** : isolation des pannes au niveau processus ; propriété des
données claire, migrations sans coordination ; cycles de vie et montées en
version indépendants ; expérience d'ingénierie distribuée formatrice pour
le porteur ; chemin naturel vers Kubernetes.

**Négatives / acceptées** : coût opérationnel plus élevé (plusieurs bases,
courtier, passerelle, observabilité obligatoire) ; cohérence uniquement
finale sur le chemin d'indexation (assumé : l'index peut retarder de
quelques minutes) ; débogage multi-services exigeant (correlation ID et
journaux structurés dès la phase 0 pour compenser) ; risque de
sur-fragmentation (règle de gouvernance : un nouveau service n'existe que
s'il possède ses données ET un cycle de vie distinct).

## Références

- Sam Newman, *Building Microservices* (database per service, smart
  endpoints / dumb pipes).
- ADR-0002 pour le choix du courtier.
