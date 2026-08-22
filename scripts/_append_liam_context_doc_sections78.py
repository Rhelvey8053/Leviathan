"""
scripts/_append_liam_context_doc_sections78.py — one-off script, run 2026-08-22.

Appended sections 7 and 8 to the existing monday.com Doc "Liam Context —
Reading the Backlog Correctly" (doc id 46152477), chaining off the last
existing block's id (queried live via `docs(ids: [...]) { blocks { id type } }`
rather than assumed) so this adds to the doc instead of duplicating it.
Kept for provenance, not meant to be re-run.

Source text: docs/liam_context_doc.md sections 7-8. See
scripts/_post_liam_context_doc.py for the block-format helpers and the two
monday API quirks (content must be json.dumps()'d, add_content_to_doc_from_markdown
doesn't exist on this account's schema) this reuses.

Third API quirk found here: `Document.blocks` silently caps at a default
limit (25 -- confirmed via `__type(name: "Document")` introspection, which
shows `limit`/`page` args) when queried without one. A post-append read-back
using the unpaginated field showed 25 blocks ending at the OLD last block ID,
which looked exactly like a failed write (new block IDs missing entirely)
and briefly triggered a false-alarm investigation. Always pass
`blocks(limit: 100)` (or higher) when verifying a doc's true block count --
the field does not error or warn on truncation, it just silently returns
a partial list.
"""
import json
import sys

sys.path.insert(0, r"C:\Users\Administrator\Downloads\Leviathan")
from scripts import monday_sync as ms

DOC_ID = "46152477"
LAST_EXISTING_BLOCK_ID = "67a64f90-b0fd-4224-ad4d-509db701ed62"  # section 6's body, queried live


def text_block(text, bold=False):
    return {
        "alignment": "left", "direction": "ltr",
        "deltaFormat": [{"insert": text, **({"attributes": {"bold": True}} if bold else {})}],
    }


BLOCKS = [
    ("medium_title", text_block("7. Own the External Research thread as a standing section, not one-off mentions")),
    ("normal_text", text_block(
        "Your regulatory/competitor findings are the highest-leverage thing you do, "
        "precisely because they don't require anything from backlog.json -- it's "
        "genuinely new information a human or a Claude Code session would otherwise "
        "have to go re-research from scratch every time."
    )),
    ("normal_text", text_block(
        "Please make this an explicit, clearly-labeled \"External Research\" section in "
        "every report, even when there's nothing new (say \"no new developments since "
        "last report\" rather than omitting the section) -- a standing section that's "
        "reliably in the same place is something a downstream process can scan directly "
        "instead of hunting for a scattered mention buried in prose.", bold=True
    )),
    ("normal_text", text_block("Cover:")),
    ("bulleted_list", {
        "alignment": "left", "direction": "ltr", "indentation": 1,
        "deltaFormat": [{"insert": "Kalshi/prediction-market regulatory status (state-by-state legal/geofencing actions, CFTC activity)"}],
    }),
    ("bulleted_list", {
        "alignment": "left", "direction": "ltr", "indentation": 1,
        "deltaFormat": [{"insert": "Competing platforms' API/feature changes (Polymarket, Manifold, PredictIt, Metaculus)"}],
    }),
    ("bulleted_list", {
        "alignment": "left", "direction": "ltr", "indentation": 1,
        "deltaFormat": [{"insert": "Any tool evaluated for adoption here (e.g. graphify) -- organic-growth/security/maintenance-health signals, not a recommendation on whether to adopt it; that decision is the project owner's"}],
    }),
    ("medium_title", text_block("8. Gate progress is now visible directly on the board (as of 2026-08-22) -- read it, don't infer it")),
    ("normal_text", text_block(
        "Every locked/blocked item's Detail field now includes a live-computed "
        "\"Gate: <metric>=<live value> <op> <threshold> (MET/not met)\" line, generated "
        "fresh on every sync from the real database -- unlike the static thresholds in "
        "section 4 above, which only show the target, not the current value. A "
        "sentinel-gated item (section 3) reads \"[requires human decision, never "
        "auto-computed]\" instead of a MET/not-met verdict."
    )),
    ("normal_text", text_block(
        "Read this line directly from the item's own Detail field before making any "
        "status recommendation. It's the authoritative live answer to the exact "
        "question that caused the two wrong recommendations this doc exists to "
        "prevent -- you no longer have to infer it or leave it unstated.", bold=True
    )),
]


def main():
    after_id = LAST_EXISTING_BLOCK_ID
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
