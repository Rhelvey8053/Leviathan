# Leviathan — Test Suite

## Running

```
python -m pytest -q
```

From the repo root. All tests are **offline-only** — no Kalshi, Polymarket,
Manifold, PredictIt, OddsAPI, Metaculus, claude CLI, or email calls are made.
Network and subprocess boundaries are mocked with `unittest.mock.patch`.

## Coverage

| Module     | File                  | What's tested |
|------------|-----------------------|---------------|
| `logger`   | `tests/test_logger.py` | `resolve_outcomes` payoff math (all 5 direction×outcome cases), reads `market_price` not `edge`, skips already-resolved rows, handles unsettled markets, `get_stats` win-rate and resolved-count, `log_signal` blank-outcome convention, regression pins on per-$1 payoff formula |
| `scanner`  | `tests/test_scanner.py` | `filter_markets` price-bounds gate, volume floors per bucket, close-time window, `expiration_time` fallback, keyword gate; `classify_time_horizon` boundary conditions; `score_market` flag logic (no-base-rate, edge-threshold, drift, no-mid-price), spread signal; `score_markets` sort order |

## SQLite safety

Logger tests use `pytest`'s `tmp_path` fixture to create a throwaway
`test.db` per test. `logger.DB_PATH` is monkeypatched before any DB
operations run. The real `leviathan.db` is never touched.

## Adding tests

- Keep all tests offline — mock every network call with `patch`.
- For logger tests, always use the `tmp_db` fixture so `logger.DB_PATH`
  points at the temp file.
- Run `python -m pytest -q` before committing.
