"""
scripts/_post_liam_context_doc.py — one-off script, already run 2026-08-20.

Created the monday.com Doc "Liam Context — Reading the Backlog Correctly"
(https://reedhelveys-team.monday.com/docs/18427442892, workspace 17049665,
kind=public) and populated it with docs/liam_context_doc.md's content, as
monday doc blocks. Kept for provenance -- shows exactly how the doc was
built, not meant to be re-run (it would create a duplicate doc).

Two API surprises worth recording for next time:
  - The documented add_content_to_doc_from_markdown mutation does not
    exist on this account's live schema despite being in monday's public
    API reference -- fell back to create_doc_block per block.
  - create_doc_block's `content` argument is typed JSON! in the schema
    but the API actually rejects a raw object; it must be a JSON-encoded
    STRING (json.dumps(content), not content itself).
  - "checked" is only a valid content property for check_list blocks, not
    bulleted_list -- including it raises "Unrecognized property [checked]".

Doc content update policy: docs/liam_context_doc.md is the source text.
To push an edit, either hand-write a new create_doc_block call chained
off the relevant block's id (see the query in the __main__ block below
for how to list existing block ids), or delete_doc_block the stale ones
and re-run a similar posting loop.
"""
import json
import sys

sys.path.insert(0, r"C:\Users\Administrator\Downloads\Leviathan")
from scripts import monday_sync as ms

DOC_ID = "46152477"


def text_block(text, bold=False):
    return {
        "alignment": "left", "direction": "ltr",
        "deltaFormat": [{"insert": text, **({"attributes": {"bold": True}} if bold else {})}],
    }


def list_block(text):
    return {
        "alignment": "left", "direction": "ltr",
        "deltaFormat": [{"insert": text}],
        "indentation": 1,
    }


