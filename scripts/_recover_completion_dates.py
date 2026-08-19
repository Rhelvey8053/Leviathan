"""
One-off script (not part of the regular toolchain): walk backlog/backlog.json's
git history commit-by-commit and find the first commit where each item's
status became "done" -- that commit's author date is the item's real
completion date, recovered from history rather than guessed. Falls back to
parsing an explicit "SHIPPED YYYY-MM-DD" / date pattern from the item's own
notes field when the item was already "done" in the very first commit that
introduced it (no visible transition in this file's history to anchor on).

Prints a JSON report {item_id: "YYYY-MM-DD" or null}. Read-only -- does not
write to backlog.json or the board.
"""
import json
import re
import subprocess
import sys

ROOT = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip()
PATH = "backlog/backlog.json"
CANDIDATE_PATHS = ["backlog/backlog.json", "backlog.json"]


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=ROOT, check=True).stdout


def commits():
    """
    List of (sha, author_date_iso) touching either candidate path, oldest
    first -- a single git log call across both pathspecs so ordering comes
    from git's own topological/chronological walk, not a manual string
    sort of %aI (which is wrong whenever author timezone offsets differ,
    since e.g. "20:00:00-05:00" sorts before "22:00:00+00:00" lexically
    despite being a later instant in real time).
    """
    out = git("log", "--format=%H %aI", "--reverse", "--", *CANDIDATE_PATHS)
    result = []
    for line in out.splitlines():
        if not line.strip():
            continue
        sha, date = line.split(" ", 1)
        result.append((sha, date))
    return result


def load_at(sha):
    for path in CANDIDATE_PATHS:
        try:
            text = git("show", f"{sha}:{path}")
        except subprocess.CalledProcessError:
            continue
        try:
            d = json.loads(text)
        except json.JSONDecodeError:
            continue
        items = d["items"] if isinstance(d, dict) else d
        return {i["id"]: i for i in items}
    return None


_SHIPPED_RE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")


def notes_fallback_date(item):
    notes = item.get("notes") or ""
    m = _SHIPPED_RE.search(notes)
    if m:
        return m.group(1)
    return None


def main():
    with open(f"{ROOT}/{PATH}", encoding="utf-8") as f:
        current = json.load(f)
    current_items = {i["id"]: i for i in current["items"]}
    all_ids = set(current_items)
    done_ids = {iid for iid, it in current_items.items() if it["status"] == "done"}

    first_seen_status = {}   # item_id -> status at first commit it appears in
    first_seen_date = {}      # item_id -> author_date of first commit it appears in at all
    done_at = {}              # item_id -> author_date of first commit where status == done

    cs = commits()
    for sha, date in cs:
        snapshot = load_at(sha)
        if snapshot is None:
            continue
        for iid in all_ids:
            item = snapshot.get(iid)
            if item is None:
                continue
            if iid not in first_seen_status:
                first_seen_status[iid] = item["status"]
                first_seen_date[iid] = date
            if item["status"] == "done" and iid not in done_at:
                done_at[iid] = date

    if "--started" in sys.argv:
        started = {iid: (first_seen_date[iid][:10] if iid in first_seen_date else None) for iid in sorted(all_ids)}
        print(json.dumps(started, indent=2))
        return

    report = {}
    for iid in sorted(done_ids):
        if iid in done_at and first_seen_status.get(iid) != "done":
            # Real transition observed in this file's history.
            report[iid] = {"date": done_at[iid][:10], "source": "git-transition", "commit": None}
        else:
            # Born done (or never observed transitioning) -- fall back to notes.
            fallback = notes_fallback_date(current_items[iid])
            if fallback:
                report[iid] = {"date": fallback, "source": "notes-shipped-date", "commit": None}
            elif iid in done_at:
                # Born done in this file's history, but we do have SOME commit date -- weakest signal.
                report[iid] = {"date": done_at[iid][:10], "source": "git-first-appearance-already-done", "commit": None}
            else:
                report[iid] = None

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
