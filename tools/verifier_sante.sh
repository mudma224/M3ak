#!/usr/bin/env bash
# Vérification de santé de l'ossature M3ak (phase 0).
# Prérequis : les conteneurs sont lancés (docker compose up -d dans infra/).
# Usage : bash tools/verifier_sante.sh
set -u

REPERTOIRE_INFRA="$(cd "$(dirname "$0")/../infra" && pwd)"
JETON="$(grep -E '^INTERNAL_TOKEN=' "$REPERTOIRE_INFRA/.env" | cut -d= -f2-)"

if [[ -z "$JETON" ]]; then
  echo "ERREUR : INTERNAL_TOKEN introuvable dans infra/.env" >&2
  exit 1
fi

BASE="http://127.0.0.1"
ECHECS=0

services=(corpus recherche conversation documents llm whatsapp)

verifier () {
  local description="$1"; local attendu="$2"; local chemin="$3"; shift 3
  local hote="$1"; shift
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: ${hote}.m3ak.localhost" "$@" "$BASE$chemin")
  if [[ "$code" == "$attendu" ]]; then
    echo "  OK   [$code] $description"
  else
    echo "  FAIL [$code != $attendu] $description"
    ECHECS=$((ECHECS + 1))
  fi
}

echo "— Santé publique (/health, sans jeton) —"
for s in "${services[@]}"; do
  verifier "$s" 200 "/health" "$s"
done

echo "— Routes protégées sans jeton (401 attendu) —"
for s in "${services[@]}"; do
  verifier "$s /ready" 401 "/ready" "$s"
done

echo "— Routes protégées avec jeton (200 attendu) —"
for s in "${services[@]}"; do
  verifier "$s /ready" 200 "/ready" "$s" -H "X-Internal-Token: $JETON"
done

echo "— Corrélation : identifiant fourni renvoyé —"
renvoye=$(curl -s -i -H "Host: corpus.m3ak.localhost" -H "X-Correlation-ID: verif-123" "$BASE/health" | grep -i '^x-correlation-id:' | tr -d '\r' | cut -d' ' -f2)
if [[ "$renvoye" == "verif-123" ]]; then
  echo "  OK   correlation_id=verif-123 renvoyé par corpus"
else
  echo "  FAIL correlation_id renvoyé : '$renvoye'"
  ECHECS=$((ECHECS + 1))
fi

echo "— Propagation inter-services (conversation -> recherche) —"
corps=$(curl -s -H "Host: conversation.m3ak.localhost" -H "X-Internal-Token: $JETON" -H "X-Correlation-ID: prop-42" "$BASE/demo/appel-recherche")
if echo "$corps" | grep -q '"correlation_id": *"prop-42"' && echo "$corps" | grep -q '"correlation_id": *"prop-42"' <<< "$(echo "$corps" | grep -o '"reponse_recherche":.*')"; then
  echo "  OK   correlation_id propagé jusqu'à recherche"
  echo "  corps : $corps"
else
  echo "  FAIL propagation : $corps"
  ECHECS=$((ECHECS + 1))
fi

echo ""
if [[ "$ECHECS" -eq 0 ]]; then
  echo "SUCCES : ossature conforme (0 échec)."
else
  echo "ECHECS : $ECHECS vérification(s) en erreur."
fi
exit "$ECHECS"
