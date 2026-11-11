"""Write a review digest of the concordance to reviews/YYYY-MM-DD.md (today) and print its path.

Everything is read live: notes via priority.load_notes(), ranking via priority.rank(),
todos via todo_items, branches via git (review.repos in harness.yaml), link health via
links.problems(). Sections with nothing to say, or whose add-on is not configured, are left out.

  1. Top of the stove            top ranked items, score and reasons
  2. Overdue nudges              nudge_every exceeded: what it waits on, days since touched
  3. Just unblocked              every dependency done, the last within UNBLOCKED_DAYS
  4. Pressure                    ranked up because something depends on them
  5. Waiting on others / blocked
  6. Proposed - please confirm   state: proposed, with each note's first body line
  7. Todo coverage               open + inline todos by pin status; oldest unknown open todos
  8. Long-lived branches (NAME)  per configured repo: ahead of its main branch, last commit older
                                 than its stale_days (default BRANCH_STALE_DAYS)
  9. Link health                 counts from links.py check
 10. Questions for OWNER         bullets under "## Open questions" in every note

review.repos entries: {path: ..., name: shown in the heading (default: folder name),
main: main branch (default master), stale_days: (default BRANCH_STALE_DAYS)}

Usage:
  python review.py [--stdout]     --stdout prints the digest instead of writing the file
"""
import argparse, collections, datetime, os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config, links, priority, todo_items

REVIEWS = config.REVIEWS
TOP_N = 10
PROPOSED_N = 20
OLD_TODOS_N = 10
UNBLOCKED_DAYS = 21
BRANCH_STALE_DAYS = 60
FIRST_LINE_LEN = 100
PIN_STATUSES = ["unknown", "pinned", "dead", "done-elsewhere"]
QUESTIONS = re.compile(r"^##\s+Open questions\s*$", re.I)


def short(text, n=FIRST_LINE_LEN):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + " ..."


def waits_on(r, notes):
    """What an item is waiting on: waiting_on field, then unfinished dependencies."""
    meta, out = notes[r["slug"]]["meta"], []
    w = meta.get("waiting_on")
    if w:
        out += [str(x) for x in w] if isinstance(w, list) else [str(w)]
    out += [f"[[{d}]]" for d in r["blocked_by"]]
    return ", ".join(out)


def top(rows, notes):
    out = []
    for i, r in enumerate([r for r in rows if not r["unranked"]][:TOP_N], 1):
        out.append(f"{i:2}. {r['score']:5.1f}  [[{r['slug']}]] ({r['state']})")
        out.append(f"           {'; '.join(r['why'])}")
    return out


def nudges(rows, notes):
    out = []
    for r in rows:
        if r["nudge_overdue"] > 0:
            every = notes[r["slug"]]["meta"].get("nudge_every")
            on = waits_on(r, notes) or "(no waiting_on set)"
            out.append(f"- [[{r['slug']}]]: {r['days_untouched']}d since last touched (nudge every {every}d);"
                       f" waiting on {on}")
    return out


def unblocked(rows, notes):
    out = []
    for r in rows:
        on = r["unblocked_on"]
        if on and (priority.TODAY - on).days <= UNBLOCKED_DAYS:
            deps = ", ".join(f"[[{d}]]" for d in priority.slugs(notes[r["slug"]]["meta"].get("depends_on")))
            out.append(f"- [[{r['slug']}]]: last dependency done {on} ({deps})")
    return out


def pressure(rows, notes):
    out = []
    for r in rows:
        if r["pressure_from"]:
            *own, why = r["why"]  # rank() appends the pressure reason last
            out.append(f"- [[{r['slug']}]] {r['score']:.1f}: {why} (own: {'; '.join(own)})")
    return out


def waiting(rows, notes):
    out = []
    for r in rows:
        if r["blocked_by"] or r["state"] in ("waiting", "blocked"):
            out.append(f"- [[{r['slug']}]] ({r['state']}): waits on {waits_on(r, notes) or '(not recorded)'}")
    return out


def proposed(rows, notes):
    found = sorted(s for s, n in notes.items() if n["meta"].get("state") == "proposed")
    out = [f"- [[{s}]]: {links.summary(notes[s]['body'])}" for s in found[:PROPOSED_N]]
    if len(found) > PROPOSED_N:
        out.append(f"- ... and {len(found) - PROPOSED_N} more")
    return out


def todo_coverage(rows, notes):
    its = [it for it in todo_items.items() if it["source"] in ("open", "inline")]
    if not its:
        return []
    pins = todo_items.pins()
    status = lambda it: pins.get(it["id"], {}).get("status", "unknown")
    counts = collections.Counter((it["source"], status(it)) for it in its)
    out = [f"    {'':8}" + "".join(f"{s:>16}" for s in PIN_STATUSES)]
    for src in ("open", "inline"):
        out.append(f"    {src:8}" + "".join(f"{counts[(src, s)]:16}" for s in PIN_STATUSES))
    old = sorted((it for it in its if it["source"] == "open" and status(it) == "unknown"),
                 key=lambda it: -it["pos"])[:OLD_TODOS_N]
    if old:
        out += ["", "Oldest open todos not yet tied to a note:"]
        out += [f"- {it['id']} (#{it['pos']}): {short(it['first'])}" for it in old]
    return out


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True).stdout.strip()


