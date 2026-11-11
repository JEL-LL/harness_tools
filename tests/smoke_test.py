"""Smoke test: run every command of every script against a throwaway copy of example/.

    python tests/smoke_test.py        (from any folder; exit 0 when every check passes)

Needs only PyYAML. Each run copies example/ to a temp folder and points HARNESS_ROOT at it;
example/ itself is hashed before and after to prove it was not written to. Scores age with
today's date, so only facts that hold on any day after the example's story (Sep-Nov 2026)
are asserted, never exact scores.

  Run 1  add-ons on: priority, links, review, note, thought_log, todo_items
  Run 2  add-ons off (thought_log and todo removed from harness.yaml)
  Run 3  bad settings: an unknown key and a wrong type stop every script with one line
  Run 4  edge cases (tests/smoke_edges.py): failing link checks, review.repos, HARNESS_CONFIG,
         null and invalid settings, missing todo sources, odd thought-log stamps, note.py corners
"""
import os, re, sys, tempfile

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # python -I leaves this folder off sys.path
sys.dont_write_bytecode = True  # no tests/__pycache__ from importing the helpers
from smoke_lib import ALL_SCRIPTS, EXAMPLE, RESULTS, Harness, check, lines, ranked, tree_hash
import smoke_edges


# ---------------------------------------------------------------- run 1: add-ons on