BLOCKS = [
    ("large_title", text_block("Leviathan Board — Context for Liam (PM Agent)")),
    ("normal_text", text_block(
        "This doc exists because the weekly/daily reports have twice recommended moving "
        "auto-calibration-loop and replay-instrument-validation from Blocked to Ready, and "
        "both were wrong both times. This is the context that was missing. Read this before "
        "recommending any status change."
    )),
    ("medium_title", text_block("1. backlog.json is the source of truth, not the board")),
    ("normal_text", text_block(
        "This board is a one-way mirror of a file called backlog/backlog.json in the "
        "project's repo, synced by a script. Every item's real gating logic lives in that "
        "file's trigger and depends_on fields -- the board's Status column (Ready/Locked/"
        "Blocked/Done) is just a rendering of it, updated once a day at most. If you're "
        "ever unsure why an item is Blocked, the honest answer is \"check backlog.json,\" "
        "not \"infer it from what's visible on the board.\""
    )),
    ("medium_title", text_block("2. depends_on being all Done does NOT mean an item is unlockable")),
    ("normal_text", text_block("This is the mistake that's happened twice. An item can have two independent gates:")),
    ("bulleted_list", list_block("depends_on: a list of other backlog item IDs that must all be status=Done.")),
    ("bulleted_list", list_block("trigger: a separate condition on a real, live metric (e.g. resolved_count >= 30).")),
    ("normal_text", text_block("Both must be satisfied, not just one.", bold=True)),
    ("normal_text", text_block(
        "auto-calibration-loop's dependencies (sample-size-gates, brier-tracking) are both "
        "Done -- but its trigger requires resolved_count >= 30, and the real live count is "
        "13. It is correctly Blocked. Checking only depends_on and ignoring trigger is "
        "exactly the error that produced the wrong recommendation both times."
    )),
    ("normal_text", text_block(
        "Before recommending any item move to Ready, state both dependency status AND "
        "trigger status, with the real live metric value. If you can't verify the live "
        "metric value, say so explicitly instead of assuming the dependency check alone "
        "is sufficient.", bold=True
    )),
    ("medium_title", text_block("3. Some items are gated behind a sentinel metric -- these will never auto-clear")),
    ("normal_text", text_block(
        "A few items use a trigger on a metric name that is deliberately never computed by "
        "anything (e.g. api_spend_authorized, graphify_corpus_shape_changed). This is "
        "intentional: it means the item is blocked on a human decision, not a measurable "
        "threshold, and it is structurally impossible for it to become Ready on its own -- "
        "no amount of time passing or dependencies completing will change it."
    )),
    ("normal_text", text_block(
        "replay-instrument-validation is one of these. Its dependencies (replay-runner, "
        "market-baseline-brier) are Done, but its trigger is api_spend_authorized >= 1, a "
        "sentinel that is never computed. The reason: the project owner has explicitly "
        "decided the bot may only use the Claude Pro subscription, never metered Anthropic "
        "API spend, and this item requires a real metered API cost to run. It will stay "
        "Blocked until the owner personally decides otherwise -- this is policy, not a "
        "stale gap. Do not recommend moving it to Ready."
    )),
    ("normal_text", text_block(
        "If an item's trigger metric name isn't in backlog.json's own metrics_glossary, or "
        "the glossary entry describes it as a sentinel/policy gate, treat that item as not "
        "eligible for a \"stale block\" recommendation at all.", bold=True
    )),
    ("medium_title", text_block("4. Real, current gate thresholds (verify the live value before citing these)")),
    ("bulleted_list", list_block("edge-decay-analysis, auto-calibration-loop (partial): resolved_count >= 30")),
    ("bulleted_list", list_block("calibration-curve, calibration-curve-dashboard: resolved_count >= 50")),
    ("bulleted_list", list_block("per-wallet-track-record, skill-vs-luck-weighting, wallet-tracking-dashboard: resolved_count_per_wallet_max >= 10")),
    ("bulleted_list", list_block("slippage-tracking: fills_count >= 20")),
    ("normal_text", text_block(
        "These are the same thresholds the project's own pre-registered methodology doc "
        "uses (n=50 is the pre-registered checkpoint for any calibration conclusion) -- "
        "they are not arbitrary, and none of them have been met yet as of this doc's writing."
    )),
    ("medium_title", text_block("5. There is a human-side check on every report you post")),
    ("normal_text", text_block(
        "The project owner runs scripts/verify_liam_report.py against every report before "
        "acting on it -- it fetches your latest post and independently recomputes real "
        "trigger/dependency status for every locked/blocked item from live data. A "
        "recommendation that doesn't hold up against that check doesn't get acted on, so a "
        "correct report is strictly more useful than a fast one. If you can't confirm live "
        "metric values, say \"resolved_count unknown, recommend the owner check backlog/"
        "checker.py's live output\" instead of guessing or recommending an action based on "
        "depends_on alone."
    )),
    ("medium_title", text_block("6. What you're genuinely good at (keep doing this)")),
    ("normal_text", text_block(
        "External research -- regulatory changes, competitor platform/API updates, package "
        "security findings on evaluated tools -- has been accurate and useful (e.g. the "
        "Kalshi Washington State geofencing finding, and correctly flagging that empirical-"
        "base-rates-poly was never wired into live scoring). Keep surfacing that kind of "
        "finding; it gets read and acted on when it's real and verified."
    )),
]


def main():
    after_id = None
    for block_type, content in BLOCKS:
        resp = ms.gql('''
            mutation($doc_id: ID!, $type: DocBlockContentType!, $content: JSON!, $after_block_id: String) {
              create_doc_block(doc_id: $doc_id, type: $type, content: $content, after_block_id: $after_block_id) {
                id
              }
            }
        ''', {"doc_id": DOC_ID, "type": block_type, "content": json.dumps(content), "after_block_id": after_id})
        after_id = resp["create_doc_block"]["id"]
        print(block_type, "->", after_id)


if __name__ == "__main__":
    main()