def branch_rows(repo, main="master"):
    """(last commit date, branch, ahead, behind, merge-base date) for every branch but main, newest first."""
    rows = []
    for ref in git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines():
        if ref == main:
            continue
        behind, ahead = git(repo, "rev-list", "--left-right", "--count", f"{main}...{ref}").split()
        last = git(repo, "log", "-1", "--format=%cs", ref)
        base = git(repo, "merge-base", main, ref)
        base_date = git(repo, "log", "-1", "--format=%cs", base)
        rows.append((last, ref, int(ahead), int(behind), base_date))
    return sorted(rows, reverse=True)


def branches(repo_cfg):
    """Section body for one review.repos entry."""
    path = config.resolve(repo_cfg.get("path"))
    name = repo_cfg.get("name") or os.path.basename(os.path.normpath(path or "repo"))
    out = []
    v = repo_cfg.get("stale_days")  # config's schema guarantees None or a whole number
    stale = BRANCH_STALE_DAYS if v is None else v
    try:
        brs = branch_rows(path, repo_cfg.get("main") or "master")
    except Exception as e:
        return [f"(could not read {name}: {type(e).__name__}: {e})"]
    for last, ref, ahead, behind, base_date in brs:
        age = (priority.TODAY - datetime.date.fromisoformat(last)).days
        if ahead > 0 and age > stale:
            out.append(f"- {ref}: {ahead} ahead, {behind} behind; last commit {last} ({age}d ago),"
                       f" last synced {base_date}")
    return out


def link_health(rows, notes, errors):
    p = links.problems(notes, errors)
    out = [f"- {len(notes)} notes, {len(p['broken'])} broken links, {len(p['errors'])} parse errors,"
           f" {len(p['duplicates'])} duplicate slugs, {len(p['untyped'])} untyped, {len(p['ageless'])} ageless,"
           f" {len(p['cycles'])} depends_on loops"]
    missing = sorted({t for _, t in p["broken"]})
    if missing:
        out.append(f"- links with no note yet: {', '.join(f'[[{t}]]' for t in missing)}")
    if p["errors"]:
        out += [f"- parse error: {links.rel(path)}: {msg}" for path, msg in p["errors"]]
    out += [f"- depends_on loop: {priority.loop_text(l)}" for l in p["cycles"]]
    if not (p["broken"] or p["errors"] or p["duplicates"] or p["untyped"] or p["ageless"] or p["cycles"]):
        return []
    return out


def questions(rows, notes):
    out = []
    for s in sorted(notes):
        on = False
        for line in notes[s]["body"].splitlines():
            if line.startswith("#"):
                on = bool(QUESTIONS.match(line.strip()))
            elif on and re.match(r"^\s*[-*]\s+", line):
                out.append(f"- [[{s}]] {re.sub(r'^\s*[-*]\s+', '', line).strip()}")
            elif on and line.strip() and out and line.startswith(" "):
                out[-1] += " " + line.strip()  # wrapped continuation of the previous bullet
    return out


def build():
    errors = []
    notes = priority.load_notes(errors)
    rows, _ = priority.rank(notes)
    sections = [
        ("Top of the stove", top),
        ("Overdue nudges", nudges),
        ("Just unblocked", unblocked),
        ("Pressure", pressure),
        ("Waiting on others / blocked", waiting),
        ("Proposed - please confirm", proposed),
        ("Todo coverage", todo_coverage),
    ]
    for repo_cfg in config.REVIEW_REPOS:
        path = config.resolve(repo_cfg.get("path"))
        name = repo_cfg.get("name") or os.path.basename(os.path.normpath(path or "repo"))
        sections.append((f"Long-lived branches ({name})", lambda r, n, c=repo_cfg: branches(c)))
    sections += [
        ("Link health", lambda r, n: link_health(r, n, errors)),
        (f"Questions for {config.OWNER}", questions),
    ]
    out = [f"# Review {priority.TODAY}", "",
           "Generated by `scripts/review.py`. [[slug]] = concordance note. Empty sections are left out.", ""]
    for title, fn in sections:
        body = fn(rows, notes)
        if body:
            out += [f"## {title}", ""] + body + [""]
    return "\n".join(out)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--stdout", action="store_true")
    a = ap.parse_args()
    if not os.path.isdir(priority.CONCORDANCE):
        sys.exit(f"no concordance folder at {priority.CONCORDANCE} (set HARNESS_ROOT or run inside a harness)")
    text = build()
    if a.stdout:
        print(text)
        return
    os.makedirs(REVIEWS, exist_ok=True)
    path = os.path.join(REVIEWS, f"{priority.TODAY}.md")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(path)


if __name__ == "__main__":
    main()
