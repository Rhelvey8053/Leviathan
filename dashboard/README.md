# Leviathan Dashboard (Streamlit)

Local, free alternative to the Power BI dashboard. Reads the same CSV export
the pipeline already writes (`data/powerbi_export/signals.csv` and
`runs.csv`) -- no separate app state, no manual refresh. Additive only: does
not modify `main.py` or anything under `core/`.

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
in a real export (verified against a live 317-row `signals.csv` on
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
columns (added the same day as this dashboard) but are 0/317 populated right
now, so no chart is built on them yet -- flagged with `# TODO:` in
`dashboard/data.py` rather than shipping a permanently-empty chart.

## Structure

```
dashboard/
  app.py                    Overview page (entry point)
  data.py                   shared CSV loading + data contract
  pages/
    2_Signal_Breakdown.py
    3_Signal_Log.py
  requirements.txt
```
