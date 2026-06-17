# Leviathan — Test Suite

## Running

```bash
python -m pytest -q
```

From the repo root. All tests are **offline-only** — no Kalshi, Polymarket,
Manifold, PredictIt, OddsAPI, Metaculus, Claude CLI, or email calls are made.
Network and subprocess boundaries are mocked with `unittest.mock.patch`.

**Current count: 286 tests, 0 failures.**

---

## Coverage

| Module | File | What's tested |
|---|---|---|
| `logger` | `tests/test_logger.py` | `resolve_outcomes` payoff math (all direction×outcome cases), reads `market_price` not `edge`, skips already-resolved rows, schema migration idempotency, fill matching (`from_signal`, `direction_aligned`), real-fill P&L net of fees, `get_stats` / `get_stats_real` separation, per-$1 payoff regression pins; `flag_path` and `watchlist_signal` persistence; `get_stats_by_flag_path` (win rate by path, resolved-only, excludes real fills) |
| `scanner` | `tests/test_scanner.py` | `filter_markets` price-bounds, volume floors per bucket, close-time window, `expiration_time` fallback, keyword gate; `classify_time_horizon` boundary cases; `score_market` flag logic (all three modes), drift thresholds, spread signal, `sig_*` independence; `score_markets` sort order; `tag_watchlist_overlap` tagging and priority sort; watchlist force-flag (`flag_path=WATCHLIST`) |
| `whales` | `tests/test_whales.py` | `detect_whale_activity` size threshold, direction detection, `scan_all_markets` aggregation |
| `research_probe` | `tests/test_research_probe.py` | Stratified sampling (volume tiers, filter_pass annotation, target_n cap); `log_probe` insertion; probe rows excluded from paper/real_fill stats; forward scoring (WIN/LOSS/pending); `get_stats_probe` PENDING verdict and high-divergence subset; `run_probe` max-cap enforcement |
| `smart_money` | `tests/test_smart_money.py` | `_is_binary_position` (YES/NO pass, sports outcomes reject, team names reject); `_is_sports_title` (game lines and competition bets detected, political/macro excluded); `_normalize` (stop words, case, punctuation, min length); `_match_to_kalshi` (2-keyword gate, threshold filtering, sort order, sports at 0.40, exact-title top-rank, single-keyword rejection); `_entity_contradiction` (US state mismatch, city mismatch, org-group mismatch, legitimate matches preserved); `_group_signals_by_ticker` (aggregation, direction voting, MIXED consensus, trader dedup) |

---

## SQLite safety

Logger tests use pytest's `tmp_path` fixture to create a throwaway
`test.db` per test. `logger.DB_PATH` is monkeypatched before any DB
operations run. The real `leviathan.db` is never touched.

---

## Adding tests

- Keep all tests offline — mock every network call with `patch`.
- For logger tests, always use the `tmp_db` fixture so `logger.DB_PATH`
  points at the temp file.
- Run `python -m pytest -q` before committing.
