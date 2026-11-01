# Image du frontend — Next.js 16 (App Router), sortie standalone, utilisateur non-root.
# Contexte de build : racine du dépôt (workspaces npm — le lockfile est à la racine).

# ── Étage 1 : construction ─────────────────────────────────────────────────
FROM node:22-alpine AS construction

WORKDIR /app

# Manifestes de tous les espaces de travail d'abord (mise en cache de couche),
# puis installation déterministe depuis le lockfile racine.
COPY package.json package-lock.json ./
COPY contracts/package.json contracts/
COPY services/corpus/package.json services/corpus/
COPY services/recherche/package.json services/recherche/
COPY services/conversation/package.json services/conversation/
COPY services/documents/package.json services/documents/
COPY services/llm/package.json services/llm/
COPY services/whatsapp/package.json services/whatsapp/
COPY services/frontend/package.json services/frontend/
RUN npm ci

# Puis le code de l'application et sa construction
COPY services/frontend services/frontend
RUN npm run build --workspace services/frontend

# ── Étage 2 : exécution ────────────────────────────────────────────────────
# NODE_ENV ici seulement : next build (étage 1) doit voir les devDependencies.
FROM node:22-alpine

ENV NODE_ENV=production \
    HOSTNAME=0.0.0.0 \
    PORT=3000

RUN addgroup --system app && adduser --system --ingroup app --home /home/app app

WORKDIR /app

# Sortie standalone : serveur auto-contenu + fichiers statiques copiés à
# côté de server.js (sans eux : 404 sur les chunks — pages sans style).
COPY --from=construction /app/services/frontend/.next/standalone ./
COPY --from=construction /app/services/frontend/.next/static ./services/frontend/.next/static
# public/ : le worker pdf.js (copié par prebuild) est servi tel quel.
COPY --from=construction /app/services/frontend/public ./services/frontend/public

USER app
EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=5 \
  CMD wget -q --spider http://127.0.0.1:3000/fr || exit 1

CMD ["node", "services/frontend/server.js"]
