# libretto-printer

Application web Next.js + FastAPI : ingestion de partitions (PDF pour l'instant), extraction libretto via Gemini, export PDF.

## Stack

- `web/` — Next.js (App Router)
- `api/` — FastAPI
- Extraction prévue : Gemini 3.5 Flash Lite

## Prerequisites

- Node.js 20+
- Python 3.12+

## Setup

Copy the env template once at the **repo root**:

```powershell
copy .env.example .env
```

Edit `.env` and set `GEMINI_API_KEY`. This is the only env file used by both API and web.

### API (FastAPI)

```powershell
cd api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

API health: http://localhost:8000/health

### Web (Next.js)

```powershell
cd web
npm install
npm run dev
```

App: http://localhost:3000

## Env (root `.env` only)

| Variable              | Used by | Default                 |
| --------------------- | ------- | ----------------------- |
| `GEMINI_API_KEY`      | API     | (empty)                 |
| `CORS_ORIGINS`        | API     | `http://localhost:3000` |
| `NEXT_PUBLIC_API_URL` | Web     | `http://localhost:8000` |

Limites publiques (taille, pages, batch) : http://localhost:8000/config — définies dans `api/app/config.py`, chargées par le front.

## Couche 3 — Déclenchement (étape actuelle)

1. Vérification PDF **locale** (pdf.js) — aucun upload.
2. Clic **Extraire le libretto** :
   - `GET /health` (exige `gemini_configured`)
   - `POST /ingest/sessions`
   - `POST /ingest/sessions/{id}/pdf`
   - `POST /extract/sessions/{id}/start` → job `status: started`
   - En cas d'échec après création de session : `DELETE /ingest/sessions/{id}` (rollback)

`session_id` doit être un uuid hex 32 chars ; `start` exige que `source.pdf` existe sur disque.

Pass 1 / Pass 2 Gemini ne tournent pas encore. Suivi : `GET /extract/sessions/{id}/status`.
