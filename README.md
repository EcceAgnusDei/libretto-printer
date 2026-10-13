# libretto-printer

Application web Next.js + FastAPI : ingestion de partitions PDF, extraction libretto via Gemini, export PDF.

## Stack

- `web/` — Next.js (App Router)
- `api/` — FastAPI
- Extraction : **Gemini** (modèle configurable, défaut `gemini-3.5-flash-lite`)
- Entrée MVP : **PDF uniquement** (côté API)
- Sortie extraction : **un seul fichier** `libretto.md` (Markdown `#` / `##` / `###`)

## Prerequisites

- Node.js 20+
- Python 3.12+

## Setup

Copy the env template once at the **repo root**:

```powershell
copy .env.example .env
```

Edit `.env` and set `GEMINI_API_KEY` (optionnel : `GEMINI_MODEL`).

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
| `GEMINI_MODEL`        | API     | `gemini-3.5-flash-lite` |
| `CORS_ORIGINS`        | API     | `http://localhost:3000` |
| `NEXT_PUBLIC_API_URL` | Web     | `http://localhost:8000` |

Limites publiques : http://localhost:8000/config

## Couche 2 — Ingestion

1. Vérification PDF locale (pdf.js)
2. `POST /ingest/sessions` → upload PDF → render PNG par lots
3. Session : `data/sessions/{id}/source.pdf`, `pages/page_XXXX.png`, `meta.json`

## Quotas & messages client

- **Rate limit / affluence (RPM·TPM, 429)** → pause + `wait.reason=rate_limit` → UI : *forte affluence : extraction ralentie* (+ compte à rebours)
- **Quota journalier serveur** : `gemini_max_batches_per_day` (défaut 250 **lots**/jour). Compteur : `data/gemini_usage.json`
- Si lots nécessaires > lots restants → confirmation UI avant extraction
- Quota Gemini API daily / rate limit → messages UI dédiés
- Pace préventive silencieuse : `gemini_min_interval_seconds` (défaut 5 s)
- `GET /quota?page_count=N` pour vérifier le reste

## Couche 3 — Extraction (passe unique)

Workflow :

1. `POST /extract/sessions/{id}/start` — préconditions + job + worker fond
2. Pour chaque lot de **5 pages** :
   - lit `libretto.md`
   - envoie à Gemini : images du lot + **contexte = depuis le dernier `##` jusqu’à la fin**
   - Gemini renvoie la **section Markdown mise à jour** (sans numéros de page)
   - remplace cette section dans `libretto.md`
3. Statuts job : `running` → `done` | `error` (plus de `started`)
4. Fichier unique : `data/sessions/{id}/libretto.md`

Endpoints :

- `POST /extract/sessions/{id}/start`
- `GET /extract/sessions/{id}/status`
- `GET /extract/sessions/{id}/libretto`

### Tester

```powershell
# Session déjà rendue (toutes les pages PNG)
Invoke-RestMethod -Method POST -Uri "http://localhost:8000/extract/sessions/SESSION_ID/start"
# Suivre
Invoke-RestMethod -Uri "http://localhost:8000/extract/sessions/SESSION_ID/status"
# Lire le markdown
Invoke-RestMethod -Uri "http://localhost:8000/extract/sessions/SESSION_ID/libretto"
```

Via l’UI : PDF → Extraire → progression des lots → aperçu `libretto.md` à la fin.

Pour relancer : supprimer `extract_job.json` + `libretto.md` (ou toute la session) si le job est `done`/`error` ; si `running`, attendre la fin ou redémarrer l’API.