def run_on(h):
    print("\n== Run 1: add-ons on ==")
    for s in ALL_SCRIPTS[1:]:
        h.expect(f"{s} -h", s, "-h", has=("usage:",))

    # priority
    p = h.expect("priority rank", "priority")
    check("rank lists ranked items, hides blocked and proposed",
          {"roof-permit", "compost-bins", "raised-beds"} <= set(ranked(p)) and
          not {"tool-shed-roof", "pollinator-strip", "greenhouse"} & set(ranked(p)), ranked(p))
    p = h.expect("priority --all", "priority", "--all")
    check("--all adds blocked, proposed and dormant items, never dead ones",
          {"tool-shed-roof", "pollinator-strip", "greenhouse"} <= set(ranked(p)) and "beehive" not in ranked(p), ranked(p))
    row = next((l for l in p.stdout.splitlines() if " tool-shed-roof " in l), "")
    check("--all: blocked tool-shed-roof lists its dependency", row.rstrip().endswith("waits on roof-permit"), row)
    p = h.expect("priority -n 2", "priority", "-n", "2")
    check("-n 2 shows two items", len(ranked(p)) == 2, p.stdout)
    h.expect("explain: pressure lifts the low-base blocker", "priority", "explain", "roof-permit",
             has=("base 0.5", "pressure from [[tool-shed-roof]]"))
    h.expect("explain: last_touched beats older mentions", "priority", "explain", "roof-permit",
             has=("(since 2026-10-28)",))
    h.expect("explain: knob with a date until (2099) lifts", "priority", "explain", "garden-season",
             has=("+1.0 lifted (keep the umbrella in view all season)",))
    h.expect("explain: dormant does not age", "priority", "explain", "greenhouse", has=("[dormant]",),
             lacks=("untouched",))
    h.expect("explain on a dead note exits non-zero", "priority", "explain", "beehive",
             code="nonzero", has=("not ranked",), where="stderr")
    h.expect("explain: blocked item names its dependency", "priority", "explain", "tool-shed-roof",
             has=("[blocked]", "waiting on: roof-permit"))
    h.expect("explain: unblock bump from a done dependency", "priority", "explain", "raised-beds",
             has=("unblocked 2026-10-30",))
    h.expect("explain: nudge overdue, aged from the latest thought-log mention", "priority", "explain",
             "compost-bins", has=("nudge overdue (every 7d)", "(since 2026-10-12)"))
    h.expect("explain: knob until [[raised-beds]] done is active", "priority", "explain", "harvest-supper",
             has=("suppressed (beds first, then the supper)",))
    p = h.expect("explain: half_life knob", "priority", "explain", "rain-barrel", has=("suppressed (wait out",))
    m = re.search(r"([-+]\d+\.\d) suppressed \(wait out", p.stdout)
    # Decays toward 0 (prints -0.0 from about 160 days after it was set), never past it.
    check("half_life knob has decayed below its amount of 2", m and -2.0 < float(m.group(1)) <= 0, p.stdout)
    h.expect("explain on a done note exits non-zero", "priority", "explain", "lumber-order",
             code="nonzero", has=("not ranked",), where="stderr")

    # links
    h.expect("links check: no broken links, nothing ageless", "links", "check",
             has=("16 notes, 0 broken links, 0 parse errors, 0 duplicate slugs, 0 untyped, 0 ageless, "
                  "0 depends_on loops",))
    h.expect("links backlinks", "links", "backlinks", "garden-season", has=("raised-beds", "compost-bins"))
    p = h.expect("links orphans", "links", "orphans")
    check("links orphans: the example has none", not p.stdout.strip(), p.stdout)
    before = h.read("concordance", "INDEX.md")
    h.expect("links index", "links", "index", has=("16 notes in 5 types",))
    after = h.read("concordance", "INDEX.md")
    check("example's INDEX.md is current (regenerating changes nothing)", before == after)
    check("INDEX.md lists notes with states", "- [[raised-beds]] — " in after and "(active)" in after)

    # review
    h.expect("review --stdout has its sections", "review", "--stdout", has=(
        "## Top of the stove", "## Overdue nudges", "## Pressure", "## Waiting on others / blocked",
        "## Proposed - please confirm", "## Todo coverage", "## Questions for the garden committee",
        "[[compost-bins]]: ", "waiting on [[sam]]", "[[tool-shed-roof]] (blocked): waits on [[roof-permit]]",
        "[[pollinator-strip]]", "Line the beds with cardboard"))
    p = h.expect("review writes its file", "review")
    path = p.stdout.strip()
    check("review file exists under reviews/", os.path.isfile(path) and
          os.path.dirname(path) == h.path("reviews") and h.read("reviews", os.path.basename(path)).startswith("# Review"),
          path)

    # thought_log
    h.expect("thought_log stats", "thought_log", "stats",
             has=("segments: 16  first: 2026-09-08  last: 2026-11-10", "repeated dates: 1",
                  "pass discovery-1: 0/16"))
    h.expect("thought_log show", "thought_log", "show", "2026-10-05", "2099-01-01",
             has=("10/05/2026", "permit", "[no segment 2099-01-01]"))
    h.expect("thought_log show of a repeated date's #2 id", "thought_log", "show", "2026-11-06#2",
             has=("Evening: Sam asked about a greenhouse",), lacks=("Framed two raised beds",))
    # next skips days dated today or later; every story day is before 2026-11-11, so these hold on any later day.
    p = h.expect("thought_log next -n 100 (whole log)", "thought_log", "next", "--pass", "discovery-1", "-n", "100")
    check("next lists all 16 days", len(lines(p)) == 16, p.stdout)
    p = h.expect("thought_log next --kb 0", "thought_log", "next", "--pass", "discovery-1", "-n", "100", "--kb", "0")
    check("--kb 0 still picks one day, then stops at the budget", [l.split("	")[0] for l in lines(p)] == ["2026-11-10"],
          p.stdout)
    p = h.expect("thought_log next", "thought_log", "next", "--pass", "discovery-1", "-n", "3")
    ids = [l.split("\t")[0] for l in lines(p)]
    check("next picks the newest three days", ids == ["2026-11-10", "2026-11-09", "2026-11-06#2"], ids)
    h.expect("thought_log mark", "thought_log", "mark", "--pass", "discovery-1", *ids, has=("marked 3",))
    h.expect("stats counts the marked days", "thought_log", "stats", has=("pass discovery-1: 3/16",))
    p = h.expect("next skips marked days", "thought_log", "next", "--pass", "discovery-1", "-n", "1",
                 has=("2026-11-06	",))
    check("ledger.jsonl has one line per mark", len(h.read("scans", "discovery-1", "ledger.jsonl").splitlines()) == 3)
    h.expect("mark on an unknown pass exits non-zero", "thought_log", "mark", "--pass", "nope", "2026-11-10",
             code="nonzero", has=("No pass 'nope'",), where="stderr")
    p = h.expect("thought_log grep, newest first", "thought_log", "grep", "compost")
    check("grep hits the two compost days, newest first",
          [l.split(":")[0] for l in lines(p)] == ["2026-10-12", "2026-09-14"], p.stdout)
    h.expect("grep ignores the inline todo section", "thought_log", "grep", "second water tap", lacks=("tap",))
    p = h.expect("grep -c is case sensitive", "thought_log", "grep", "-c", "sam")
    check("grep -c 'sam' finds nothing (the log only has 'Sam')", not p.stdout.strip(), p.stdout)
    h.expect("grep without -c ignores case", "thought_log", "grep", "sam", has=("Sam",))
    h.expect("grep shows one line per day, with a count of the rest", "thought_log", "grep", "permit",
             has=("2026-10-05: Shed roof is worse", "(+1 more lines; -a shows all)"))
    h.expect("grep -a shows every matching line", "thought_log", "grep", "-a", "permit",
             has=("and the permit needs a sketch",), lacks=("more lines",))
    p = h.expect("grep -w cuts lines", "thought_log", "grep", "-w", "10", "Dana handed")
    check("grep -w 10 keeps 10 characters", p.stdout.strip() == "2026-10-20: Dana hande", p.stdout)
    h.expect("thought_log dates flags the Saturday stamp and the repeat", "thought_log", "dates",
             has=("SATURDAY 2026-10-17 (line 26)", "REPEAT  2026-11-06  date repeated at lines [42, 45]"),
             lacks=("ORDER", "OUTLIER", "NOTDATE"))

    # todo_items
    p = h.expect("todo list (open)", "todo_items", "list", has=("7f530439", "ecfa0889"), lacks=("9220bc85",))
    check("four open items", len(lines(p)) == 4, p.stdout)
    for src, n, phrase in (("done", 2, "Measure the south fence"), ("inline", 2, "second water tap"),
                           ("all", 8, "Supper: draft")):
        p = h.expect(f"todo list --source {src}", "todo_items", "list", "--source", src, has=(phrase,))
        check(f"{n} {src} items", len(lines(p)) == n, p.stdout)
    h.expect("todo list --unpinned", "todo_items", "list", "--unpinned", has=("ecfa0889",), lacks=("7f530439",))
    h.expect("todo show", "todo_items", "show", "7f530439", "00000000",
             has=("'notes': ['[[lumber-order]]']", "| delivered 10/30", "[no item 00000000]"))
    h.expect("todo pin", "todo_items", "pin", "ecfa0889", "--notes", "[[harvest-supper]]", has=("pinned ecfa0889",))
    h.expect("pinned item leaves --unpinned", "todo_items", "list", "--unpinned", lacks=("ecfa0889",))
    h.expect("todo unpin", "todo_items", "unpin", "ecfa0889", has=("unpinned ecfa0889",))
    h.expect("unpinned item is back in --unpinned", "todo_items", "list", "--unpinned", has=("ecfa0889",))
    h.expect("pin of an unknown id exits non-zero", "todo_items", "pin", "00000000", code="nonzero",
             has=("no item 00000000",), where="stderr")
    h.expect("stale: item pinned to a done note", "todo_items", "stale",
             has=("1 with the pin saying finished/dead, 1 whose note is done", "7f530439",
                  "[[lumber-order]] done 2026-10-30", "95a85496", "pin: done-elsewhere"))
    p = h.expect("orphans: none yet", "todo_items", "orphans")
    check("orphans prints nothing", not p.stdout.strip(), p.stdout)
    todo = h.read("todo.txt")
    h.write("todo.txt", todo.replace("Beds: buy cardboard", "[x] Beds: buy cardboard"))
    h.expect("orphans: ticking a pinned item orphans its pin", "todo_items", "orphans",
             has=("71529b90", "[[raised-beds]]"))
    h.write("todo.txt", todo)

    # note.py round-trips
    h.expect("note new", "note", "new", "projects", "bird-feeder", "Bird feeder", "A feeder by the gate.",
             "--state", "active", "--mentions", r"(?i)bird feeder", "--aliases", "feeder",
             "--part-of", "garden-season", has=("created",))
    meta = yaml.safe_load(h.read("concordance", "projects", "bird-feeder.md").split("---")[1])
    check("new note's front matter round-trips", meta == {"type": "project", "state": "active", "aliases": ["feeder"],
          "mentions": [r"(?i)bird feeder"], "part_of": "[[garden-season]]"}, meta)
    h.expect("note new on an existing slug exits non-zero", "note", "new", "projects", "bird-feeder", "X", "Y",
             code="nonzero", has=("already exists",), where="stderr")
    h.expect("note log", "note", "log", "bird-feeder", "2026-11-02: hung it on the gate post", has=("logged",))
    h.expect("note log (older date)", "note", "log", "bird-feeder", "2026-10-01: idea came up", has=("logged",))
    h.expect("note log duplicate is skipped", "note", "log", "bird-feeder", "2026-11-02: hung it on the gate post",
             has=("duplicate, skipped",))
    h.expect("note question", "note", "question", "bird-feeder", "Who refills it over winter?",
             has=("question added",))
    h.expect("note set", "note", "set", "bird-feeder", "base", "3", has=("set base",))
    h.expect("note set last_touched", "note", "set", "bird-feeder", "last_touched", "2026-11-04", has=("set last_touched",))
    p = h.expect("note show", "note", "show", "bird-feeder", has=("base: 3", "## Open questions",
                 "- Who refills it over winter?"))
    body = p.stdout
    check("log bullets are kept in date order", 0 < body.find("- 2026-10-01") < body.find("- 2026-11-02"), body)
    h.expect("explain uses the new note's settings", "priority", "explain", "bird-feeder",
             has=("base 3", "(since 2026-11-04)"))
    h.expect("note unset", "note", "unset", "bird-feeder", "base", has=("unset base",))
    h.expect("unset base falls back to the default", "priority", "explain", "bird-feeder", has=("base 1",))
    h.expect("note show after unset", "note", "show", "bird-feeder", lacks=("base:",))
    h.expect("links check still clean after edits", "links", "check", has=("17 notes, 0 broken links",))
    h.expect("note set raised-beds done", "note", "set", "raised-beds", "state", "done", has=("set state",))
    h.expect("knob lifts once [[raised-beds]] is done", "priority", "explain", "harvest-supper",
             has=("base 4",), lacks=("suppressed",))
    h.expect("note new (unlinked)", "note", "new", "concepts", "lone-idea", "Lone idea", "Nothing links here.",
             has=("created",))
    h.expect("links orphans lists a note with no links in or out", "links", "orphans", has=("lone-idea",))


