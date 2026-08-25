"""
scripts/_append_liam_context_doc_section9.py — one-off script, run 2026-08-25.

Appended section 9 to the existing monday.com Doc "Liam Context --
Reading the Backlog Correctly" (doc id 46152477), chaining off the last
existing block's id (queried live via `docs(ids: [...]) { blocks(limit: 100) { id type } }`
per the truncation quirk documented in
_append_liam_context_doc_sections78.py, not assumed). Kept for
provenance, not meant to be re-run.

Source text: docs/liam_context_doc.md section 9. Requested by the
project owner: have Liam's regulatory research go beyond Kalshi-branded
headlines into the category-wide regulatory picture (CFTC rulemaking,
other operators' regulator actions, competitor regulatory exposure,
dated upcoming events, and Kalshi-specific market-availability impact).
"""
import json
import sys

sys.path.insert(0, r"C:\Users\Administrator\Downloads\Leviathan")
from scripts import monday_sync as ms

DOC_ID = "46152477"
LAST_EXISTING_BLOCK_ID = "a08a7c4d-f447-4213-a452-a2e38864a655"  # section 8's closing sentence, queried live


def text_block(text, bold=False):
    return {
        "alignment": "left", "direction": "ltr",
        "deltaFormat": [{"insert": text, **({"attributes": {"bold": True}} if bold else {})}],
    }


def bullet(text):
    return {
        "alignment": "left", "direction": "ltr", "indentation": 1,
        "deltaFormat": [{"insert": text}],
    }


BLOCKS = [
    ("medium_title", text_block("9. Regulatory research -- go deeper than Kalshi headlines (added 2026-08-25, at the owner's request)")),
    ("normal_text", text_block(
        "Section 7 already asks for a standing External Research section. This extends "
        "what \"regulatory\" should actually cover, since Kalshi's own press/blog is not "
        "the only -- or even the most reliable -- source:"
    )),
    ("bulleted_list", bullet(
        "CFTC rulemaking and guidance on event contracts / prediction markets as a "
        "regulated category, not just news mentioning Kalshi by name. A proposed-rule "
        "comment period, a no-action letter, or a public statement about the category "
        "as a whole is more consequential than any single operator's press coverage, "
        "and matters regardless of which specific operator it touches first."
    )),
    ("bulleted_list", bullet(
        "State-by-state gaming/gambling regulator actions against ANY prediction-market "
        "operator, not just Kalshi -- cease-and-desist letters, state AG actions, "
        "licensing disputes. Washington's geofencing action (already found and folded "
        "into cross-venue-expansion) is one data point; check whether other states are "
        "following the same pattern or taking a different one."
    )),
    ("bulleted_list", bullet(
        "Competing platforms' own regulatory exposure and history (Polymarket's CFTC "
        "settlement and US-user restrictions, PredictIt's CFTC no-action-letter status, "
        "Manifold/Metaculus as unregulated play-money alternatives) -- useful context "
        "for how exposed Kalshi specifically is relative to the category as a whole."
    )),
    ("bulleted_list", bullet(
        "Upcoming, dated events when you find them: comment-period deadlines, scheduled "
        "hearings, court dates on any pending litigation. A dated future risk is more "
        "useful to flag than a past event already covered elsewhere."
    )),
    ("bulleted_list", bullet(
        "When a regulatory action could affect market AVAILABILITY on Kalshi "
        "specifically (a market category being delisted, a state being geofenced), say "
        "so explicitly -- that's the concrete operational impact for this project "
        "(fewer/different markets to scan), not just abstract industry news."
    )),
    ("normal_text", text_block(
        "Keep citing sources as you already do. If there's nothing new in a given "
        "period, say so explicitly per section 7's standing-section rule -- don't skip "
        "the section.", bold=True
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
