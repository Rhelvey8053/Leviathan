"""
core/blind_scorer.py — price-blind shadow scoring (backlog: price-blind-arm).

Provides the counterfactual for whether the anchored scorer (core/scorer.py)
adds information over the market price itself, by scoring the same markets
with no price shown and no price-anchoring instructions.

This is NOT just scorer.py's SYSTEM_PROMPT with the "Current market price"
line deleted. core/scorer.py's prompt leaks price-derived information in
several other places -- FLAG REASON (compares a heuristic base rate to the
market price), SIGNAL QUALITY (the Leviathan Score grade is itself derived
from net_edge, which is price-derived), and the HEURISTIC/POLYMARKET/
CONSENSUS conflict warnings (all keyed off heuristic_direction, which is
computed by comparing base_rate to Kalshi's own mid_price). All of these
are stripped here, not just the literal price line -- otherwise the blind
arm would still be indirectly anchored and the experiment would be invalid.

Design choice: SYSTEM_PROMPT_BLIND is scorer.SYSTEM_PROMPT plus an appended
override block, rather than a hand-copied duplicate of all ~47 calibration
rules. Most of those rules are pure category base-rate/evidence-quality
guidance that never mentions market price and carries over to blind mode
unchanged -- duplicating them would just create a second copy that silently
drifts out of sync the next time someone edits a rule in the live prompt.
Only a handful of rules are STRUCTURALLY about comparing to the market
price (there is no non-price residue to preserve if you just "ignore the
price part"), and those get a real, concrete, price-free replacement
instruction in the override block below -- not just a "disregard this" note.

Always scored via core.llm's metered API path (score_blind_via_api), never
the CLI/Pro-subscription backend, regardless of config["llm"]["backend"] --
the sample-rate-vs-daily-cost-ceiling constraint this item calls for is
only enforceable through core.llm's _check_cost_ceiling gate. Since this
path always spends real money whenever config.blind_arm.enabled is true,
it is additionally gated by core.llm's config.llm.api_spend_authorized
guard (2026-08-19) -- enabling blind_arm alone is not enough to make this
fire; api_spend_authorized must also be explicitly true.
"""

from datetime import datetime, timezone as _tz

from . import scorer as _scorer
from .llm import score_blind_via_api as _score_blind_via_api

BLIND_MODE_OVERRIDE = (
    "\n\nBLIND MODE OVERRIDE — READ BEFORE APPLYING THE RULES ABOVE:\n"
    "You are NOT shown the current Kalshi market price for any market below, "
    "and must not guess or reference one. Most of the calibration rules above "
    "are category base-rate/evidence-quality guidance and apply unchanged. "
    "The following rules explicitly compare your estimate to the market price "
    "or to a price-derived gap, which does not exist in this mode -- apply "
    "these substitute instructions instead:\n"
    "  Rule 1 (tail probability): Apply this to your OWN estimate instead of "
    "the market's price -- if your estimate would land below 15% or above "
    "85%, treat that as an extreme claim requiring extraordinary, "
    "independently-verified evidence before you commit to it.\n"
    "  Rule 11 (edge requirement) and Rule 30 (anchoring guard): Do not "
    "apply as written -- there is no market price to compute an edge "
    "against or anchor away from. Report your raw probability estimate and "
    "a confidence level reflecting evidence strength alone. Do not output "
    "a direction (YES/NO/PASS) or an edge value. Rule 30's underlying "
    "purpose still matters, though: when your web research turns up only "
    "weak, generic, or ambiguous evidence, your estimate should land on "
    "the CATEGORY BASE RATE given in the applicable rule above (e.g. ~25% "
    "for an unconfirmed media release, ~35% for a legislative bill), not "
    "on an arbitrary number pulled from thin air. Only move meaningfully "
    "away from that base rate when you find something concrete, recent, "
    "and specific.\n"
    "  Rule 13 (price/level markets): You cannot see whether Kalshi's price "
    "is already near 50%, so judge this from the market question alone: for "
    "any 'will X reach $Y' or 'will X be above/below Z' style question, "
    "default your estimate near 50% unless you find a specific, dated "
    "catalyst that is not yet public knowledge.\n"
    "  Rule 28 (short-horizon edge decay): For markets closing within 7 "
    "days, weight only recent (dated within 72 hours), primary-source "
    "evidence heavily -- long-run historical base rates from the rules "
    "above are much less applicable this close to resolution. There is no "
    "edge threshold to apply since you have no price to compare against.\n"
    "  Rule 29 (LV score grade edge threshold scaling): Does not apply -- "
    "no SIGNAL QUALITY line is shown in this mode.\n"
    "  Rule 32 (sports match outcome): Ignore the 'gap vs. another platform "
    "≥15pp' clause -- you have no platform prices to compare against here. "
    "The underlying guidance still applies in full: professional bettors "
    "have already priced in everything public, so default to LOW confidence "
    "unless you find a specific, dated fact (confirmed injury, lineup "
    "change) that would not yet be broadly known.\n"
    "  Rule 35 (Fed rate decisions): Ignore the 'CME FedWatch vs Kalshi "
    "price, diverges ≥10pp' mechanic -- you have no Kalshi price to compare "
    "against. Instead, search for the current CME FedWatch-implied "
    "probability for the specific meeting and report that directly as your "
    "estimate, adjusted only for primary-source news dated after the most "
    "recent FedWatch update.\n"
)

