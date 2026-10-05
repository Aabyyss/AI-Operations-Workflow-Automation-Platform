FROM python:3.12-slim

WORKDIR /app

# No compiled deps needed; layer-cached requirements.
# requirements-postgres.txt makes the optional AIOPS_STORAGE=postgres backend
# work without a second image variant (psycopg-binary is a small wheel).
COPY requirements.txt requirements-postgres.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-postgres.txt

COPY backend/ ./backend/
COPY knowledge_base/ ./knowledge_base/
COPY dashboard/ ./dashboard/
COPY scripts/backup.py scripts/migrate_to_postgres.py ./scripts/

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
