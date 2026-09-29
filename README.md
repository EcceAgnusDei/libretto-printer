# libretto-printer

Application web Next.js + FastAPI : ingestion de partitions PDF, extraction libretto via Gemini, export PDF.

## Stack

- `web/` — Next.js (App Router)
- `api/` — FastAPI
- Extraction prévue : Gemini 3.5 Flash Lite
- Entrée MVP : **PDF uniquement** (côté API)

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

## Couche 2 — Ingestion (faite)

Flux au clic **Extraire le libretto** :

1. Vérification PDF **locale** (pdf.js) — taille / pages / lisibilité.
2. `GET /health` (exige `gemini_configured`).
3. `POST /ingest/sessions`
4. `POST /ingest/sessions/{id}/pdf` — validation **serveur** :
   - taille max (`max_upload_bytes`, lecture par chunks)
   - en-tête `%PDF`
   - PDF lisible (PyMuPDF), non protégé par mot de passe
   - 1…`max_pages` pages
5. `POST /ingest/sessions/{id}/pdf/render` en boucle (lots de `ingest_batch_size`) jusqu’à `done=true` — PNG normalisés (DPI 150, max 2048 px).
6. `POST /extract/sessions/{id}/start` — exige PDF + pages rendues.
7. Rollback : `DELETE /ingest/sessions/{id}` si échec avant le start.

Session sur disque : `data/sessions/{id}/source.pdf`, `pages/page_XXXX.png`, `meta.json`.
