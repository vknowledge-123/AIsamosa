# SL Hunting Nifty Paper Trader

A FastAPI paper-trading app for your SL-hunting workflow:

- upload or paste a rulebook
- upload historical 1-minute candle data
- sync Dhan previous-day and intraday context
- evaluate each new 1-minute candle
- simulate Nifty option entries, stop updates, target updates, and exits
- switch between `Heuristic Mode` and `Full AI Mode`
- choose `OpenAI` or `DeepSeek` as the `Full AI` provider

## What this app does

- Runs in paper-trading mode only
- Uses Dhan live Nifty 50 feed and chart-history APIs
- Uses deterministic trading logic for `Heuristic Mode`
- Uses either OpenAI or DeepSeek for `Full AI Mode`
- Stores Dhan and AI-provider settings locally for reuse after restart

## Operating modes

- `Heuristic Mode`
  - uses only deterministic SL-hunting logic
  - same candle context should give the same result every time

- `Full AI Mode`
  - uses the selected provider and model for trading decisions
  - supports:
    - `OpenAI` via the Responses API
    - `DeepSeek` via its OpenAI-compatible Chat Completions API with JSON output
  - if the selected provider is unavailable, the app returns a safe fallback decision instead of silently switching modes

## Full AI provider choices

- `OpenAI`
  - example model: `gpt-5.4-mini`

- `DeepSeek`
  - example models:
    - `deepseek-v4-flash`
    - `deepseek-v4-pro`

## Option strike rule implemented

- For bullish trades, buy the nearest lower hundred CE.
  - Example: spot `24500` -> `24500 CE`
  - Example: spot `24498` -> `24400 CE`
- For bearish trades, buy the nearest higher hundred PE.
  - Example: spot `24500` -> `24500 PE`
  - Example: spot `24498` -> `24500 PE`

## Local setup

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Copy `.env.example` to `.env` if you want to prefill settings:

```env
OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-5.4-mini
DEEPSEEK_API_KEY=your_deepseek_api_key
DEEPSEEK_MODEL=deepseek-v4-flash
FULL_AI_PROVIDER=openai
OPERATING_MODE=full-ai
DHAN_CLIENT_ID=your_client_id
DHAN_ACCESS_TOKEN=your_access_token
DHAN_LIVE_SECURITY_ID=13
```

3. Start the app:

```bash
uvicorn app.main:app --reload
```

4. Open [http://127.0.0.1:8000](http://127.0.0.1:8000)

5. Save settings from the dashboard if you prefer not to use `.env`.

## Notes

- Dhan order placement is intentionally not enabled in this build.
- The live feed aggregates incoming ticks into 1-minute candles before strategy evaluation.
- Dashboard-saved Dhan, OpenAI, and DeepSeek secrets are stored locally in plain text in the project folder.
- For production deployment, run a single process only. This app keeps live feed and session state in memory, so do not use multiple workers.

## Google Cloud deployment

Production VM deployment files are in [deploy/gcp/README.md](deploy/gcp/README.md).

Recommended baseline:

- region: `asia-south1`
- zone: `asia-south1-a`
- machine type: `e2-standard-2`
- static regional external IP
- `systemd` + `nginx`
- run with `uvicorn app.main:app` and no `--reload`

## Source notes

- OpenAI integration points are based on official OpenAI docs for the Responses API and structured outputs.
- DeepSeek integration points are based on official DeepSeek docs showing OpenAI-compatible API usage and JSON Output mode.
- Dhan SDK and market feed integration points are based on current Dhan docs for `DhanHQ-py`, live market feed, and chart APIs.
