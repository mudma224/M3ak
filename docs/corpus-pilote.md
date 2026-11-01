# Corpus pilote — stratégie de collecte idempotente (jalon B)

## Position du problème

Le corpus pilote est validé puis collecté. Comment garantir qu'un document
déjà téléchargé ne le soit jamais deux fois, sans jamais avoir à purger le
corpus ? La réponse tient en un principe et trois artefacts.

## Principe : le pilote n'est pas un bac jetable

Les fichiers collectés pendant le pilote **sont la couche brute du corpus
définitif** (cf. `schema-donnees.md` : la couche brute n'est jamais modifiée
ni supprimée). Il n'y a donc rien à supprimer à la fin du pilote, et rien à
re-télécharger : le connecteur Adala reprend le même registre et les mêmes
fichiers. Le pilote n'est pas une expérience à part, c'est le
premier dépôt du corpus.

## Trois artefacts, trois rôles

| Artefact | Rôle | Nature |
|---|---|---|
| `pilot/corpus_pilote.json` | **La décision** : qu'a-t-on décidé de cibler (sélection validée par le porteur, références vérifiées sur Adala avant figeage). | Figé, versionné git |
| `pilot/registre.json` | **L'état** : qu'avons-nous réellement (clé = chemin PDF source → empreinte sha256, chemin local, date). | Mis à jour à chaque collecte, versionné git, **reconstruisable** depuis les manifestes (`--reconstruire`) |
| `pilot/manifestes/collecte-*.json` | **La traçabilité** : un manifeste par session (date, méthode, agent, volume, empreintes) — conforme aux règles de collecte du projet (AGENTS.md §3.2). | Append-only, versionné git |
| `pilot/raw/` | Les PDF bruts eux-mêmes. | Ignoré par git (111 Mo au 2026-09-18) |

La liste figée ne bouge plus après validation ; l'état de collecte vit dans
le registre. Un document figure dans la liste mais pas encore au registre =
« à collecter ».

## Règles d'idempotence (implémentées dans `tools/collecte_pilote.py`)

1. **Skip direct** : chemin PDF source déjà au registre → aucun téléchargement.
2. **Empreinte sha256 systématique** : si un nouveau document a une empreinte
   déjà connue sous un autre chemin, il est collecté mais marqué
   `doublon_de` (même contenu servi à deux endroits — l'indexation
   dédupliquera).
3. **Versions** : même identifiant stable, contenu différent (texte modifié
   entre deux collectes) → nouvelle entrée, l'ancienne est conservée ; le
   choix de la meilleure extraction se fait en aval, jamais en supprimant
   la brute.
4. **Modifications silencieuses** : un PDF remplacé sous la même URL n'est
   pas détecté par la collecte (skip) — c'est le rôle de la **veille**
   (`app/veille.py` du service corpus), qui compare périodiquement les
   listes de la source au corpus ingéré.

## Articulation avec les connecteurs

- Le connecteur Adala réutilise le même format de registre et les mêmes
  empreintes : les 24 documents du pilote sont ingérés sans nouveau
  téléchargement (import par empreinte dans le stockage du service corpus).
- Le collecteur pilote est volontairement le prototype du connecteur :
  allowlist d'hôte stricte, résolution DNS vérifiée (adresses publiques
  uniquement), redirections interdites, agent utilisateur identifiable,
  une requête par 1,2 s maximum, nom de fichier local tronqué pour
  respecter la limite MAX_PATH de Windows.
- Les familles hors normatif (fiches Idarati, décisions Jurisprudence.ma)
  sont figurées dans la liste avec `statut: collecte différée` : leurs API
  propres sont traitées par leurs connecteurs dédiés (Idarati,
  Jurisprudence.ma).

## Commandes

```bash
python tools/collecte_pilote.py                 # collecte ce qui manque
python tools/collecte_pilote.py --dry-run       # montrer sans télécharger
python tools/collecte_pilote.py --cles A1,B1    # sous-ensemble
python tools/collecte_pilote.py --familles textes_anciens
python tools/collecte_pilote.py --reconstruire  # registre depuis les manifestes
```

## Collecte initiale (2026-09-18)

- 24 documents Adala collectés (111 Mo), 0 échec ; familles : codes à
  hiérarchie profonde (6), textes anciens (4), lois citoyennes (7),
  sociétés (3), décrets et versions (4).
- Quatre textes non indexés par la recherche Adala à cette date — loi 22-01
  (procédure pénale), loi 67-12 (baux d'habitation), loi 44-00 (VEFA),
  loi 5-96 (SAR) — remplacés par des équivalents structurels ; si la source
  les expose de nouveau, la veille les signalera comme nouveaux et la
  re-collecte idempotente les réintégrera.
