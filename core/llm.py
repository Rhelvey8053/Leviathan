"""
core/llm.py — Anthropic Messages API client for Leviathan scoring.

backend=api replaces the CLI subprocess in scorer.py with:
  - Forced tool_choice (record_scores / record_probe) for zero-regex structured output
  - web_search_20250305 for live web search (server-side, Anthropic-hosted)
  - Prompt cache on the system prompt (confirmed ~8k tokens, well above 1024 minimum)
  - 2 retries with exponential backoff on APIError / APITimeoutError
  - Real token counts and cost_usd from per-model pricing constants

Agentic loop: one API call with tool_choice="any" lets Claude search and then
call record_scores. Web search is executed server-side by Anthropic so no
client-side result handling is needed. If Claude reaches end_turn without calling
record_scores, a forced second call constrains tool_choice to record_scores only.

_find_claude() is the canonical CLI binary finder — imported by scorer.py and
analysis/research_probe.py for the legacy backend="cli" path.
"""

import json
import os
import shutil
import time
from datetime import datetime, timezone
from typing import Any

import anthropic
from dotenv import load_dotenv

load_dotenv()

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── Daily cost ceiling (llm-cost-ceiling) ─────────────────────────────────────
# Real Anthropic API spend from score_via_api/probe_via_api is metered and
# unbounded by default -- unlike main.py's CLI/Pro-subscription scan path,
# which has no per-token bill. This matters most once replay-runner starts
# calling these functions at volume. State persists in a small JSON file
# (same pattern as scripts/gate_notifier.py's data/gate_state.json) so the
# ceiling holds across process restarts and across every caller (research
# probes, eval re-scoring, future replay-runner), not just one run.

DAILY_COST_STATE_PATH = os.path.join(_ROOT, "data", "llm_daily_cost.json")
DEFAULT_DAILY_COST_CEILING_USD = 20.0


class LLMCostCeilingExceeded(RuntimeError):
    """Raised when today's accumulated LLM spend has already met/exceeded the configured ceiling."""


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_daily_cost_state() -> dict:
    try:
        with open(DAILY_COST_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"date": _today_str(), "total_cost_usd": 0.0}


def _save_daily_cost_state(state: dict) -> None:
    os.makedirs(os.path.dirname(DAILY_COST_STATE_PATH), exist_ok=True)
    with open(DAILY_COST_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def get_daily_cost_usd() -> float:
    """
    Today's accumulated real API spend from core.llm calls (UTC day).
    Returns 0.0 if no calls have been made today, or if the persisted state
    is from a prior day (the total resets with the day; a stale file is
    never carried over into today's figure).
    """
    state = _load_daily_cost_state()
    if state.get("date") != _today_str():
        return 0.0
    return float(state.get("total_cost_usd", 0.0))


def _accumulate_daily_cost(cost_usd: float) -> float:
    """Adds cost_usd to today's running total (resetting on a new UTC day), persists, returns the new total."""
    today = _today_str()
    state = _load_daily_cost_state()
    if state.get("date") != today:
        state = {"date": today, "total_cost_usd": 0.0}
    state["total_cost_usd"] = round(float(state.get("total_cost_usd", 0.0)) + cost_usd, 6)
    _save_daily_cost_state(state)
    return state["total_cost_usd"]


def _check_cost_ceiling(config: dict) -> None:
    """
    Raises LLMCostCeilingExceeded if today's accumulated spend has already
    met or exceeded the configured ceiling -- called BEFORE issuing an API
    request, so a caller looping over many markets (replay-runner) stops
    making new calls the moment the ceiling is hit, rather than finding out
    only after the call that breached it already ran.
    """
    llm_cfg = config.get("llm", {})
    ceiling = float(llm_cfg.get("daily_cost_ceiling_usd", DEFAULT_DAILY_COST_CEILING_USD))
    current = get_daily_cost_usd()
    if current >= ceiling:
        raise LLMCostCeilingExceeded(
            f"Daily LLM cost ceiling reached: ${current:.2f} >= ${ceiling:.2f} "
            f"for {_today_str()}. No further API calls until the ceiling resets "
            f"tomorrow, or daily_cost_ceiling_usd is raised in config.json."
        )

# ── Per-model pricing (USD per token, July 2026) ──────────────────────────────
_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {
        "input":       3.00  / 1_000_000,
        "output":      15.00 / 1_000_000,
        "cache_write": 3.75  / 1_000_000,
        "cache_read":  0.30  / 1_000_000,
    },
    "claude-opus-4-7": {
        "input":       15.00 / 1_000_000,
        "output":      75.00 / 1_000_000,
        "cache_write": 18.75 / 1_000_000,
        "cache_read":  1.50  / 1_000_000,
    },
    "claude-haiku-4-5-20251001": {
        "input":       0.80 / 1_000_000,
        "output":      4.00 / 1_000_000,
        "cache_write": 1.00 / 1_000_000,
        "cache_read":  0.08 / 1_000_000,
    },
}
_DEFAULT_PRICING = _PRICING["claude-sonnet-4-6"]

