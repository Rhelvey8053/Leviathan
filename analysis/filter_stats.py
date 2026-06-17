"""
Filter diagnostic — shows how many markets pass each filter stage.

Fetches the current Kalshi snapshot and runs the pipeline dry up to step 3
(filter + dedup + score), reporting counts per stage and flag-path breakdown.
Does NOT call Claude or send any email.

Usage:
    python analysis/filter_stats.py              # live Kalshi API fetch
    python analysis/filter_stats.py --snapshot   # use latest saved snapshot (fast)
"""

import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv()

import kalshi
import scanner

W = 68


def _rule(c="-"):
    return c * W


def _load_snapshot() -> list[dict]:
    """Load latest saved snapshot from data/snapshots/."""
    snap_dir = os.path.join(ROOT, "data", "snapshots")
    files = sorted(glob.glob(os.path.join(snap_dir, "markets_*.json")))
    if not files:
        raise FileNotFoundError(f"No snapshot files found in {snap_dir}")
    latest = files[-1]
    print(f"  Using snapshot: {os.path.basename(latest)}")
    data = json.load(open(latest, encoding="utf-8"))
    if isinstance(data, dict) and "markets" in data:
        return data["markets"]
    return data


def main(use_snapshot: bool = False):
    cfg_path = os.path.join(ROOT, "config.json")
    with open(cfg_path, encoding="utf-8") as f:
        config = json.load(f)

    print()
    print(_rule("="))
    print("LEVIATHAN — FILTER DIAGNOSTICS")
    print(_rule("="))
    print()

    if use_snapshot:
        # Fast path — read from saved snapshot file
        print("Loading snapshot...")
        try:
            all_markets = _load_snapshot()
        except Exception as e:
            print(f"  Snapshot load failed: {e}")
            return
    else:
        # Auth
        try:
            kalshi.authenticate(config)
        except Exception as e:
            print(f"Auth failed: {e}")
            return

        # Fetch events + markets
        print("Fetching markets from Kalshi...")
        all_markets = []
        try:
            events = kalshi.fetch_events(config)
            seen   = set()
            for event in events:
                ev_ticker = event.get("event_ticker") or event.get("ticker", "")
                if not ev_ticker or "KXMVE" in ev_ticker:
                    continue
                try:
                    for m in kalshi.fetch_event_markets(config, ev_ticker):
                        t = m.get("ticker")
                        if t and t not in seen:
                            seen.add(t)
                            all_markets.append(m)
                except Exception:
                    pass
        except Exception as e:
            print(f"  Events fetch failed: {e}")
            return

    print(f"  Raw markets fetched: {len(all_markets)}")
    print()

    # Run each filter stage separately
    mkt_cfg = config.get("markets", {})

    # Stage 1: volume + price + keyword + time window (all in filter_markets)
    filtered = scanner.filter_markets(all_markets, config)
    print(f"  After filter_markets:  {len(filtered):>5}  (from {len(all_markets)})")

    # Count per drop reason (approximate, single-pass)
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    max_vol   = mkt_cfg.get("max_volume_filter", 75000)
    min_vol   = mkt_cfg.get("min_volume", 500)
    min_price = mkt_cfg.get("min_market_price", 0.05)
    max_price = mkt_cfg.get("max_market_price", 0.95)
    min_oi    = mkt_cfg.get("min_open_interest", 0)
    max_days  = mkt_cfg.get("max_days_to_close", 180)
    keywords  = [k.lower() for k in mkt_cfg.get("efficient_market_keywords", [])]

    dropped_vol_high   = 0
    dropped_vol_low    = 0
    dropped_price      = 0
    dropped_keyword    = 0
    dropped_time       = 0
    dropped_oi         = 0
    keyword_hits: dict[str, int] = {}

    for m in all_markets:
        vol = float(m.get("volume_fp") or m.get("volume") or 0)
        if vol > max_vol:
            dropped_vol_high += 1
            continue
        oi = float(m.get("open_interest_fp") or m.get("open_interest") or 0)
        if min_oi > 0 and oi < min_oi:
            dropped_oi += 1
            continue
        yes_bid = float(m.get("yes_bid_dollars") or m.get("yes_bid") or 0)
        yes_ask = float(m.get("yes_ask_dollars") or m.get("yes_ask") or 0)
        if yes_bid > 0 and yes_ask > 0:
            mid = (yes_bid + yes_ask) / 2
        else:
            last_p = float(m.get("last_price_dollars") or 0)
            mid = last_p if last_p > 0 else None
        if mid is not None and not (min_price <= mid <= max_price):
            dropped_price += 1
            continue
        title = (m.get("title") or "").lower()
        hit_kw = next((kw for kw in keywords if kw in title), None)
        if hit_kw:
            dropped_keyword += 1
            keyword_hits[hit_kw] = keyword_hits.get(hit_kw, 0) + 1
            continue
        close_str = m.get("close_time") or m.get("expiration_time")
        if not close_str:
            dropped_time += 1
            continue
        try:
            close = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
            if (close - now).days > max_days or close < now:
                dropped_time += 1
        except Exception:
            dropped_time += 1

    print(f"    Dropped (vol > {max_vol:,}):  {dropped_vol_high:>5}")
    print(f"    Dropped (OI < {min_oi}):        {dropped_oi:>5}")
    print(f"    Dropped (price OOB):      {dropped_price:>5}")
    print(f"    Dropped (keyword match):  {dropped_keyword:>5}")
    print(f"    Dropped (time window):    {dropped_time:>5}")

    if keyword_hits:
        top_kws = sorted(keyword_hits.items(), key=lambda x: -x[1])[:10]
        print()
        print("    Top efficient-market keywords (markets dropped):")
        for kw, cnt in top_kws:
            print(f"      {cnt:>4}  {kw}")
    print()

    # Stage 2: dedup
    if mkt_cfg.get("dedup_by_event", False):
        before = len(filtered)
        deduped = scanner.dedup_by_event(filtered)
        print(f"  After dedup_by_event:   {len(deduped):>5}  ({before - len(deduped)} duplicates removed)")
    else:
        deduped = filtered
        print(f"  dedup_by_event: OFF")

    # Stage 3: score + flag
    scored = scanner.score_markets(deduped, config)
    flagged = [m for m in scored if m.get("flag")]
    print(f"  After score+flag:       {len(flagged):>5}  ({len(deduped) - len(flagged)} unflagged)")

    # Flag path breakdown
    path_counts: dict[str, int] = {}
    for m in flagged:
        p = m.get("flag_path") or "UNKNOWN"
        path_counts[p] = path_counts.get(p, 0) + 1

    print()
    print("  Flag path breakdown:")
    for path, count in sorted(path_counts.items(), key=lambda x: -x[1]):
        print(f"    {path:<20}  {count:>4}")

    # Sig_ field counts across all scored markets (mode-independent signals)
    n_sig_edge    = sum(1 for m in scored if m.get("sig_edge"))
    n_sig_drift   = sum(1 for m in scored if m.get("sig_drift"))
    n_sig_br_none = sum(1 for m in scored if m.get("sig_br_none"))
    n_sig_both    = sum(1 for m in scored if m.get("sig_edge") and m.get("sig_drift"))
    print()
    print("  Mode-independent signal presence (all scored markets):")
    print(f"    sig_edge:     {n_sig_edge:>4}  ({n_sig_edge/len(scored)*100:.0f}%)" if scored else "    sig_edge: 0")
    print(f"    sig_drift:    {n_sig_drift:>4}  ({n_sig_drift/len(scored)*100:.0f}%)" if scored else "    sig_drift: 0")
    print(f"    sig_br_none:  {n_sig_br_none:>4}  ({n_sig_br_none/len(scored)*100:.0f}%)" if scored else "    sig_br_none: 0")
    print(f"    both edge+drift: {n_sig_both:>3}")

    # Prompt-time signals: volume spike and price jump (computed same way as scorer.py)
    n_vol_spike  = 0
    n_price_jump = 0
    for m in scored:
        vol_total = float(m.get("volume_fp") or m.get("volume") or 0)
        vol_24h   = float(m.get("volume_24h_fp") or 0)
        if vol_total > 0 and vol_24h > 0 and (vol_24h / vol_total) >= 0.20:
            n_vol_spike += 1
        prev_p = float(m.get("previous_price_dollars") or 0)
        last_p = float(m.get("last_price_dollars") or 0)
        if prev_p > 0 and last_p > 0 and abs((last_p - prev_p) / prev_p) >= 0.20:
            n_price_jump += 1
    print()
    print("  Prompt-time signals (all scored markets, >=20% threshold):")
    print(f"    vol_spike:    {n_vol_spike:>4}  (24h vol >=20% of total)")
    print(f"    price_jump:   {n_price_jump:>4}  (last vs previous >=20% move)")

    # Watchlist overlap
    sig_cache = os.path.join(ROOT, "data", "smart_money", "latest_signals.json")
    if os.path.exists(sig_cache):
        import json as _j
        sm_data  = _j.load(open(sig_cache, encoding="utf-8"))
        sm_tickers = set(sm_data.get("kalshi_tickers", []))
        scanner.tag_watchlist_overlap(deduped, sm_tickers)
        n_boost = sum(1 for m in deduped if m.get("watchlist_signal"))
        print()
        print(f"  Watchlist overlap:      {n_boost:>5}  markets match smart money positions")

    # Time horizon breakdown of flagged
    horizon_counts: dict[str, int] = {}
    for m in flagged:
        h = m.get("time_horizon", "?")
        horizon_counts[h] = horizon_counts.get(h, 0) + 1

    print()
    print("  Flagged by horizon:")
    for h in ["INTRADAY", "WEEKLY", "MONTHLY", "QUARTERLY", "LONG"]:
        cnt = horizon_counts.get(h, 0)
        if cnt:
            print(f"    {h:<12}  {cnt:>4}")

    # Heuristic base rate distribution across all scored markets
    br_dist: dict[str, int] = {"None (BR_NONE)": 0}
    for m in scored:
        br = m.get("base_rate")
        if br is None:
            br_dist["None (BR_NONE)"] += 1
        else:
            key = f"{br:.2f}"
            br_dist[key] = br_dist.get(key, 0) + 1

    print()
    print("  Base rate distribution (scored markets):")
    for br_key in sorted(br_dist, key=lambda k: br_dist[k], reverse=True):
        print(f"    {br_key:<20}  {br_dist[br_key]:>4}")

    # Top flagged by edge
    top_flagged = sorted(
        [m for m in flagged if m.get("raw_edge") is not None],
        key=lambda m: -(m.get("raw_edge") or 0)
    )[:10]

    if top_flagged:
        print()
        print("  Top 10 flagged markets by edge:")
        print(f"  {'Ticker':<28}  {'Path':<10}  {'Edge':>6}  {'Mid':>6}  Title")
        print(f"  {'-'*28}  {'-'*10}  {'-'*6}  {'-'*6}  -----")
        for m in top_flagged:
            ticker = (m.get("ticker") or "")[:28]
            path   = (m.get("flag_path") or "")[:10]
            edge   = f"{(m.get('raw_edge') or 0)*100:.1f}%"
            mid    = f"{(m.get('mid_price') or 0)*100:.1f}%"
            title  = (m.get("title") or "")[:40]
            ws     = " [SM]" if m.get("watchlist_signal") else ""
            print(f"  {ticker:<28}  {path:<10}  {edge:>6}  {mid:>6}  {title}{ws}")

    print()
    print(_rule("="))
    print()


if __name__ == "__main__":
    use_snap = "--snapshot" in sys.argv
    main(use_snapshot=use_snap)