# ---------------------------------------------------------------- run 2: add-ons off

def run_off(h):
    print("\n== Run 2: add-ons off ==")
    with open(h.path("harness.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    for key in ("thought_log", "todo"):
        cfg.pop(key)
    h.write("harness.yaml", yaml.safe_dump(cfg, sort_keys=False))
    h.expect("priority still ranks", "priority", has=("roof-permit", "compost-bins"))
    h.expect("aging now comes from the Log bullets", "priority", "explain", "compost-bins",
             has=("(since 2026-10-02)", "nudge overdue"))
    h.expect("note log works", "note", "log", "compost-bins", "2026-11-01: pallets arrived", has=("logged",))
    h.expect("a new Log bullet resets aging", "priority", "explain", "compost-bins", has=("(since 2026-11-01)",))
    h.expect("note question works", "note", "question", "compost-bins", "Who turns the heap?", has=("question added",))
    h.expect("links check: still no broken links, nothing ageless", "links", "check",
             has=("0 broken links", "0 ageless"))
    h.expect("review --stdout without the todo section", "review", "--stdout",
             has=("## Top of the stove", "## Questions for the garden committee", "Who turns the heap?"),
             lacks=("## Todo coverage",))
    p = h.expect("todo_items reports no items", "todo_items", "list", "--source", "all")
    check("todo list is empty and quiet", not p.stdout.strip() and not p.stderr.strip(), p)
    h.expect("todo stale finds nothing", "todo_items", "stale",
             has=("0 with the pin saying finished/dead, 0 whose note is done",))
    for cmd in (["stats"], ["dates"], ["grep", "x"]):
        h.expect(f"thought_log {cmd[0]} exits non-zero", "thought_log", *cmd, code="nonzero",
                 has=("No thought log configured",), where="stderr")


# ---------------------------------------------------------------- run 3: bad settings

def run_bad(h):
    print("\n== Run 3: bad settings ==")
    good = h.read("harness.yaml")
    cases = [("unknown key", good + "\ncolour: green\n", "unknown setting 'colour'"),
             ("wrong type", good.replace("suspect_weekdays: [5]", "suspect_weekdays: saturday"),
              "setting 'thought_log.suspect_weekdays' should be")]
    for label, text, want in cases:
        check(f"{label}: harness.yaml was changed", text != good)
        h.write("harness.yaml", text)
        for s in ALL_SCRIPTS[1:]:
            p = h.run(s, "-h")
            err = p.stderr.strip()
            ok = p.returncode != 0 and len(err.splitlines()) == 1 and want in err and "Traceback" not in p.stderr
            check(f"{label}: {s} stops with one line naming the key", ok, f"exit {p.returncode}: {p.stderr!r}")
    h.write("harness.yaml", good)


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    start = tree_hash(EXAMPLE)
    # TemporaryDirectory's cleanup also clears read-only bits (git object files from run 4),
    # which a plain rmtree(ignore_errors=True) leaves behind on Windows.
    with tempfile.TemporaryDirectory(prefix="harness-smoke-") as tmp:
        run_on(Harness(tmp, "on"))
        run_off(Harness(tmp, "off"))
        run_bad(Harness(tmp, "bad"))
        smoke_edges.run(tmp)
    check("temp folder removed", not os.path.exists(tmp), tmp)
    check("example/ was not modified", tree_hash(EXAMPLE) == start)
    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