# ── Tool schemas ──────────────────────────────────────────────────────────────

RECORD_SCORES_TOOL: dict[str, Any] = {
    "name": "record_scores",
    "description": (
        "Record your probability estimates for all scored markets. "
        "Call this once with all markets after completing your research."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "scores": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "ticker":          {"type": "string"},
                        "market_price":    {"type": "number"},
                        "our_estimate":    {"type": "number"},
                        "edge":            {"type": "number"},
                        "direction":       {"type": "string", "enum": ["YES", "NO", "PASS"]},
                        "confidence":      {"type": "string", "enum": ["HIGH", "MED", "LOW"]},
                        "reasoning":       {"type": "string"},
                        "sources_checked": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [
                        "ticker", "market_price", "our_estimate", "edge",
                        "direction", "confidence", "reasoning", "sources_checked",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["scores"],
        "additionalProperties": False,
    },
}

RECORD_PROBE_TOOL: dict[str, Any] = {
    "name": "record_probe",
    "description": "Record your probability estimate for the single market you researched.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ticker":              {"type": "string"},
            "claude_estimate":     {"type": "number"},
            "predicted_direction": {"type": "string", "enum": ["YES", "NO", "PASS"]},
            "confidence":          {"type": "string", "enum": ["HIGH", "MED", "LOW"]},
            "rationale":           {"type": "string"},
        },
        "required": [
            "ticker", "claude_estimate", "predicted_direction", "confidence", "rationale",
        ],
        "additionalProperties": False,
    },
}

_SCORE_REQUIRED = frozenset({
    "ticker", "market_price", "our_estimate", "edge",
    "direction", "confidence", "reasoning", "sources_checked",
})
_VALID_DIRECTION  = frozenset({"YES", "NO", "PASS"})
_VALID_CONFIDENCE = frozenset({"HIGH", "MED", "LOW"})

# ── CLI binary finder (canonical — imported by scorer.py and research_probe.py) ─

