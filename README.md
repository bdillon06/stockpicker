# Swing Finder — short-term stock setup finder

A personal web app that ranks stocks by their **short-term (swing-trade, days to
~2 weeks) upside potential** using moving averages and advanced chart analysis,
then enriches the top candidates with every piece of public information yfinance
exposes (earnings dates, analyst upgrades, news, options skew, short interest).

> ⚠️ **Not financial advice.** This is an educational research tool built on public
> data. Short-term trading is high-risk; the score ranks *technical setups*, not
> guaranteed gains.

## Quick start

**Requirements:** Python 3.10+ and an internet connection (for live prices; the
app still works off the bundled snapshot offline).

**macOS / Linux:**
```bash
bash run.sh          # creates .venv, installs deps, launches the server
# then open http://127.0.0.1:5057
```

**Windows (PowerShell):**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
# then open http://127.0.0.1:5057
```

First run seeds a price cache from the bundled `seed_snapshot.csv` so you get a
ranking immediately. Click **↻ Refresh data** to pull fresh prices for the full
universe from Yahoo Finance. Everything runs locally on your own machine — no
account, no data leaves your computer except the price requests to Yahoo.

## Deploy to Render (optional — local use still works)

The repo includes `render.yaml`, so hosting is a few clicks. Running locally with
`bash run.sh` is unchanged.

1. Put this folder in a GitHub repo:
   ```bash
   git init && git add -A && git commit -m "Swing Finder"
   git branch -M main
   git remote add origin https://github.com/<you>/swing-finder.git
   git push -u origin main
   ```
2. In Render: **New → Blueprint**, pick the repo. `render.yaml` configures the
   build (`pip install`) and start command (`gunicorn`). Click **Apply**.
3. Render builds and gives you a public URL to share.

**Free-tier notes:**
- The instance **sleeps after ~15 min idle**; the first request after sleeping
  takes ~30–60s to wake.
- The filesystem is **ephemeral** — the price cache and watchlist reset on each
  deploy/restart. The app auto re-seeds from `seed_snapshot.csv`, so it's never
  empty; hit **↻ Refresh data** for live prices. To persist them, use a paid
  instance with a disk (see the commented block in `render.yaml`).
- Yahoo throttles datacenter IPs harder than home connections, so a full Refresh
  on Render may be slower / partially rate-limited; the snapshot keeps it usable.

## How it works — two-stage pipeline

1. **Stage 1 — technical pre-screen (cheap).** A single chunked `yf.download`
   pulls 6 months of daily OHLCV for the whole universe (S&P 500, `universe.csv`).
   Indicators are computed locally and blended into a **0–100 Short-Term Upside
   Score**, with each factor percentile-ranked across the universe:
   - **Trend (heaviest weight): the 13 / 90 / 200 EMA stack** — rewards full
     bullish alignment (price > EMA13 > EMA90 > EMA200) with all three EMAs
     sloping up; being below the 200-EMA is penalised. Needs ~2 years of daily
     history, which is why the app caches a 2-year window.
   - Momentum: MACD, RSI sweet spot, recent return
   - Breakout: 20-day high break, Bollinger squeeze
   - Volume: surge vs 20-day average, OBV trend
   - Relative strength vs SPY
2. **Stage 2 — catalyst enrichment (expensive), top ~40 only.** Per-ticker
   earnings date, analyst upgrades/downgrades, recent news, options put/call
   ratio and short interest adjust the score (e.g. imminent earnings = penalty,
   upgrades / squeeze setup = boost).

Each pick comes with a **BUY / WATCH / AVOID** badge, the reasons that fired, and
**ATR-based suggested entry / stop / target**.

## Rate limiting

yfinance's bulk download works fine from most machines. Yahoo's per-symbol
endpoints are throttled from datacenter IPs (HTTP 429); the app caches
aggressively (prices 6h, enrichment 12h), fetches lazily, backs off on errors,
and degrades to the seed snapshot rather than failing.

## Layout

| File | Role |
|------|------|
| `indicators.py` | Pure technical math (EMA/SMA/MACD/RSI/Bollinger/ATR/OBV/…) |
| `scoring.py` | Pure: factor blend + percentile ranking + catalyst adjustments |
| `signals.py` | Pure: BUY/WATCH/AVOID + ATR stop/target |
| `data.py` | The only network module: yfinance bulk + enrich, with caching |
| `db.py` | SQLite cache + watchlist |
| `app.py` | Flask API + serves the frontend |
| `static/` | Vanilla-JS single-page UI |
| `generate_seed.py` | Refreshes `seed_snapshot.csv` (run occasionally) |
| `tests/` | Network-free unit tests for the analytical core |

## Tests

```bash
. .venv/bin/activate && python -m pytest tests/ -q
```

## Customising

- **Universe:** edit `universe.csv` (`symbol,name,sector`).
- **Weights:** adjust the sliders in the UI per scan, or change
  `scoring.DEFAULT_WEIGHTS`.
- **Stop/target multiples:** `signals.levels(stop_mult, target_mult)`.
