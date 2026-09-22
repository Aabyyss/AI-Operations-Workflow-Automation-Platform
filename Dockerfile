FROM python:3.12-slim

WORKDIR /app

# No compiled deps needed; layer-cached requirements.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY knowledge_base/ ./knowledge_base/
COPY dashboard/ ./dashboard/
COPY scripts/backup.py ./scripts/backup.py

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
