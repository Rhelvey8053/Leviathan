# Leviathan — Progress Log

---

## Goal 1b — pytest suite for logger and scanner

**Branch:** `tests-1b`
**Result:** 81 tests, 0 failures, exit 0

### Coverage

**logger.py — `resolve_outcomes`**
- All 5 payoff cases verified to the cent:
  - YES at 0.30, resolves YES → +0.70 ✓
  - YES at 0.30, resolves NO  → −0.30 ✓
  - NO  at 0.30, resolves NO  → +0.30 ✓
  - NO  at 0.30, resolves YES → −0.70 ✓
  - blank direction           →  0.00 ✓
- Confirmed reads `market_price`, not `edge` (separate test with `edge=0.25, market_price=0.30`; expects +0.70 not +0.75)
- Skips already-resolved rows — only unresolved rows trigger API calls
- Leaves rows untouched when Kalshi API returns unsettled result

**logger.py — `get_stats`**
- `win_rate` is `None` when `resolved == 0`
- Computes correctly with 2 WIN + 1 LOSS (66.7%)
- `log_signal` writes blank `outcome`/`result` → `resolved` count stays 0 until `resolve_outcomes` runs
- Round-trip test confirms blank-outcome convention is consistent between `log_signal`, `resolve_outcomes`, and `get_stats`

**scanner.py — `filter_markets`**
- Price bounds: mid < 0.05 or > 0.95 dropped; boundaries 0.05 and 0.95 kept
- Volume: below per-bucket minimum dropped; above global max dropped
- Close-time: no `close_time` field dropped; `expiration_time` fallback accepted; beyond 180d dropped
- Efficient-market keywords: CPI/Federal Reserve titles dropped

**scanner.py — `classify_time_horizon`**
- 15 boundary cases covering INTRADAY / WEEKLY / MONTHLY / QUARTERLY / LONG

**scanner.py — `score_market` flag logic**
- `base_rate is None` and `mid_price` set → `flag=True` (dominant real-world trigger)
- Edge > 0.08 → `flag=True`
- Edge small but drift > 5% → `flag=True`
- `mid_price is None` (no bid/ask) → `flag=False`

**Regression: per-$1 payoff convention**
- 4 × 7 = 28 parametrized cases covering YES-win, YES-loss, NO-win, NO-loss at prices 0.10–0.90
- Will break loudly if anyone changes notional size or flips the formula

### Bugs found during testing

None. All logic was already correct after Goal 1 remediation. The tests confirm the P&L fix is wired correctly end-to-end.

### Top 3 recommended next steps

1. **Add `test_whales.py`** — `detect_whale_activity` and `scan_all_markets` are completely untested. The whale flag feeds the second signal path in `main.py` and is never covered by any automated check.

2. **Add `test_report.py`** — `compile_report` and `send_report` have no tests. Email sending should be mocked; test that signal cards render correctly and that `[SECOND PASS — LOW CONVICTION]` is stamped on second-pass signals.

3. **Integration smoke test** — a single end-to-end test that mocks all external calls (Kalshi, Polymarket, Claude CLI) and runs `main.main()` to verify the pipeline completes without exception and logs at least one run row to the DB. This would catch wiring regressions that unit tests on individual modules can't see.
