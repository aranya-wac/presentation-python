# WACDeckStudio Backend

AI-powered presentation generation platform built with FastAPI, Celery, and MongoDB.

## Stack

- **FastAPI** — async REST API
- **Beanie / Motor** — async MongoDB ODM
- **Celery + Redis** — background task queue
- **Google Gemini** — AI content generation
- **python-pptx / WeasyPrint / Reveal.js** — export formats

## Quick Start

### 1. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your MongoDB, Redis, and Gemini API credentials
```

### 3. Run services

Make sure MongoDB and Redis are running locally (or update URLs in .env).

```bash
# Start FastAPI server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Start Celery worker (separate terminal)
celery -A app.tasks.celery_app worker --loglevel=info

# Seed database
python seeds/seed_runner.py
```

### 4. API Documentation

Visit `http://localhost:8000/docs` for the interactive Swagger UI.

## Project Structure

```
app/
  core/         — Database, security, storage, cache, exceptions
  models/       — Beanie document models
  schemas/      — Pydantic request/response schemas
  api/v1/       — FastAPI routers
  services/     — Business logic
  extractors/   — Document text extraction
  ai/           — Gemini client and prompt templates
  agents/       — Generation and export pipeline agents
  tasks/        — Celery task definitions
  utils/        — Logging, validators, file helpers
seeds/          — Database seed data (themes, templates)
storage/        — File uploads and exports (gitignored)
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | /api/v1/auth/register | Register user |
| POST | /api/v1/auth/login | Login |
| POST | /api/v1/auth/refresh | Refresh tokens |
| GET | /api/v1/auth/me | Current user |
| GET | /api/v1/templates | List templates |
| GET | /api/v1/templates/{id} | Template detail |
| POST | /api/v1/generation/start | Start generation job |
| GET | /api/v1/generation/jobs | List user jobs |
| GET | /api/v1/generation/jobs/{id} | Job status |
| GET | /api/v1/presentations | List presentations |
| GET | /api/v1/presentations/{id} | Presentation detail |
| PATCH | /api/v1/presentations/{id} | Update presentation |
| DELETE | /api/v1/presentations/{id} | Delete presentation |
| POST | /api/v1/export/{id} | Start export job |
| GET | /api/v1/export/jobs/{id} | Export job status |
| GET | /api/v1/export/jobs/{id}/download | Download export |
