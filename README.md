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
ranking immediately. **Every scan refreshes prices automatically** when the cache
is older than `data.PRICE_TTL` (6h), so a scan always reflects the latest close
without you having to remember to refresh. The first scan of the day therefore
takes ~40-60s while it pulls the universe; later scans that day are instant.
**↻ Refresh data** is still there to force a pull. Everything runs locally on your
own machine — no account, no data leaves your computer except requests to Yahoo.

The scan header always shows **prices as of `<date>`**, and warns when that date
is more than a few days old — a throttled refresh otherwise looks exactly like a
fresh scan, leaving you ranking a stale snapshot.

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
  takes ~30–60s to wake, and the first scan after that spends another ~40–60s
  refreshing the universe before it returns.
- The filesystem is **ephemeral**, and the whole SQLite file dies with it on every
  sleep/deploy/restart. The four tables do **not** degrade equally:
  - `price_cache` / `enrich_cache` — self-healing. The next scan refetches, and
    the app re-seeds from `seed_snapshot.csv` meanwhile, so a scan is never empty.
  - **`watchlist` — lost.** Starred tickers silently disappear.
  - **`rank_history` — lost**, so the **Δ 1d column can never work**: with no prior
    day it reports `NEW` forever.

  The first two recover by themselves; the last two are user state and cannot.
  **If you want the watchlist and Δ 1d to survive, you need the paid instance with
  a disk** (uncomment the block in `render.yaml`) — that, not CPU or memory, is
  what the free plan actually costs you. A scan peaks near 200MB RSS against the
  512MB free limit, so memory is not the constraint.
- Yahoo throttles datacenter IPs harder than home connections, so a refresh on
  Render may be slower or partially rate-limited. When that happens the scan
  header shows **prices as of `<date>`** with a warning instead of quietly serving
  a stale ranking, and any ticker whose catalysts failed is named. Keep
  `seed_snapshot.csv` current (`python generate_seed.py`) — on free tier it is the
  cold-start state of the app, so a stale snapshot means a stale first scan.

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
2. **Stage 2 — the EMA gate (absolute, not relative).** Ranking alone is
   percentile-based, so it *always* yields a "top 30" no matter how bad the tape
   — the best name in a falling market still scores ~100. `scoring.DEFAULT_FILTERS`
   therefore gates on the setup itself before anything is shown: price above the
   200-EMA, EMA13 above EMA90, not overextended above the 13-EMA (in ATRs), plus
   a price and dollar-volume liquidity floor. Names that fail never reach the
   results, and the scan reports `scanned → qualified → shown`. **If nothing
   qualifies, the scan says so rather than padding the list** — some days the
   market isn't offering a setup.
3. **Stage 3 — catalyst enrichment (expensive), top ~40 only.** Per-ticker
   earnings date, analyst upgrades/downgrades, recent news, options put/call
   ratio and short interest adjust the score (e.g. imminent earnings = penalty,
   upgrades / squeeze setup = boost). Because a catalyst can move a score ~14
   points, enrichment **repeats until the displayed cut is stable** — otherwise a
   penalised name sinks and pulls an un-enriched one into view, leaving that row
   on a bare technical score while its neighbours carry adjusted ones. If Yahoo
   throttles a per-ticker call, the affected tickers are listed in the scan's
   `unenriched` field and named in the UI rather than silently skewing a row.

Every stock in a scan is priced on the **same session** (`as_of`); a name that
hasn't printed the latest bar is dropped rather than ranked against fresher
peers, since one stale price corrupts every percentile it takes part in.

Each pick comes with a **BUY / WATCH / AVOID** badge, the reasons that fired, and
**ATR-based suggested entry / stop / target**. The badge is driven by the
absolute chart (EMA stack, MACD, RSI, volume) and only *confirmed* by the score —
scoring on percentile alone used to promote the least-bad stock in a weak market
to BUY.

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
| `backtest.py` | Walk-forward backtest of the scan (no network; reads the cache) |
| `tests/` | Network-free unit tests for the analytical core |

## Backtest — does the score actually pick winners?

The app can only tell you a chart *matches the pattern*. Whether the pattern pays
is a separate question, and `backtest.py` is the only thing here that addresses
it. It replays the scanner over the cached history, taking the picks it would
genuinely have made on each date:

```bash
python backtest.py                      # top 5 BUY signals, 10-bar hold
python backtest.py --top 10 --hold 5 --slippage-bps 10
```

It calls the **live** `compute_all` / `rank_universe` / `passes_filters` /
`signals.evaluate` rather than re-implementing the strategy, so it measures what
the app actually does. Indicators are computed strictly from bars up to the
decision date, entries are at the *next* open, and a bar that spans both stop and
target resolves to the **stop** (daily bars hide intraday order, and the
optimistic branch invents profit). `tests/test_backtest.py` guards all three.

It reports two controls alongside the strategy, and they matter more than the
headline number:

- **every gated name** — the average of all names that passed the EMA filter. If
  the picks don't beat this, the *score* adds nothing and the gate is doing the
  work.
- **SPY buy & hold** — if the picks don't beat this, the strategy was just the
  market with extra steps.

**A positive result is not evidence this makes money.** The biases below inflate
returns and cannot be removed with the cached data — the run prints them every
time on purpose:

- **Survivorship (largest).** `universe.csv` is *today's* S&P 500; names that
  dropped out during the window are missing, and today's members are there
  because they did well enough to stay.
- **~1.2 years, one regime.** 2 years of bars minus the 200-EMA warm-up, in a
  bull market — exactly where a long-only, above-200-EMA strategy flatters itself.
- **No catalysts.** Earnings/news exist only as a current snapshot, never
  point-in-time, so `apply_catalysts` is out of scope; this is the technical
  score alone.
- **Idealised fills.** Real gaps are worse than `--slippage-bps` models.

## Tests

```bash
. .venv/bin/activate && python -m pytest tests/ -q
```

## Customising

- **Universe:** edit `universe.csv` (`symbol,name,sector`).
- **Weights:** adjust the sliders in the UI per scan, or change
  `scoring.DEFAULT_WEIGHTS`. The UI's `DEFAULT_W` in `static/app.js` mirrors
  these — **keep the two in sync**, or the scan and the detail drawer will score
  the same stock differently.
- **EMA filters:** `scoring.DEFAULT_FILTERS`, or per scan via the API:
  ```bash
  curl -X POST localhost:5057/api/scan -H 'Content-Type: application/json' -d '{
    "filters": {"require_full_stack": true, "require_slow_rising": true,
                "max_ext_from_fast": 2.5, "min_dollar_volume": 20000000},
    "hide_avoid": true}'
  ```
  `require_full_stack` demands the textbook price > EMA13 > EMA90 > EMA200;
  `require_slow_rising` additionally demands a rising 200-EMA; `max_ext_from_fast`
  caps how far above the 13-EMA (in ATRs) a name may be before it counts as a
  chase. Tighten these to get fewer, higher-conviction names.
- **Freshness:** `data.PRICE_TTL` (default 6h) controls how old the cache may be
  before a scan refetches. Lower it to refresh more eagerly, at the cost of more
  Yahoo requests.
- **Stop/target multiples:** `signals.levels(stop_mult, target_mult)`.
