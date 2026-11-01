# ADR-0006 — Interface web : Next.js App Router, bilinguisme par route, Tailwind

- **Statut** : accepté et **mis en œuvre** (phase 3, étape 1)
- **Date** : octobre 2026
- **Décideurs** : porteur du projet

## Contexte

La phase 3 livre le produit V1 : le citoyen discute avec le moteur RAG,
lit les sources par nature, suit une checklist de démarche. L'ADR-0003
a retenu Next.js pour la couche affichage ; restaient à trancher la
structure bilingue, l'outillage de style et la topologie d'exposition,
sous les contraintes permanentes : bilinguisme natif (ar-MA, fr-MA,
RTL dès la conception), WCAG 2.1 AA, sobriété, accès sans compte.

## Décision

- **Application dans `services/frontend`** : Next.js 16 (App Router,
  TypeScript strict), workspace npm du monorepo — lint et build passent
  par turbo (ADR-0004) comme pour les autres services ; conteneur en
  sortie **standalone** (serveur auto-contenu, utilisateur non-root).
- **Bilinguisme par route `[lang]`** : deux pages statiques prérendues
  (`/fr`, `/ar`) ; dictionnaires JSON typés (`dico/fr.json`,
  `dico/ar.json`) vérifiés **à la compilation** — une clé manquante en
  arabe casse le build, le bilinguisme ne peut pas se désynchroniser.
  Le middleware redirige `/` selon le choix mémorisé (cookie), puis
  Accept-Language (analyse des q-valeurs) ; la langue de l'URL est la
  source de vérité. `lang` et `dir` portés par `<html>` (BCP 47 :
  `ar-MA`, `fr-MA`) ; utilitaires CSS logiques (`ms/me/ps/pe`,
  `text-start`) : le miroir RTL est automatique, sans variante dédiée.
- **Styles : Tailwind v4** (plugin PostCSS, thème en CSS) — palette
  institutionnelle sobre (vert marocain), contrastes AA vérifiés,
  mobile d'abord. Polices auto-hébergées via les paquets `@fontsource`
  (Inter Variable, IBM Plex Sans Arabic) — `next/font/google` a été
  écarté après coup : il exige une connexion à fonts.gstatic.com à
  chaque build (échec sur la machine du porteur comme dans le
  conteneur) ; les paquets sont versionnés par `package-lock.json`,
  aucune requête externe au build ni à l'exécution.
- **Exposition** : Traefik route `m3ak.localhost` vers le conteneur —
  le frontend devient l'entrée publique de la plateforme ; il
  consommera l'API publique du service conversation (étape 2).
- **Lint** : ESLint 9 (flat config, eslint-config-next) câblé sur la
  tâche turbo `lint` — `npm run qualite` l'intègre ; CI dédiée
  (lint + build) et image ajoutée à la matrice de build.

## Conséquences

**Positives** : entrée publique unique, sans compte ; RTL natif sans
duplication de composants ; i18n vérifié à la compilation ; pages
statiques (latence minimale, surface d'attaque réduite en V1) ;
montée en charge de l'étape 2 (chat SSE) sans refonte.

**Négatives / acceptées** : deuxième chaîne d'outils (Node) à côté de
Python — assumée depuis l'ADR-0004 ; Tailwind ajoute une étape de build
CSS ; Next 16 retenu plutôt que 15 pour un audit npm propre sur postcss
(5 vulnérabilités « high » résiduelles demeurent dans la chaîne
eslint-config-next — amont, outillage de lint uniquement, suivies).

## Notes de mise en œuvre (étape 1)

- **Convention Next 16** : `proxy.ts` (export par défaut `proxy`)
  remplace `middleware.ts` (déprécié) — même sémantique, matcher en
  `export const config`.
- **Sortie standalone** : les fichiers statiques (`.next/static`) ne
  sont pas inclus dans la sortie — ils doivent être copiés à côté de
  `server.js` dans l'image (le Dockerfile le fait) ; sinon 404 sur les
  chunks CSS/JS : pages sans style ni hydratation.
- **HOSTNAME** : le serveur standalone lie l'écoute à
  `process.env.HOSTNAME` — Git Bash définit `HOSTNAME=<machine>` ;
  le Dockerfile force `0.0.0.0`.

## Alternatives écartées

- **Sous-domaines par langue** (fr.m3ak… / ar.m3ak…) : certificats et
  configuration Traefik supplémentaires pour un bénéfice nul en V1.
- **i18n par cookie seul, sans URL localisée** : pas d'URL partageable
  dans la langue du visiteur, partage de lien rompu.
- **Bibliothèque i18n** (next-intl…) : deux langues, grammaire simple —
  un dictionnaire typé de quelques dizaines de lignes suffit (sobriété).

## Références

- ADR-0003 (stack applicative), ADR-0004 (turbo).
- AGENTS.md §6 — phase 3 : plan acté en 7 étapes (2026-10-03).
