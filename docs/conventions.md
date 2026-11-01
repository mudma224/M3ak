# Conventions de développement M3ak

## Dépôt et branches

- Monorepo unique ; branche principale `main`, toujours déployable en
  environnement de développement.
- Travail en branches courtes préfixées par le type : `feat/…`, `fix/…`,
  `docs/…`, `chore/…`, `refactor/…`, `test/…`.
- Fusion après validation de la CI (lint + tests verts).

## Commits (conventionnels)

Format : `type(portée): description à l'impératif, en minuscules`.

```
feat(recherche): upsert idempotent des segments par segment_id
fix(conversation): propage le correlation id vers llm
docs(runbook): procédure d'inspection des DLQ
```

## Style Python

- **Outil : ruff** (ligne ≤ 100 caractères, cible Python 3.12) — lancé par
  la CI sur `contracts/` et chaque service.
- Typage statique progressif ; `from __future__ import annotations` en tête
  de module.
- Nomenclature **en français** pour le domaine (fonctions, variables,
  docstrings) : la plateforme est francophone/arabophone, le code doit se
  lire comme la documentation. Les identifiants techniques standard
  (healthcheck, middleware…) restent en anglais lorsque c'est l'usage de
  l'écosystème.
- Docstring de module obligatoire ; commentaires réservés aux contraintes
  que le code n'exprime pas seul.

## Tests

- `pytest` par service et pour `contracts` ; tests **unitaires sans
  infrastructure** (httpx ASGITransport, pas de base ni de courtier).
- Les tests nécessitant l'infrastructure réelle sont marqués
  `integration` et exclus de la CI (lancés localement après
  `docker compose up`).
- Toute correction de bug s'accompagne du test qui l'aurait empêché.

## Contrats et versionnage

- **SemVer** partout : paquets (`m3ak-contracts`, services), et champ
  `schema_version` des messages RabbitMQ.
- Un changement de contrat = nouvelle version + période de compatibilité ;
  jamais de modification silencieuse d'un schéma publié.
- Les JSON Schema exportés (`contracts/schemas/`) sont régénérés à chaque
  évolution des modèles Pydantic.

## Sécurité

- Aucun secret dans le code ni dans l'historique git : tout par
  `infra/.env` (non versionné, voir `.env.example`).
- Toute requête SQL passe par SQLAlchemy (paramètres liés) — jamais de
  concaténation.
- Les nouvelles routes internes sont protégées par `exiger_jeton_interne`
  sauf décision explicite documentée.
- Le scanner Mimosa (sécurité) est un garde-fou : ses blocages se traitent
  en corrigeant le code, pas en contournant l'outil.

## Frontend (à partir de la phase 3)

- Next.js / React ; composants pensés RTL d'abord (`ar-MA`), puis LTR.
- Accessibilité WCAG 2.1 AA : structure de titres, contrastes, navigation
  clavier.

## Journalisation

- JSON structuré via `app/logging_setup.py` ; jamais de `print`.
- Le correlation ID est disponible partout via le contextvar ; il doit
  apparaître dans toute ligne de journal d'une requête.