SYSTEM_PROMPT_BLIND = _scorer.SYSTEM_PROMPT + BLIND_MODE_OVERRIDE

BLIND_RESPONSE_SCHEMA = """
Return a JSON array where each element has exactly these fields:
{
  "ticker": "string — Kalshi ticker",
  "estimate": 0.00,
  "confidence": "HIGH | MED | LOW",
  "reasoning": "2-3 sentences max",
  "sources_checked": ["headline or url"]
}

confidence reflects evidence strength alone -- there is no market price in
this mode to compute an "edge" against, so do not include a direction or
edge field.
"""


def build_prompt_blind(markets: list[dict], now: "datetime | None" = None) -> str:
    """
    Price-blind per-market prompt. Deliberately shows far less than
    scorer.build_prompt(): ticker, title, horizon, and days-to-close survive
    unchanged, plus WHALE ALERT (informed-trader positioning is independent
    evidence, not a market-price comparison). Everything else in
    build_prompt() -- current price, FLAG REASON, SIGNAL QUALITY, the
    HEURISTIC/POLYMARKET/CONSENSUS conflict warnings, SIGNAL SUMMARY, DRIFT/
    REVERSAL/SPREAD signals, and the price-history section -- is omitted
    because each one is derived from comparing to Kalshi's own market price,
    which would make the "blind" arm not actually blind.
    """
    lines = [
        "Score the following Kalshi prediction markets. For each, search for "
        "recent relevant information and estimate the TRUE probability of "
        "YES occurring, based ONLY on real-world evidence.\n",
        BLIND_RESPONSE_SCHEMA,
        "\n--- MARKETS ---\n",
    ]

    _now = now if now is not None else datetime.now(_tz.utc)

    for i, m in enumerate(markets, 1):
        horizon = m.get("time_horizon", "MONTHLY")
        horizon_note = {
            "INTRADAY":  "closes today — weight breaking news and current momentum only",
            "WEEKLY":    "closes within 7 days — near-term catalysts most relevant",
            "MONTHLY":   "closes within 30 days — balance recent news with base rates",
            "QUARTERLY": "closes within 90 days — fundamentals and structural factors carry more weight",
            "LONG":      "closes 90+ days out — base rates and long-run trends dominate",
        }.get(horizon, "")

        close_str = m.get("close_time") or m.get("expiration_time", "")
        days_left = None
        if close_str:
            try:
                close_dt  = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
                days_left = max(0, (close_dt - _now).days)
            except (ValueError, AttributeError):
                pass
        days_note = f" ({days_left}d remaining)" if days_left is not None else ""

        lines.append(f"{i}. [{m.get('ticker', '')}] {(m.get('title', ''))[:120]}")
        lines.append(f"   Horizon: {horizon} ({horizon_note})")
        lines.append(f"   Closes: {close_str}{days_note}")

        whale = m.get("whale_data")
        if whale and whale.get("whale_detected"):
            lines.append(
                f"   WHALE ALERT: Large trades detected buying {whale.get('whale_direction', 'unknown')}. "
                f"Max trade size: {whale.get('max_trade_size', 0):.0f} (avg: {whale.get('avg_trade_size', 0):.1f})"
            )

        lines.append("")

    return "\n".join(lines)


def score_blind(markets: list[dict], config: dict) -> tuple[list[dict], dict]:
    """
    Score markets price-blind via the metered Anthropic API (never CLI,
    regardless of config["llm"]["backend"] -- see module docstring).

    Returns (results, token_info) where each result dict has keys:
    ticker, estimate, confidence, reasoning, sources_checked.
    Raises whatever core.llm.score_blind_via_api raises (including
    LLMCostCeilingExceeded) -- callers are responsible for catching this,
    same as every other API-backend call site in this codebase.
    """
    if not markets:
        return [], {}
    user_prompt = build_prompt_blind(markets)
    return _score_blind_via_api(SYSTEM_PROMPT_BLIND, user_prompt, config)
