"""
core/cross_model.py — independent cross-model corroboration.

backlog: cross-model-corroboration. Gets a second, independent opinion
from a DIFFERENT model family (never Claude) via a local OmniRoute
gateway (https://github.com/diegosouzapw/OmniRoute), for the small
shortlisted-pick set only -- same call site and cadence as
core.scorer.ground_citations in main.py's _rescore_shortlist_for_
clean_sources. Never blended into direction/confidence/edge or any
win-rate/Brier calculation: purely an auxiliary signal persisted to its
own `signals.cross_model_opinion` column for the user to read, same
"separate column, never pooled" discipline as core.sizing's
stake_size_hypothetical and the blind_scores table.

Off by default (config.cross_model.enabled=false). Uses OmniRoute's
keyless "auto" route by default -- zero API key, zero signup, routes to
whatever free backend OmniRoute currently has healthy (verified live
2026-09-02: resolved to a real model, replied correctly). A caller who
configures a different `model`/paid provider is on their own for any
account/credential setup that requires -- this module never touches
Claude Code's own auth and never assumes any specific backend.

Requires OmniRoute running locally (`npm i -g omniroute`, then
`omniroute` in a terminal -- or any persistent-process tool, e.g.
herdr, keeping it alive across sessions). This module makes zero
assumption that it is: any failure (gateway not running, connection
refused, backend timeout, malformed response) is caught and logged,
never raised -- get_opinion() returns None and the caller's pick keeps
whatever it already had, exactly like ground_citations's own
try/except in main.py.
"""

import json

import requests

DEFAULT_BASE_URL = "http://localhost:20128/v1"
DEFAULT_MODEL = "auto"
# Live-verified 2026-09-02: OmniRoute's keyless "auto" route resolved to a
# real reasoning-capable backend ("big-pickle") -- a trivial "reply PONG"
# round-tripped in seconds, but a genuine forecasting question took ~70-90s
# to return a real, coherent answer (20s and even 60s both timed out on the
# same prompt that succeeded at 120s). This is an unknown-quality free
# backend, not Claude -- budget real time for it rather than assuming
# API-like latency.
DEFAULT_TIMEOUT_S = 90

_VALID_DIRECTIONS = {"YES", "NO", "PASS"}


def _extract_json_object(text: str) -> dict | None:
    """
    Same defensive extraction core.scorer._score_via_cli uses for its
    JSON array, adapted for a single object -- an unknown-quality free
    model is more likely than Claude to wrap its reply in markdown
    fences or add stray prose around the JSON.
    """
    import re
    fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, re.DOTALL)
    if fence:
        candidate = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        candidate = text[start:end + 1] if start != -1 and end > start else text
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def get_opinion(market: dict, our_score: dict, config: dict | None = None) -> dict | None:
    """
    Returns {"model", "direction", "estimate", "reasoning"} or None.

    market: the scored market dict -- reads ticker/title/mid_price
        (falls back to market_price) only, nothing Claude-specific.
    our_score: Claude's own score dict for this market (our_estimate/
        direction/confidence) -- shown to the second model so it can
        independently agree or disagree, never to be blended with it.
    config: reads config.cross_model.{enabled,base_url,model,timeout_s}.
        Returns None immediately when not enabled -- callers never need
        to check the flag themselves. Never raises; every failure mode
        (disabled, gateway unreachable, timeout, malformed reply) prints
        a one-line non-fatal notice and returns None.
    """
    cm_cfg = (config or {}).get("cross_model", {})
    if not cm_cfg.get("enabled", False):
        return None

    base_url = cm_cfg.get("base_url", DEFAULT_BASE_URL)
    model = cm_cfg.get("model", DEFAULT_MODEL)
    timeout_s = cm_cfg.get("timeout_s", DEFAULT_TIMEOUT_S)
    ticker = market.get("ticker", "")

    prompt = (
        f"Prediction market question: \"{market.get('title', '')}\" (ticker {ticker}).\n"
        f"Current market price implies {market.get('mid_price') or market.get('market_price')}.\n"
        "Another forecaster estimated the true probability of YES at "
        f"{our_score.get('our_estimate')} and would bet {our_score.get('direction')} "
        f"with {our_score.get('confidence')} confidence.\n\n"
        "Independently of that estimate, give your OWN probability estimate for YES "
        "and a one-sentence reason. Reply with ONLY this JSON, no other text: "
        '{"direction": "YES|NO|PASS", "estimate": 0.00, "reasoning": "..."}'
    )

    try:
        resp = requests.post(
            f"{base_url}/chat/completions",
            json={"model": model, "messages": [{"role": "user", "content": prompt}]},
            timeout=timeout_s,
        )
        resp.raise_for_status()
        envelope = resp.json()
        content = envelope["choices"][0]["message"]["content"]
        used_model = envelope.get("model", model)
    except Exception as e:
        print(f"  [cross_model] {ticker}: request failed (non-fatal): {e}")
        return None

    parsed = _extract_json_object(content or "")
    if parsed is None:
        print(f"  [cross_model] {ticker}: reply was not valid JSON (non-fatal): {content[:200]!r}")
        return None

    direction = (parsed.get("direction") or "").strip().upper()
    if direction not in _VALID_DIRECTIONS:
        print(f"  [cross_model] {ticker}: unrecognized direction {direction!r} (non-fatal)")
        return None

    return {
        "model": used_model,
        "direction": direction,
        "estimate": parsed.get("estimate"),
        "reasoning": (parsed.get("reasoning") or "").strip(),
    }
