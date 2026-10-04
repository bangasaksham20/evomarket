# EvoMarket — Autonomous Closed-Loop Genetic Ad Agency

Treats digital advertising as a genetic reinforcement loop: cultural recon
(Tavily), hypothesis generation (ZooWork sandbox), a multi-generational ad
exchange simulation with 50% pruning + crossover, and a dark-mode campaign
dashboard.

## Run locally

```bash
cd ~/workspace/evomarket
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8080
```

Then open http://localhost:8080

## Deploy to Vercel

1. Push this folder to a Git repo (`.env` is gitignored — never commit keys).
2. In Vercel: **New Project → Import** the repo. Vercel auto-detects
   `api/index.py` + root `requirements.txt`.
3. Add environment variables in **Project → Settings → Environment Variables**:
   - `ZOOWORK_API_KEY`, `TAVILY_API_KEY`
   - `TAVILY_TIMEOUT_MS` = `6000` (recommended on Hobby's 10s function limit;
     the recon degrades gracefully to built-in trends on timeout)
   - `ZOOWORK_LIVE` = `1` (optional; enables the live ZooWork agent call)
4. Deploy. The dashboard is served at `/`, the API at `/api/optimize`.

`vercel.json` rewrites `/` to the static dashboard and gives the API function
`maxDuration: 60` (applies on Pro; Hobby caps at 10s).

## API

- `POST /api/optimize` — body: `{"product_name", "description", "category",
  "image_url" (optional), "retail_price"}` → full evolution telemetry.
- `GET /api/health` — integration status for Tavily / ZooWork (no keys leaked).

## Notes

- The exchange simulator is an in-memory model (clearly labeled in the UI as
  simulated). If the Tavily or ZooWork SDKs/APIs are unreachable, the engine
  degrades gracefully to built-in heuristic recon + hypothesis generation and
  reports `fallback` in the response.
- Secrets live in `.env` (gitignored by convention). Rotate any key that has
  been pasted into a chat transcript.