def _find_claude() -> str:
    """Locate the claude CLI binary."""
    cmd = shutil.which("claude")
    if cmd:
        return cmd
    candidates = [
        r"C:\Users\Administrator\AppData\Local\AnthropicClaude\claude.exe",
        r"C:\Program Files\Claude\claude.exe",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    raise RuntimeError(
        "claude CLI not found in PATH. "
        "Run Leviathan from a Claude Code terminal, or ensure `claude` is in PATH."
    )


# ── Internal helpers ──────────────────────────────────────────────────────────

def _make_client() -> anthropic.Anthropic:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set in environment")
    return anthropic.Anthropic(api_key=key)


def _cost_usd(usage: Any, model: str) -> float:
    p = _PRICING.get(model, _DEFAULT_PRICING)
    return (
        getattr(usage, "input_tokens", 0)                  * p["input"]
        + getattr(usage, "output_tokens", 0)               * p["output"]
        + getattr(usage, "cache_creation_input_tokens", 0) * p["cache_write"]
        + getattr(usage, "cache_read_input_tokens", 0)     * p["cache_read"]
    )


def _token_info(response: Any, model: str) -> dict:
    u = response.usage
    return {
        "input_tokens":                  getattr(u, "input_tokens", 0),
        "output_tokens":                 getattr(u, "output_tokens", 0),
        "cache_creation_input_tokens":   getattr(u, "cache_creation_input_tokens", 0),
        "cache_read_input_tokens":       getattr(u, "cache_read_input_tokens", 0),
        "cost_usd":                      round(_cost_usd(u, model), 6),
    }


def _finalize_token_info(*responses: Any, model: str) -> dict:
    """
    token_info for a completed exchange, with combined cost folded into
    today's running total (daily_total_usd) -- the single point both
    score_via_api and probe_via_api go through before returning, so the
    ceiling is enforced no matter which one a caller uses.

    Accepts one response for the common case, or two when _force_tool()
    fired a second billed call after Claude reached end_turn without
    calling the tool on the first attempt -- an expected, even routine,
    occurrence per this module's own docstring. A prior version only
    priced whichever single response was passed in, silently dropping the
    other call's tokens (typically the larger of the two, since the first
    call carries the full system prompt and any web-search results) from
    both the returned cost_usd and the persisted daily ceiling total,
    letting the ceiling be blown through unnoticed on exactly this path.
    """
    combined = {
        "input_tokens": 0, "output_tokens": 0,
        "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
        "cost_usd": 0.0,
    }
    for response in responses:
        info = _token_info(response, model)
        for key in ("input_tokens", "output_tokens",
                    "cache_creation_input_tokens", "cache_read_input_tokens"):
            combined[key] += info[key]
        combined["cost_usd"] += info["cost_usd"]
    combined["cost_usd"] = round(combined["cost_usd"], 6)
    combined["daily_total_usd"] = _accumulate_daily_cost(combined["cost_usd"])
    return combined


def _find_tool_use(response: Any, name: str) -> Any | None:
    """Return the first tool_use content block with the given name, or None."""
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == name:
            return block
    return None


def _validate_scores(scores: list[dict]) -> None:
    """Raise ValueError if any score dict is missing a required field or has a bad enum value."""
    for s in scores:
        missing = _SCORE_REQUIRED - set(s.keys())
        if missing:
            raise ValueError(
                f"record_scores: ticker={s.get('ticker', '?')} missing fields: {missing}"
            )
        if s["direction"] not in _VALID_DIRECTION:
            raise ValueError(
                f"record_scores: ticker={s['ticker']} bad direction={s['direction']!r}"
            )
        if s["confidence"] not in _VALID_CONFIDENCE:
            raise ValueError(
                f"record_scores: ticker={s['ticker']} bad confidence={s['confidence']!r}"
            )


def _system_block(text: str) -> list[dict]:
    """System prompt as a cached content block (>1024 tokens confirmed)."""
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def _force_tool(
    client: anthropic.Anthropic,
    model: str,
    system: list[dict],
    messages: list[dict],
    prior_content: list,
    tool: dict,
    tool_name: str,
    temperature: float | None = None,
) -> Any:
    """Force a specific tool call after Claude reached end_turn without calling it."""
    extra_kwargs: dict = {} if temperature is None else {"temperature": temperature}
    forced = client.messages.create(
        model=model,
        system=system,
        messages=messages + [
            {"role": "assistant", "content": prior_content},
            {
                "role": "user",
                "content": f"You have completed your research. Now call {tool_name} with your estimates.",
            },
        ],
        tools=[tool],
        tool_choice={"type": "tool", "name": tool_name},
        max_tokens=4096,
        extra_headers={
            "anthropic-beta": "prompt-caching-2024-07-31",
        },
        **extra_kwargs,
    )
    return forced


# ── Public API ────────────────────────────────────────────────────────────────

def score_via_api(
    system_prompt: str,
    user_prompt: str,
    config: dict,
    temperature: float | None = None,
) -> tuple[list[dict], dict]:
    """
    Score markets via Anthropic Messages API with forced tool_choice structured output.

    Returns (scores, token_info) where:
      scores     — list of dicts matching scorer.py RESPONSE_SCHEMA (8 required fields)
      token_info — input_tokens, output_tokens, cache_creation/read tokens, cost_usd

    Web search (web_search_20250305) is executed server-side by Anthropic.
    System prompt cache control applied (confirmed ~8k tokens, well above 1024 minimum).
    Retries 2 times with 5s / 10s backoff on APIError or APITimeoutError.

    temperature is omitted from the API call (Anthropic default) unless
    explicitly passed — production callers are unaffected. The eval harness
    (analysis/eval_rescore.py) pins temperature=0 for reproducible re-scoring.
    """
    _check_cost_ceiling(config)

    llm_cfg      = config.get("llm", {})
    model        = llm_cfg.get("model", "claude-sonnet-4-6")
    max_searches = int(llm_cfg.get("max_web_searches", 8))

    tools: list[dict] = [
        {"type": "web_search_20250305", "name": "web_search", "max_uses": max_searches},
        RECORD_SCORES_TOOL,
    ]
    system   = _system_block(system_prompt)
    messages = [{"role": "user", "content": user_prompt}]
    client   = _make_client()
    last_exc: Exception | None = None
    extra_kwargs: dict = {} if temperature is None else {"temperature": temperature}

    for attempt in range(3):
        try:
            response = client.messages.create(
                model=model,
                system=system,
                messages=messages,
                tools=tools,
                tool_choice={"type": "any"},
                max_tokens=4096,
                extra_headers={
                    "anthropic-beta": "web-search-2025-03-05,prompt-caching-2024-07-31",
                },
                **extra_kwargs,
            )

            block = _find_tool_use(response, "record_scores")
            if block is not None:
                scores = block.input["scores"]
                _validate_scores(scores)
                return scores, _finalize_token_info(response, model=model)

            # Claude finished without calling record_scores — force it
            forced = _force_tool(
                client, model, system, messages,
                response.content, RECORD_SCORES_TOOL, "record_scores",
                temperature=temperature,
            )
            block = _find_tool_use(forced, "record_scores")
            if block is None:
                raise RuntimeError("score_via_api: forced record_scores returned no tool_use block")
            scores = block.input["scores"]
            _validate_scores(scores)
            # Both calls are billed -- price the pair together, not just the forced one.
            return scores, _finalize_token_info(response, forced, model=model)

        except (anthropic.APIError, anthropic.APITimeoutError) as e:
            last_exc = e
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(
                f"score_via_api: API error after 3 attempts: {e}"
            ) from e

    raise RuntimeError(f"score_via_api: failed after 3 attempts: {last_exc}")


def probe_via_api(
    system_prompt: str,
    user_prompt: str,
    config: dict,
) -> tuple[dict, dict]:
    """
    Probe a single market via API with record_probe tool.
    Returns (probe_input_dict, token_info).
    probe_input_dict keys: ticker, claude_estimate, predicted_direction, confidence, rationale.
    """
    _check_cost_ceiling(config)

    llm_cfg      = config.get("llm", {})
    model        = llm_cfg.get("model", "claude-sonnet-4-6")
    max_searches = int(llm_cfg.get("max_web_searches", 8))

    tools: list[dict] = [
        {"type": "web_search_20250305", "name": "web_search", "max_uses": max_searches},
        RECORD_PROBE_TOOL,
    ]
    system   = _system_block(system_prompt)
    messages = [{"role": "user", "content": user_prompt}]
    client   = _make_client()
    last_exc: Exception | None = None

    for attempt in range(3):
        try:
            response = client.messages.create(
                model=model,
                system=system,
                messages=messages,
                tools=tools,
                tool_choice={"type": "any"},
                max_tokens=1024,
                extra_headers={
                    "anthropic-beta": "web-search-2025-03-05,prompt-caching-2024-07-31",
                },
            )

            block = _find_tool_use(response, "record_probe")
            if block is not None:
                return block.input, _finalize_token_info(response, model=model)

            forced = _force_tool(
                client, model, system, messages,
                response.content, RECORD_PROBE_TOOL, "record_probe",
            )
            block = _find_tool_use(forced, "record_probe")
            if block is None:
                raise RuntimeError("probe_via_api: forced record_probe returned no tool_use block")
            # Both calls are billed -- price the pair together, not just the forced one.
            return block.input, _finalize_token_info(response, forced, model=model)

        except (anthropic.APIError, anthropic.APITimeoutError) as e:
            last_exc = e
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(
                f"probe_via_api: API error after 3 attempts: {e}"
            ) from e

    raise RuntimeError(f"probe_via_api: failed after 3 attempts: {last_exc}")
