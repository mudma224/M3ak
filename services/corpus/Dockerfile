# Image du service corpus — python 3.12-slim, utilisateur non-root.
# Contexte de build : racine du dépôt (pour inclure le paquet contracts).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN groupadd --system app && useradd --system --gid app --create-home app

WORKDIR /opt/service

# Contrats partagés d'abord (mise en cache de couche)
COPY contracts /opt/contracts
RUN pip install /opt/contracts

# Puis le service lui-même
COPY services/corpus/pyproject.toml ./pyproject.toml
COPY services/corpus/app ./app
RUN pip install .

USER app
EXPOSE 8001

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health')"

CMD ["python", "-m", "app"]
