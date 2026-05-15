# Slim base; we install only the native libs the app actually uses.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Native deps:
#   libpango / libcairo / libgdk-pixbuf / shared-mime-info / fontconfig / fonts-*  -> WeasyPrint PDF export
#   tesseract-ocr                                                                   -> pytesseract OCR
#   poppler-utils                                                                   -> pdf2image
#   libpq5                                                                          -> asyncpg / Postgres client
#   build-essential / libffi-dev                                                    -> wheels that compile (cffi, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        shared-mime-info \
        fontconfig \
        fonts-dejavu \
        fonts-liberation \
        tesseract-ocr \
        poppler-utils \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Render injects $PORT at runtime.
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
