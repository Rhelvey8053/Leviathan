"""
Net-of-spread edge analysis -- shows realizable edge distribution for flagged markets.

Computes raw_edge and net_edge (raw_edge - half_spread) for all flagged markets,
showing what fraction of theoretical edge is actually capturable after bid-ask cost.

Usage:
    python analysis/net_edge_analysis.py              # live Kalshi API fetch
    python analysis/net_edge_analysis.py --snapshot   # use latest saved snapshot (fast)
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


def _pct(v):
    return f"{float(v)*100:.1f}pp" if v is not None else "--"


def _load_snapshot() -> list[dict]:
    snap_dir = os.path.join(ROOT, "data", "snapshots")
    files = sorted(glob.glob(os.path.join(snap_dir, "markets_*.json")))
    if not files:
        raise FileNotFoundError(f"No snapshot files in {snap_dir}")
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
    print("LEVIATHAN -- NET-OF-SPREAD EDGE ANALYSIS")
    print(_rule("="))
    print()

    if use_snapshot:
        print("Loading snapshot...")
        try:
            all_markets = _load_snapshot()
        except Exception as e:
            print(f"  Snapshot load failed: {e}")
            return
    else:
        try:
            kalshi.authenticate(config)
        except Exception as e:
            print(f"Auth failed: {e}")
            return
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

    print(f"  Raw markets: {len(all_markets)}")

    filtered = scanner.filter_markets(all_markets, config)
    scored   = scanner.score_markets(filtered, config)

    if config.get("markets", {}).get("dedup_by_event", False):
        scored = scanner.dedup_by_event_scored(scored)

    flagged = [m for m in scored if m.get("flag")]
    print(f"  Flagged markets: {len(flagged)}")
    print()

    # Separate markets by whether we have spread data
    with_spread  = [m for m in flagged if m.get("net_edge") is not None]
    no_spread    = [m for m in flagged if m.get("net_edge") is None]

    tradeable    = [m for m in with_spread if (m.get("net_edge") or 0) > 0]
    spread_dom   = [m for m in with_spread if (m.get("net_edge") or 0) <= 0]

    print(_rule("-"))
    print("  TRADEABILITY SUMMARY")
    print(_rule("-"))
    print(f"  Markets with spread data:  {len(with_spread):>4} / {len(flagged)}")
    if with_spread:
        pct_t = len(tradeable) / len(with_spread) * 100
        pct_s = len(spread_dom) / len(with_spread) * 100
        print(f"    Tradeable (net_edge > 0):  {len(tradeable):>4}  ({pct_t:.0f}%)")
        print(f"    Spread-dominant (<= 0):    {len(spread_dom):>4}  ({pct_s:.0f}%)")
    print(f"  No spread data (1-sided book): {len(no_spread):>4}")
    print()

    # Flag path breakdown by net_edge
    if with_spread:
        from collections import defaultdict
        fp_net: dict[str, list] = defaultdict(list)
        for m in with_spread:
            fp = m.get("flag_path") or "UNKNOWN"
            fp_net[fp].append(m.get("net_edge") or 0)

        print(_rule("-"))
        print("  NET EDGE BY FLAG PATH")
        print(_rule("-"))
        print(f"  {'Flag Path':<18}  {'Count':>5}  {'Avg Raw':>8}  {'Avg Net':>8}  {'% > 0':>6}")
        print(f"  {'-'*18}  {'-'*5}  {'-'*8}  {'-'*8}  {'-'*6}")
        for fp in sorted(fp_net, key=lambda x: -len(fp_net[x])):
            mkt_list = [m for m in with_spread if (m.get("flag_path") or "UNKNOWN") == fp]
            avg_raw  = sum(m.get("raw_edge") or 0 for m in mkt_list) / len(mkt_list)
            avg_net  = sum(m.get("net_edge") or 0 for m in mkt_list) / len(mkt_list)
            pct_pos  = sum(1 for m in mkt_list if (m.get("net_edge") or 0) > 0) / len(mkt_list) * 100
            print(f"  {fp:<18}  {len(mkt_list):>5}  {_pct(avg_raw):>8}  {_pct(avg_net):>8}  {pct_pos:>5.0f}%")
        print()

    # Net edge distribution histogram
    if with_spread:
        buckets = {
            "< -10pp":     [m for m in with_spread if (m.get("net_edge") or 0) < -0.10],
            "-10 to 0pp":  [m for m in with_spread if -0.10 <= (m.get("net_edge") or 0) <= 0],
            "0 to 5pp":    [m for m in with_spread if 0 < (m.get("net_edge") or 0) <= 0.05],
            "5 to 10pp":   [m for m in with_spread if 0.05 < (m.get("net_edge") or 0) <= 0.10],
            "> 10pp":       [m for m in with_spread if (m.get("net_edge") or 0) > 0.10],
        }
        print(_rule("-"))
        print("  NET EDGE DISTRIBUTION (flagged markets with spread data)")
        print(_rule("-"))
        bar_width = 30
        for label, mlist in buckets.items():
            n    = len(mlist)
            pct  = n / len(with_spread) * 100 if with_spread else 0
            bar  = "#" * int(pct / 100 * bar_width)
            mark = "  [SPREAD > EDGE]" if label in ("< -10pp", "-10 to 0pp") else ""
            print(f"  {label:<14}  {n:>4}  {pct:>4.0f}%  {bar:<{bar_width}}{mark}")
        print()

    # Top tradeable markets by net_edge
    top_tradeable = sorted(
        tradeable,
        key=lambda m: -(m.get("net_edge") or 0)
    )[:10]

    if top_tradeable:
        print(_rule("-"))
        print("  TOP 10 FLAGGED MARKETS BY NET EDGE (best realizable edge)")
        print(_rule("-"))
        print(f"  {'Ticker':<28}  {'Path':<10}  {'Raw':>6}  {'Net':>6}  {'Spread%':>7}  Title")
        print(f"  {'-'*28}  {'-'*10}  {'-'*6}  {'-'*6}  {'-'*7}  -----")
        for m in top_tradeable:
            ticker   = (m.get("ticker") or "")[:28]
            path     = (m.get("flag_path") or "")[:10]
            raw      = f"{(m.get('raw_edge') or 0)*100:.1f}%"
            net      = f"{(m.get('net_edge') or 0)*100:.1f}%"
            sp_pct   = f"{(m.get('spread_pct') or 0)*100:.1f}%"
            title    = (m.get("title") or "")[:38]
            sm_tag   = " [SM]" if m.get("watchlist_signal") else ""
            print(f"  {ticker:<28}  {path:<10}  {raw:>6}  {net:>6}  {sp_pct:>7}  {title}{sm_tag}")
        print()

    # Worst spread-dominated markets (highest raw_edge but spread_dominant)
    if spread_dom:
        worst = sorted(
            spread_dom,
            key=lambda m: -(m.get("raw_edge") or 0)
        )[:5]
        print(_rule("-"))
        print("  TOP 5 SPREAD-DOMINANT MARKETS (theoretical edge eaten by spread)")
        print(_rule("-"))
        print(f"  {'Ticker':<28}  {'Raw':>6}  {'Net':>6}  {'Spread%':>7}  Title")
        print(f"  {'-'*28}  {'-'*6}  {'-'*6}  {'-'*7}  -----")
        for m in worst:
            ticker = (m.get("ticker") or "")[:28]
            raw    = f"{(m.get('raw_edge') or 0)*100:.1f}%"
            net    = f"{(m.get('net_edge') or 0)*100:.1f}%"
            sp_pct = f"{(m.get('spread_pct') or 0)*100:.1f}%"
            title  = (m.get("title") or "")[:40]
            print(f"  {ticker:<28}  {raw:>6}  {net:>6}  {sp_pct:>7}  {title}")
        print()

    print(_rule("="))
    print()


if __name__ == "__main__":
    use_snap = "--snapshot" in sys.argv
    main(use_snapshot=use_snap)
