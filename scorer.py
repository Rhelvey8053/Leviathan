import json
import os
import re
import time
import anthropic
from dotenv import load_dotenv

load_dotenv()

SYSTEM_PROMPT = (
    "You are a prediction market analyst. For each market provided, estimate the true "
    "probability of the YES outcome occurring. Use web search to find relevant recent "
    "information. Return ONLY valid JSON — no markdown, no explanation outside the JSON. "
    "Be calibrated, not overconfident."
)

RESPONSE_SCHEMA = """
Return a JSON array where each element has exactly these fields:
{
  "ticker": "string — Kalshi ticker",
  "market_price": 0.00,
  "our_estimate": 0.00,
  "edge": 0.00,
  "direction": "YES | NO | PASS",
  "confidence": "HIGH | MED | LOW",
  "reasoning": "2-3 sentences max",
  "sources_checked": ["headline or url"]
}

direction = "YES" if our_estimate > market_price and edge is worth acting on
direction = "NO" if our_estimate < market_price and edge is worth acting on
direction = "PASS" if edge is not meaningful or evidence is unclear
"""


def build_prompt(markets: list[dict]) -> str:
    lines = [
        "Score the following Kalshi prediction markets. For each, search for recent "
        "relevant information and estimate the true probability of YES occurring.\n",
        RESPONSE_SCHEMA,
        "\n--- MARKETS ---\n",
    ]

    for i, m in enumerate(markets, 1):
        mid_price = m.get("mid_price")
        whale = m.get("whale_data")

        horizon = m.get("time_horizon", "MONTHLY")
        horizon_note = {
            "INTRADAY":  "closes today — weight breaking news and current momentum only",
            "WEEKLY":    "closes within 7 days — near-term catalysts most relevant",
            "MONTHLY":   "closes within 30 days — balance recent news with base rates",
            "QUARTERLY": "closes within 90 days — fundamentals and structural factors carry more weight",
            "LONG":      "closes 90+ days out — base rates and long-run trends dominate",
        }.get(horizon, "")

        lines.append(f"{i}. [{m.get('ticker', '')}] {(m.get('title', ''))[:120]}")
        lines.append(f"   Horizon: {horizon} ({horizon_note})")
        lines.append(f"   Current market price (YES): {f'{mid_price * 100:.1f}%' if mid_price is not None else 'unknown'}")
        lines.append(f"   Closes: {m.get('close_time') or m.get('expiration_time', '')}")

        if whale and whale.get("whale_detected"):
            lines.append(
                f"   WHALE ALERT: Large trades detected buying {whale.get('whale_direction', 'unknown')}. "
                f"Max trade size: {whale.get('max_trade_size', 0):.0f} (avg: {whale.get('avg_trade_size', 0):.1f})"
            )

        if m.get("whale_reversal"):
            lines.append(
                f"   REVERSAL SIGNAL: Whale buying {(whale or {}).get('whale_direction', 'unknown')} while "
                f"price is trending the opposite direction — possible informed contrarian positioning."
            )

        if m.get("drift_flag"):
            drift_pct = (m.get("price_drift") or 0) * 100
            lines.append(
                f"   DRIFT SIGNAL: Order-book mid is {abs(drift_pct):.1f}% "
                f"{'above' if drift_pct > 0 else 'below'} last traded price — mean reversion candidate."
            )

        if m.get("spread_wide"):
            lines.append(
                f"   SPREAD SIGNAL: Bid/ask spread is {(m.get('spread_pct') or 0) * 100:.1f}% of mid — "
                f"market maker uncertainty, higher probability of mispricing."
            )

        ext = m.get("ext_markets") or []
        cons = m.get("ext_consensus") or {}
        if ext:
            ext_lines = []
            for e in ext[:4]:
                gap      = e.get("price_gap", 0) * 100
                gap_str  = f"{gap:+.1f}%"
                ext_lines.append(f"{e['source']}: {e['probability']*100:.1f}% ({gap_str} vs Kalshi, match {e['match_score']:.2f})")
            if cons.get("consensus_dir"):
                avg_pct = (cons.get("avg_ext_price") or 0) * 100
                cgap    = (cons.get("consensus_gap") or 0) * 100
                ext_lines.append(
                    f"Consensus ({cons['sources_higher']} higher, {cons['sources_lower']} lower): "
                    f"avg {avg_pct:.1f}% ({cgap:+.1f}% vs Kalshi) → lean {cons['consensus_dir']}"
                )
            lines.append("   CROSS-MARKET:")
            for el in ext_lines:
                lines.append(f"   · {el}")

        smart_money = m.get("smart_money") or []
        if smart_money:
            yes_traders = [s for s in smart_money if s.get("direction") == "YES"]
            no_traders  = [s for s in smart_money if s.get("direction") == "NO"]
            sm_parts = []
            if yes_traders:
                avg_pnl = sum(s["avg_pct_pnl"] for s in yes_traders) / len(yes_traders)
                sm_parts.append(f"{len(yes_traders)} winning wallet(s) buying YES (avg portfolio +{avg_pnl:.1f}%)")
            if no_traders:
                avg_pnl = sum(s["avg_pct_pnl"] for s in no_traders) / len(no_traders)
                sm_parts.append(f"{len(no_traders)} winning wallet(s) buying NO (avg portfolio +{avg_pnl:.1f}%)")
            lines.append(f"   SMART MONEY: {'; '.join(sm_parts)}")

        poly = m.get("poly")
        if poly and poly.get("price_gap") is not None:
            gap      = poly["price_gap"] * 100
            direction = "higher" if gap > 0 else "lower"
            lines.append(
                f"   POLYMARKET: Equivalent market priced at {poly['poly_price'] * 100:.1f}% "
                f"({abs(gap):.1f}% {direction} than Kalshi). "
                f'Match: "{poly["poly_question"][:80]}" (score {poly["match_score"]:.2f})'
            )

        if m.get("ob_flag"):
            imb = (m.get("ob_imbalance") or 0) * 100
            ob_dir = m.get("ob_direction", "?")
            lines.append(
                f"   ORDER BOOK: {imb:.0f}% of depth is on the {ob_dir} side — "
                f"strong {'buying' if ob_dir == 'YES' else 'selling'} pressure."
            )

        if m.get("base_rate") is not None:
            lines.append(f"   Base rate estimate: {m['base_rate'] * 100:.1f}%")

        lines.append("")

    return "\n".join(lines)


