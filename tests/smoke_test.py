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
         null and invalid settings, missing todo sources, odd thought-log stamps, the thought log
         and todo file as .docx and .odt (built at test time), note.py corners
  Run 5  query.py: every operator, OR, negation, groups, dates, errors; added-after/-before on a
         git repo made from the copy (two commits with set committer dates)
"""
import copy, datetime, json, os, re, shutil, subprocess, sys, tempfile

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # python -I leaves this folder off sys.path
sys.dont_write_bytecode = True  # no tests/__pycache__ from importing the helpers
from smoke_lib import ALL_SCRIPTS, EXAMPLE, RESULTS, Harness, check, lines, ranked, skip, tree_hash
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
    h.expect("todo pin --status without --notes", "todo_items", "pin", "ecfa0889", "--status", "done-elsewhere",
             has=("pinned ecfa0889",))
    h.expect("closing a pin keeps its notes", "todo_items", "show", "ecfa0889",
             has=("'notes': ['[[harvest-supper]]']", "'status': 'done-elsewhere'"))
    h.expect("todo pin with a bare --notes", "todo_items", "pin", "ecfa0889", "--notes", has=("pinned ecfa0889",))
    h.expect("a bare --notes clears the notes", "todo_items", "show", "ecfa0889", has=("'notes': []",))
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


# ---------------------------------------------------------------- run 5: query

def run_query(h, tmp):
    print("\n== Run 5: query ==")
    q = lambda *args, **kw: h.expect(f"query {' '.join(args)}", "query", *args, **kw)
    rows = lambda p: [l.split()[0] for l in p.stdout.splitlines()[:-1] if l[:1].strip()]
    # a Log with a continuation line, a date range, an undated bullet and a trailing Todos: line
    h.edit("concordance/projects/rain-barrel.md", "- 2026-10-17: parked until after the autumn rains.\n",
           "- 2026-10-17: parked until after the autumn rains.\n  The downpipe kit waits in the shed.\n"
           "- 2026-10-18..10-24: rain gauge read every evening.\n- no date: a third barrel came up.\n"
           "Todos: none yet\n\nA closing remark, not an entry.\n")
    start = tree_hash(h.root)
    p = q("note:rain-barrel", has=("4 entries in 1 note",), lacks=("Todos", "closing remark"))
    check("query: continuation indented beneath its entry, undated entry last",
          p.stdout.splitlines()[1:5] == ["rain-barrel  2026-10-17: parked until after the autumn rains.",
                                         "             The downpipe kit waits in the shed.",
                                         "rain-barrel  2026-10-18..10-24: rain gauge read every evening.",
                                         "rain-barrel  no date: a third barrel came up."], p.stdout)
    q("note:[[rain-barrel]] downpipe", has=("1 entry in 1 note", "parked until"))  # continuations are searched
    q("note:rain-barrel after:2026-10-18", has=("1 entry", "rain gauge"))           # a range: its first date
    q("note:rain-barrel before:2026-10-18", has=("2 entries", "parked until"), lacks=("no date",))
    q("note:rain-barrel -after:2026-01-01", has=("1 entry", "no date"))             # undated: never after:
    # date edges: after: is inclusive, before: exclusive
    q("after:2026-11-09", has=("1 entry", "garden-season"))
    p = q("before:2026-09-14")
    check("query before: is exclusive", rows(p) == ["garden-season"], p.stdout)
    # note operators
    p = q("kind:projects", "after:2026-11-06")
    check("query kind: folder name form", rows(p) == ["greenhouse", "raised-beds", "garden-season"], p.stdout)
    q("kind:person", has=("sam ",), lacks=("garden-season",))
    p = q("state:waiting", "--notes")
    check("query state: with --notes", rows(p) == ["compost-bins", "roof-permit"] and "2 notes, 4 entries"
          in p.stdout and "waiting" in p.stdout.splitlines()[0], p.stdout)
    p = q("part_of:raised-beds", "--notes")
    check("query part_of: the note itself and its children", rows(p) == ["lumber-order", "raised-beds"], p.stdout)
    p = q("part_of:garden-season", "--notes")
    check("query part_of: transitive (lumber-order sits under raised-beds)", "lumber-order" in rows(p)
          and "roof-permit" in rows(p) and "sam" not in rows(p), p.stdout)
    p = q("links:dana", "--notes")
    check("query links:", rows(p) == ["garden-season", "harvest-supper", "roof-permit"], p.stdout)
    q("waiting_on:[[sam]]", has=("2 entries in 1 note", "compost-bins"))
    q("depends_on:lumber-order", has=("raised-beds",), lacks=("lumber-order ",))
    q("base:0.5", has=("2 entries", "roof-permit"))
    q("no_such_key:x", code=1, has=("0 entries",))
    # entry operators, OR, negation, groups
    q('"permit desk"', has=("1 entry", "submitted at the city"))
    q("re:pallets?", "-note:sam", has=("compost-bins",), lacks=("sam ",))
    p = q("pallets OR lumber OR tarp")
    check("query OR chain", rows(p) == ["compost-bins", "sam", "raised-beds", "tool-shed-roof"], p.stdout)
    p = q("state:active", "-(note:garden-season OR kind:person)", "after:2026-11-01")
    check("query negated group", rows(p) == ["raised-beds"], p.stdout)
    p = q("(lumber OR tarp) after:2026-11-01")
    check("query group with OR, then AND", rows(p) == ["tool-shed-roof"], p.stdout)
    q("permit", "--limit", "1", has=("2 entries in 2 notes (showing 1)",))
    p = q("--json", "note:roof-permit", "desk")
    try:
        data = json.loads(p.stdout)
    except ValueError:
        data = None
    check("query --json", data and data[0]["slug"] == "roof-permit" and data[0]["kind"] == "project"
          and data[0]["path"] == "concordance/projects/roof-permit.md" and data[0]["date"] == "2026-10-20"
          and "added" in data[0], p.stdout)
    q("nothing-says-this", code=1, has=("0 entries",))
    for bad, why in (("a OR", "OR needs a term"), ("(a", "unclosed '('"), ("a)", "unmatched ')'"),
                     ("after:2026-1-1", "bad date"), ("re:[", "bad regex"), ('"open', "unclosed quote"),
                     ("state:", "nothing after"), ("-AND x", "unexpected AND (AND needs a term on each side)"),
                     ("-OR x", "unexpected OR (OR needs a term on each side)")):
        q(bad, code=2, has=(why,), where="stderr")
    p = q("kind:person", "--notes", "--json")
    check("query --notes --json: a note with no state has state null",
          json.loads(p.stdout or "[]") == [{"slug": "sam", "kind": "person", "state": None, "entries": 1}], p.stdout)
    q("kind:person", "--notes", has=("sam  person   -         1",))
    q("--note", "sam", code=2, has=("unknown option --note",), where="stderr")
    q("--", "--note", code=1, has=("0 entries",))
    # a part_of loop still answers
    old = h.edit("concordance/projects/garden-season.md", "state: active\n",
                 'state: active\npart_of: "[[raised-beds]]"\n')
    q("part_of:lumber-order", "--notes", has=("1 note,",))
    q("part_of:raised-beds", "after:2026-11-09", has=("garden-season",))
    h.write("concordance/projects/garden-season.md", old)
    # added-after: / added-before: need git
    ceiling = {"GIT_CEILING_DIRECTORIES": tmp}  # never find a repo above the temp folder
    q("added-after:2026-01-01", code=2, has=("git work tree",), where="stderr", env=ceiling)
    check("query wrote nothing", tree_hash(h.root) == start)
    git_added(h, q, rows, ceiling)


def git_added(h, q, rows, ceiling):
    if not shutil.which("git"):
        skip("query added-after:", "git is not installed")
        return
    env = dict(ceiling, GIT_AUTHOR_NAME="Tester", GIT_AUTHOR_EMAIL="tester@example.invalid",
               GIT_COMMITTER_NAME="Tester", GIT_COMMITTER_EMAIL="tester@example.invalid",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")  # the fixture ignores the user's git config

    def git(stamp, *args):
        e = dict(os.environ, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp, **env)
        p = subprocess.run(["git", "-C", h.root, *args], capture_output=True, text=True, env=e)
        return check(f"query fixture: git {args[0]}", p.returncode == 0, p.stderr)

    # The second commit lands 30 seconds after local midnight (the boundary of added-after:2026-11-05).
    # Commits dated earlier than their parent are tested on a repo of their own (skewed_history), since
    # one would turn the prefilter off for every later check here.
    old, new = "2026-10-01T12:00:00", "2026-11-05T00:00:30"
    if not (git(old, "init", "-q") and git(old, "add", "-A") and git(old, "commit", "-q", "-m", "story")):
        return
    h.run("note", "log", "compost-bins", "2026-11-05: pallets stacked by the shed.")
    if not git(new, "commit", "-q", "-am", "pallets"):
        return
    h.run("note", "log", "compost-bins", "2026-11-07: first bay filled.")  # not committed
    h.write("concordance/projects/bird-bath.md", "---\ntype: project\n---\n# Bird bath\n\n## Log\n"
                                                 "- 2026-11-07: a neighbour offered one.\n")  # untracked
    p = q("added-after:2026-11-01", env=env)
    check("query added-after: the later commit, an uncommitted line and an untracked note",
          rows(p) == ["compost-bins", "bird-bath", "compost-bins"] and "pallets stacked" in p.stdout, p.stdout)
    p = q("added-before:2026-11-01", "note:compost-bins", env=env)
    check("query added-before: only lines from the first commit",
          rows(p) == ["compost-bins", "compost-bins"] and "pallets stacked" not in p.stdout, p.stdout)
    p = q("added-after:2026-10-01", "-added-after:2026-10-02", "kind:person", env=env)
    check("query added-after: is inclusive, by committer date", rows(p) == ["sam"] and "pallets" in p.stdout, p.stdout)
    q("added-after:2026-11-05", "pallets", env=env, has=("1 entry",))     # just after local midnight
    q("added-before:2026-11-05", "pallets", env=env, has=("2 entries",), lacks=("stacked",))
    # the prefilter gives exactly what blaming every file gives
    for args in (["added-after:2026-11-05"], ["added-after:2026-11-06"], ["added-before:2026-11-05"],
                 ["added-before:2026-09-02"], ["added-after:2026-09-01", "-added-after:2026-10-02"],
                 ["added-after:2026-10-02", "OR", "state:dead"], ["--json", "added-after:2026-11-01"],
                 ["--json", "added-before:2026-10-02", "kind:person"]):
        fast, slow = h.run("query", *args, env=env), h.run("query", *args, env=dict(env, QUERY_BLAME_ALL="1"))
        check(f"query {' '.join(args)}: prefilter matches blaming every file",
              (fast.returncode, fast.stdout) == (slow.returncode, slow.stdout) and fast.stdout,
              f"{fast.stdout!r}\n      {slow.stdout!r}")
    # dates with no local midnight the platform can represent (before 1970 on Windows, year 9999)
    same_as_blame_all(h, "dates outside the clock's range", env, queries=(
        ["added-after:1960-01-01"], ["added-before:1960-01-01"], ["added-after:0001-01-01", "note:sam"],
        ["added-before:9999-12-31", "note:sam"], ["-added-after:9999-12-31", "kind:person"]))
    q("added-after:1960-01-01", "note:sam", env=env, has=("1 entry",))
    q("added-before:9999-12-31", "note:sam", env=env, has=("1 entry",))
    p = q("--json", "note:compost-bins", "pallets OR filled", env=env)
    check("query --json leaves added null without an added-* term or --added",
          [d["added"] for d in json.loads(p.stdout or "[]")] == [None] * 3, p.stdout)
    p = q("--json", "--added", "note:compost-bins", "pallets OR filled", env=env)
    try:
        data = json.loads(p.stdout)
    except ValueError:
        data = []
    check("query --json --added fills added from git",
          [d["added"] for d in data] == ["2026-10-01", "2026-11-05", datetime.date.today().isoformat()], p.stdout)
    prefilter_corners(h, git, env, ceiling["GIT_CEILING_DIRECTORIES"])
    # git blame failing: an added-* filter that needs it stops; --added just leaves the value null
    if not git(old, "config", "blame.ignoreRevsFile", "no-such-file"):
        return
    q("added-after:2026-11-01", env=env, code=2, has=("git blame failed",), where="stderr")
    p = q("--json", "--added", "note:sam", env=env)
    check("query --json --added: a failed blame gives null, not an error",
          [d["added"] for d in json.loads(p.stdout or "[]")] == [None], p.stdout)
    skewed_history(env, ceiling["GIT_CEILING_DIRECTORIES"])


def skewed_history(env, tmp):
    """Commits dated earlier than their parent (a rebase, a wrong clock), on a repo of their own."""
    h = Harness(tmp, "query-skew")

    def git(stamp, *args):
        e = dict(os.environ, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp, **env)
        p = subprocess.run(["git", "-C", h.root, *args], capture_output=True, text=True, env=e)
        return check(f"query skew fixture: git {args[0]}", p.returncode == 0, p.stderr)

    # c1 (2020) holds the notes; c2 (2026-11-20) adds a line; c3, child of c2 but dated 2020-06-01,
    # renames the note, so no commit after the cutoff lists the note's current name.
    first = "2020-01-01T12:00:00"
    if not (git(first, "init", "-q") and git(first, "add", "-A") and git(first, "commit", "-q", "-m", "c1")):
        return
    h.run("note", "log", "compost-bins", "2026-11-02: a line added late.")
    if not (git("2026-11-20T12:00:00", "commit", "-q", "-am", "c2")
            and git("2020-06-01T12:00:00", "mv", "concordance/projects/compost-bins.md",
                    "concordance/projects/compost-heap.md")
            and git("2020-06-01T12:00:00", "commit", "-q", "-m", "c3")):
        return
    # c4, dated earlier still than its parent, adds a line to another note
    h.run("note", "log", "sam", "2026-09-01: lent a wheelbarrow.")
    if not git("2019-06-01T12:00:00", "commit", "-q", "-am", "c4"):
        return
    p = h.run("query", "added-after:2026-11-01", env=env)
    check("query: a rename dated before its parent does not hide a recent line",
          "a line added late" in p.stdout and "1 entry" in p.stdout, p.stdout + p.stderr)
    p = h.run("query", "added-before:2020-01-01", env=env)
    check("query: a commit dated before its parent", "wheelbarrow" in p.stdout and "1 entry" in p.stdout,
          p.stdout + p.stderr)
    same_as_blame_all(h, "commits dated before their parent", env, queries=(
        ["added-after:2026-11-01"], ["-added-after:2026-11-01", "note:compost-heap"], ["added-before:2020-01-01"],
        ["added-after:2020-01-02"], ["--json", "added-after:2020-01-02"]))


PREFILTER_QUERIES = (["added-after:2026-11-05"], ["added-before:2026-11-05"], ["-added-after:2026-10-02"],
                     ["added-after:2026-10-01", "-added-after:2026-11-06"], ["--json", "added-after:2026-11-01"])


def same_as_blame_all(h, label, env, queries=PREFILTER_QUERIES):
    """Each query gives the same answer with the prefilter as with QUERY_BLAME_ALL=1."""
    for args in queries:
        fast, slow = h.run("query", *args, env=env), h.run("query", *args, env=dict(env, QUERY_BLAME_ALL="1"))
        check(f"query {' '.join(args)}: prefilter matches blaming every file ({label})",
              (fast.returncode, fast.stdout) == (slow.returncode, slow.stdout) and fast.stdout
              and "Traceback" not in fast.stderr + slow.stderr, f"{fast.stdout!r}\n      {slow.stdout!r}")


def prefilter_corners(h, git, env, tmp):
    """Cases where git's view of the files differs from the notes' paths or from git status."""
    stamp = "2026-11-20T12:00:00"
    # the harness reached through a link: git reports the resolved folder, the notes do not
    link = os.path.join(tmp, "query-link")
    if os.name == "nt":
        made = subprocess.run(["cmd", "/c", "mklink", "/J", link, h.root], capture_output=True).returncode == 0
    else:
        try:
            os.symlink(h.root, link, target_is_directory=True)
            made = True
        except OSError:
            made = False
    if made:
        via = copy.copy(h)
        via.root = link
        try:
            same_as_blame_all(via, "through a link", env)
            p = via.run("query", "-added-after:2026-10-02", env=env)
            check("query through a link: old lines stay old", "offered pallets" in p.stdout, p.stdout)
        finally:
            os.rmdir(link) if os.name == "nt" else os.remove(link)  # the link only, not its target
    else:
        skip("query through a link", "could not make a junction or symlink")
    # a note taken out of the index (git rm --cached): touched since the cutoff, no longer tracked
    if git(stamp, "rm", "-q", "--cached", "concordance/projects/greenhouse.md"):
        same_as_blame_all(h, "untracked after rm --cached", env)
        p = h.run("query", "note:greenhouse", "added-before:2026-11-05", env=env)
        check("query: a note taken out of the index is not dropped", "raised it again" in p.stdout, p.stdout)
        git(stamp, "reset", "-q", "HEAD", "--", "concordance/projects/greenhouse.md")
    # local edits git status cannot see: assume-unchanged and skip-worktree
    for flag, slug in (("--assume-unchanged", "rain-barrel"), ("--skip-worktree", "beehive")):
        if git(stamp, "update-index", flag, f"concordance/projects/{slug}.md"):
            h.run("note", "log", slug, f"2026-11-08: edited under {flag}.")
            same_as_blame_all(h, flag, env)
            q = h.run("query", f"note:{slug}", f"added-after:{datetime.date.today()}", env=env)
            check(f"query: a line edited under {flag} counts as added today", f"under {flag}" in q.stdout, q.stdout)
            git(stamp, "update-index", flag.replace("--", "--no-", 1), f"concordance/projects/{slug}.md")
    # every note touched (new timestamps, same content): the query must not refresh .git/index
    later = datetime.datetime.now().timestamp() + 60
    for d, _, files in os.walk(h.path("concordance")):
        for f in files:
            os.utime(os.path.join(d, f), (later, later))
    index = h.path(".git", "index")
    with open(index, "rb") as f:
        before = (f.read(), os.stat(index).st_mtime_ns)
    same_as_blame_all(h, "notes touched, content unchanged", env)
    with open(index, "rb") as f:
        after = (f.read(), os.stat(index).st_mtime_ns)
    check("query leaves .git/index alone (bytes and mtime)", after == before)
    # the fixture refreshes it itself, so the touched notes stop looking changed (and being blamed),
    # which would hide what the checks below are after
    git(stamp, "update-index", "-q", "--refresh")
    # log.showRoot false in the repo's own config: the root commit's files must still count
    if git(stamp, "config", "log.showRoot", "false"):
        same_as_blame_all(h, "log.showRoot false", env, queries=(
            ["added-after:2026-10-01"], ["added-before:2026-10-02"], ["--json", "added-after:2026-10-01", "kind:person"]))
        p = h.run("query", "added-after:2026-10-01", "note:sam", env=env)
        check("query: lines from the root commit count with log.showRoot false", "offered pallets" in p.stdout, p.stdout)
    # a case-only rename committed in git, then undone on a case-insensitive disk
    ignorecase = subprocess.run(["git", "-C", h.root, "config", "--bool", "core.ignorecase"],
                                capture_output=True, text=True).stdout.strip() == "true"
    if not ignorecase:
        skip("query: note named in another case than in git", "core.ignorecase is not true here")
        return
    old, new = "concordance/projects/harvest-supper.md", "concordance/projects/harvest-Supper.md"
    if git(stamp, "mv", old, new) and git(stamp, "commit", "-q", "-m", "case"):
        os.rename(h.path(*new.split("/")), h.path(*old.split("/")))
        same_as_blame_all(h, "case differs from the index", env)
        p = h.run("query", "note:harvest-supper", "-added-after:2026-11-01", env=env)
        check("query: a note named in another case than in git keeps its old lines old",
              "suggested it" in p.stdout and "2 entries" in p.stdout, p.stdout)


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
        run_query(Harness(tmp, "query"), tmp)
    check("temp folder removed", not os.path.exists(tmp), tmp)
    check("example/ was not modified", tree_hash(EXAMPLE) == start)
    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
