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

| Variable | Used by | Default |
| --- | --- | --- |
| `GEMINI_API_KEY` | API | (empty) |
| `GEMINI_MODEL` | API | `gemini-3.5-flash-lite` |
| `DEVLOG` | API | `false` |
| `CORS_ORIGINS` | API | `http://localhost:3000` |
| `NEXT_PUBLIC_API_URL` | Web | `http://localhost:8000` |
| `GEMINI_MAX_BATCHES_PER_DAY` | API | `100` |
| `ALLOW_UNLIMITED_GEMINI_QUOTA` | API | `false` |
| `FORCE_HTTPS` | API | `false` |
| `TRUST_PROXY_HEADERS` | API | `false` |
| `SESSION_TTL_HOURS` | API | `72` |
| `RATE_LIMIT_WINDOW_SECONDS` | API | `60` |
| `RATE_LIMIT_UPLOAD_MAX` | API | `20` |
| `RATE_LIMIT_EXTRACT_START_MAX` | API | `30` |

Limites publiques : http://localhost:8000/config

### Prod (sans auth)

- Mettre `FORCE_HTTPS=true` derrière un reverse proxy TLS, et `TRUST_PROXY_HEADERS=true` si le proxy envoie `X-Forwarded-Proto` / `X-Forwarded-For`
- Garder `CORS_ORIGINS` sur l’origine front exacte (pas `*`)
- Ne pas activer `ALLOW_UNLIMITED_GEMINI_QUOTA` (un plafond `≤ 0` sans ce flag bloque l’extraction)
- `DEVLOG=false` en prod

## Couche 2 — Ingestion

1. Vérification PDF locale (pdf.js)
2. `POST /ingest/sessions` → upload PDF → render PNG par lots
3. Session : `data/sessions/{id}/source.pdf`, `pages/page_XXXX.png`, `meta.json`
4. Purge automatique des sessions inactives (`SESSION_TTL_HOURS`)

## Quotas & messages client

- **Rate limit HTTP (IP)** sur création/upload et `extract/start` → `429` *trop de requêtes*
- **Rate limit / affluence Gemini (RPM·TPM, 429)** → pause + `wait.reason=rate_limit` → UI : *forte affluence : extraction ralentie* (+ compte à rebours)
- **Quota journalier serveur** : `gemini_max_batches_per_day` (défaut **100** lots/jour). Compteur : `data/gemini_usage.json` (incrémenté **après** un appel Gemini réussi)
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