def score_markets(flagged_markets: list[dict], config: dict) -> tuple[list[dict], dict]:
    """
    Calls Claude with web_search tool enabled for a batch of flagged markets.
    Returns (scored_markets, token_info).
    """
    if not flagged_markets:
        return [], {}

    scorer_model = config.get("scoring", {}).get("scorer_model", "claude-sonnet-4-6")
    max_markets = config.get("scoring", {}).get("max_markets_per_run", 20)
    batch = flagged_markets[:max_markets]
    prompt = build_prompt(batch)

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    cached_system = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
    cached_tools  = [{"type": "web_search_20250305", "name": "web_search", "cache_control": {"type": "ephemeral"}}]

    for attempt in range(3):
        try:
            response = client.messages.create(
                model=scorer_model,
                max_tokens=4096,
                system=cached_system,
                tools=cached_tools,
                messages=[{"role": "user", "content": prompt}],
            )
            break
        except anthropic.RateLimitError:
            if attempt == 2:
                raise
            wait = 60 * (attempt + 1)
            print(f"  [scorer] Rate limited — waiting {wait}s before retry...")
            time.sleep(wait)

    all_text = "\n".join(
        block.text for block in response.content
        if hasattr(block, "text") and block.text.strip()
    )
    if not all_text:
        raise RuntimeError("scorer.py: Claude returned no text content")

    fence_match = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", all_text, re.DOTALL)
    if fence_match:
        raw_json = fence_match.group(1).strip()
    else:
        start, end = all_text.find("["), all_text.rfind("]")
        raw_json = all_text[start:end + 1] if start != -1 and end > start else all_text

    try:
        scored = json.loads(raw_json)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"scorer.py: Failed to parse Claude JSON: {e}\nRaw: {raw_json[:500]}")

    usage = response.usage
    token_info = {
        "input_tokens":       usage.input_tokens,
        "output_tokens":      usage.output_tokens,
        "cache_read_tokens":  getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cache_write_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
    }

    return scored, token_info
