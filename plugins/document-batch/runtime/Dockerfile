FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ocrmypdf tesseract-ocr-eng tesseract-ocr-chi-sim && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY plugins/document-batch/runtime/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY harness/__init__.py /app/harness/__init__.py
COPY harness/contracts /app/harness/contracts
COPY plugins/document-batch /app/plugins/document-batch
COPY skills/files/path_guard.py /app/path_guard.py
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "uvicorn", "plugins.document-batch.runtime.app:app", "--host", "0.0.0.0", "--port", "8300"]
