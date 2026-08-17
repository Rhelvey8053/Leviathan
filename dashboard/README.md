# Leviathan Dashboard (Streamlit)

Local, free alternative to the Power BI dashboard. Reads the same CSV export
the pipeline already writes (`data/powerbi_export/signals.csv` and
`runs.csv`) -- no separate app state, no manual refresh.

`signals.csv` holds only real bets (direction YES/NO) as of the 2026-08-16
export cleanup -- PASS decisions (scanner looked, no signal, ~85% of rows
before the cleanup) now live in a separate `scan_log.csv`, which this
dashboard does not read.

## Run it (Windows, Command Prompt)

```
cd C:\path\to\Leviathan
python -m venv dashboard\.venv
dashboard\.venv\Scripts\activate.bat
pip install -r dashboard\requirements.txt
streamlit run dashboard\app.py
```

Streamlit opens the app in your browser (default `http://localhost:8501`).
The sidebar lists all three pages: Overview, Signal Breakdown, Signal Log.

## Where the CSV path comes from

Defaults to `<repo root>/data/powerbi_export/` (same folder `main.py`
already exports to). Override with an env var if you want to point at a
different export:

```
set LEVIATHAN_DASHBOARD_DATA_DIR=C:\some\other\path
streamlit run dashboard\app.py
```

No secrets or machine-specific paths are hardcoded anywhere in `dashboard/`.

## Data contract

See the comment block at the top of `dashboard/data.py` -- it lists every
column the dashboard reads, its real dtype, and how populated it actually is
in a real export (verified against a live 46-row `signals.csv` on
2026-08-16, not assumed). Two things from the original spec don't map to
real columns and are called out with `# TODO:` in the code instead of being
faked:

- **"Markets scanned (Kalshi + Polymarket)"** -- `runs.csv` only has a single
  combined `markets_scanned` count (Kalshi markets scanned that run). There
  is no separate Polymarket-scanned metric anywhere in the pipeline's output,
  so the KPI is labeled "Markets Scanned (Kalshi)".
- **"Breakdown by source (Kalshi vs Polymarket)"** -- the real `source`
  column is `paper` / `real_fill` / `research_probe` (signal population
  type), not a detection platform. The dashboard uses `flag_path` instead
  (`DRIFT` / `HEURISTIC` / `RESOLVE_FIRST` / `CROSS_MARKET`) as the nearest
  real proxy -- `CROSS_MARKET` is the Polymarket-corroborated path in
  `core/scorer.py`, it just has 0 rows in the current snapshot.

`volume`, `open_interest`, `resolved_at`, and `days_to_resolution` are real
columns (added the same day as this dashboard) but are 0/46 populated right
now, so no chart is built on them yet -- flagged with `# TODO:` in
`dashboard/data.py` rather than shipping a permanently-empty chart.

`pre_scoring_era` (new column, same cleanup) flags real bets logged before
`leviathan_score` existed as a tracked field (2026-04-13 to 2026-07-27) --
27 of the 46 real bets. Can't be backfilled; the market snapshot from that
moment is gone. Surfaced in the Signal Log's default columns and as a count
on Signal Breakdown so it reads as "old data, expected gap" rather than a
data-quality bug.

## Interactivity & visual style

`dashboard/theme.py` holds a shared color palette, Plotly template, and CSS
so all three pages read as one product: styled KPI cards, a consistent
win/loss color pairing everywhere, and a `small-n-badge` shown any time a
chart is built on a thin sample rather than letting a nicer-looking chart
imply more confidence than the data supports.

Signal Breakdown's "By Detection Path" chart is click-to-filter: click a
bar and every chart below it (edge/confidence distributions, category
breakdown, CLV drift, the resolved-bets outcome strips) filters to that
flag_path. Click the same bar again or use "Clear selection" to reset.
Uses Streamlit's native `st.plotly_chart(..., on_select="rerun")` --
confirmed present in the pinned Streamlit version's signature, and the
empty-selection code path is exercised by the bare-mode smoke test, but
the click interaction itself needs a real browser to fully verify.

The resolved-bets section (Signal Breakdown) shows individual outcomes as
strip plots rather than a binned reliability curve -- 13-16 resolved bets
is too few for binning to mean anything without manufacturing false
precision.

## Structure

```
dashboard/
  app.py                    Overview page (entry point)
  data.py                   shared CSV loading + data contract
  theme.py                  shared color palette, Plotly template, CSS
  pages/
    2_Signal_Breakdown.py
    3_Signal_Log.py
  requirements.txt
```
