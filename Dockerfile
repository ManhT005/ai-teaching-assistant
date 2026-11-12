FROM python:3.11-slim-bookworm@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    EMBEDDING_CACHE_DIR=/opt/models \
    OMP_NUM_THREADS=2 \
    TOKENIZERS_PARALLELISM=false

WORKDIR /app

# CPU wheels keep CUDA libraries out of the distributable image.
COPY requirements.txt requirements-docker.lock ./
RUN pip install --no-cache-dir --no-deps --index-url https://download.pytorch.org/whl/cpu 'torch==2.7.1+cpu'
RUN pip install --no-cache-dir -r requirements.txt -r requirements-docker.lock && pip check

COPY app/__init__.py app/config.py app/db_url.py ./app/
COPY app/knowledge/__init__.py app/knowledge/embedding_contract.py app/knowledge/embeddings.py ./app/knowledge/
# The pinned E5 model is part of the image, including for offline handover.
RUN python -m app.knowledge.embeddings --download && chown -R 10001:10001 /opt/models

COPY app/ ./app/
COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY scripts/ ./scripts/
COPY data/samples/ ./data/samples/

RUN groupadd --gid 10001 assistant \
    && useradd --uid 10001 --gid assistant --create-home assistant \
    && mkdir -p /app/data/raw /app/data/generated /app/.local \
    && chown -R assistant:assistant /app

ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    EMBEDDING_LOCAL_FILES_ONLY=true \
    HOME=/home/assistant

USER assistant
EXPOSE 8000
ENTRYPOINT ["python", "-m", "scripts.container_entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
