"""Run 4 of the smoke test: edge cases on one more copy of example/. Steps restore the files they
break (links, todo, thought log, settings); notes they add, log into or set, and the pin
records they append, are kept.

Date-proofing: "Just unblocked" sets done_on to the scripts' own today (read from review's
heading), and the git branch is dated 2001, so both hold on any day after the story.
"""
import json, os, re, shutil, subprocess

from smoke_lib import Harness, check, lines, skip

TODAY_RE = re.compile(r"^# Review (\d{4}-\d{2}-\d{2})$", re.M)


def run(tmp):
    print("\n== Run 4: edge cases ==")
    h = Harness(tmp, "edges")
    link_failures(h)
    dependency_loops(h)
    just_unblocked(h)
    todo_pins(h)
    todo_sources(h)
    odd_stamps(h)
    note_corners(h, tmp)
    count_log_entries(h)
    settings(h, tmp)
    review_repos(h, tmp)


def link_failures(h):
    h.expect("pristine review has no Link health section", "review", "--stdout", lacks=("## Link health",))
    dana = h.edit("concordance/people/dana.md", "Chairs the", "Chairs [[no-such-note]] and the")
    shutil.copy(h.path("concordance", "people", "sam.md"), h.path("concordance", "places", "sam.md"))
    h.expect("links check fails on a broken link and a duplicate slug", "links", "check", code=1,
             has=("Broken links:", "[[no-such-note]]  in concordance/people/dana.md", "Duplicate slugs:",
                  "  sam: ", "1 broken links", "1 duplicate slugs"))
    h.expect("review shows Link health when there is a problem", "review", "--stdout",
             has=("## Link health", "1 broken links", "links with no note yet: [[no-such-note]]"))
    h.write("concordance/people/dana.md", dana)
    os.remove(h.path("concordance", "places", "sam.md"))
    h.expect("links check is clean again after undoing", "links", "check", lacks=("Broken links:", "Duplicate"))


def dependency_loops(h):
    """Loops written by hand are reported; note.py refuses to write one; odd notes do not break either."""
    proj = lambda s: f"concordance/projects/{s}.md"
    # written by hand: a two-note loop between live notes plus a self-loop, at the same time. compost-bins
    # (first in slug order) leads into the roof loop, so Tarjan finds that loop before garden-season's:
    # the report must still be in slug order.
    permit = h.edit(proj("roof-permit"), "state: waiting\n", 'state: waiting\ndepends_on: ["[[tool-shed-roof]]"]\n')
    bins = h.edit(proj("compost-bins"), "state: waiting\n", 'state: waiting\ndepends_on: ["[[tool-shed-roof]]"]\n')
    season = h.edit("concordance/projects/garden-season.md", "state: active\n",
                    'state: active\ndepends_on: ["[[garden-season]]"]\n')
    loop, self_loop = "roof-permit -> tool-shed-roof -> roof-permit", "garden-season -> garden-season"
    h.expect("links check fails on two loops, one a self-loop", "links", "check", code=1,
             has=("depends_on loops (these notes wait on each other forever):", f"  {self_loop}\n  {loop}\n",
                  "2 depends_on loops"))
    h.expect("priority names both live loops", "priority", "--all",
             has=(f"depends_on loops (these notes wait on each other forever): {self_loop}; {loop}",))
    h.expect("review shows the loops under Link health", "review", "--stdout",
             has=("## Link health", "2 depends_on loops", f"- depends_on loop: {self_loop}", f"- depends_on loop: {loop}"))
    h.write(proj("roof-permit"), permit)
    h.write(proj("compost-bins"), bins)
    h.write("concordance/projects/garden-season.md", season)
    lumber = h.edit(proj("lumber-order"), "state: done\n", 'state: done\ndepends_on: ["[[raised-beds]]"]\n')
    h.expect("links check also fails on a loop through a done note", "links", "check", code=1,
             has=("  lumber-order -> raised-beds -> lumber-order",))
    h.expect("priority stays quiet about a loop through a done note", "priority", "--all", lacks=("loops",))
    h.write(proj("lumber-order"), lumber)
    dana = h.edit("concordance/people/dana.md", "type: person\n", "type: person\ndepends_on: 3\n")
    h.expect("links check copes with a depends_on that is not a list", "links", "check", has=("0 depends_on loops",))
    h.expect("review copes with it too", "review", "--stdout", lacks=("depends_on loop",))
    h.write("concordance/people/dana.md", dana.replace("type: person\n", 'type: person\ndepends_on: "[[dana]]"\n'))
    h.expect("a one-string depends_on counts as a one-item list", "links", "check", code=1, has=("  dana -> dana",))
    h.write("concordance/people/dana.md", dana)
    bins = h.edit(proj("compost-bins"), "state: waiting\n", 'state: waiting\ndepends_on: ""\n')
    h.expect("an empty-string depends_on on a tracked note names nothing", "priority", "--all",
             lacks=("depends_on names with no note yet",))
    h.write(proj("compost-bins"), bins)

    # note.py refuses to write one
    files = {s: h.read(proj(s)) for s in ("roof-permit", "tool-shed-roof", "lumber-order", "raised-beds")}
    h.expect("note.py refuses a note depending on itself", "note", "set", "roof-permit", "depends_on",
             '["[[roof-permit]]"]', code=1, where="stderr", has=("would make a loop", "roof-permit -> roof-permit"))
    h.expect("note.py refuses a two-note loop, named from the note typed in another case", "note", "set",
             "ROOF-PERMIT", "depends_on", '["[[compost-bins]]", "[[tool-shed-roof]]"]', code=1, where="stderr",
             has=(loop,))
    check("refused loops leave the notes unchanged", all(h.read(proj(s)) == t for s, t in files.items()))
    h.expect("a chain that is not a loop is fine", "note", "set", "lumber-order", "depends_on",
             '["[[tool-shed-roof]]"]', has=("set depends_on",))
    h.expect("note.py names a three-note loop from the note being set", "note", "set", "tool-shed-roof", "depends_on",
             '["[[raised-beds]]"]', code=1, where="stderr",
             has=("tool-shed-roof -> raised-beds -> lumber-order -> tool-shed-roof",))
    check("  ... and leaves tool-shed-roof unchanged", h.read(proj("tool-shed-roof")) == files["tool-shed-roof"])
    h.write(proj("lumber-order"), files["lumber-order"])

    # a loop already elsewhere does not hide the new one; a note with broken front matter does not block
    side = {"loop-a": '["[[loop-c]]", "[[loop-b]]"]', "loop-b": '["[[loop-a]]"]', "loop-c": None}
    for s, deps in side.items():
        h.write(f"concordance/concepts/{s}.md", "---\ntype: concept\n" +
                (f"depends_on: {deps}\n" if deps else "") + f"---\n# {s}\n")
    odd = {"half-written": b"---\ntype: concept\nstate: active\n# never closed\n",
           "bad-yaml": b"---\ntype: [unclosed\n---\n# bad\n",
           "bad-bytes": b"---\ntype: concept\n---\n# \xff\xfe not utf-8\n"}
    for s, data in odd.items():
        with open(h.path("concordance", "concepts", f"{s}.md"), "wb") as f:
            f.write(data)
    h.expect("note.py catches a new loop next to an old one", "note", "set", "loop-c", "depends_on", '["[[loop-b]]"]',
             code=1, where="stderr", has=("loop-c -> loop-b -> loop-a -> loop-c",))
    h.expect("note.py sets depends_on despite other notes' broken front matter, bad YAML or bad bytes", "note", "set",
             "roof-permit", "depends_on", '["[[lumber-order]]"]', has=("set depends_on",),
             lacks=("half-written", "bad-yaml", "bad-bytes"))
    for s in list(side) + list(odd):
        os.remove(h.path("concordance", "concepts", f"{s}.md"))
    h.write(proj("roof-permit"), files["roof-permit"])
    check("notes are back to how they were", all(h.read(proj(s)) == t for s, t in files.items()))
    h.expect("links check is clean again", "links", "check", has=("0 broken links", "0 depends_on loops"))


def just_unblocked(h):
    m = TODAY_RE.search(h.run("review", "--stdout").stdout)
    today = m.group(1) if m else "?"
    h.expect("set lumber-order done_on to today", "note", "set", "lumber-order", "done_on", today, has=("set done_on",))
    h.expect("review shows Just unblocked", "review", "--stdout",
             has=("## Just unblocked", f"- [[raised-beds]]: last dependency done {today} ([[lumber-order]])"))


def todo_pins(h):
    h.expect("pin with --context, --status done-elsewhere and --why", "todo_items", "pin", "ecfa0889",
             "--notes", "[[harvest-supper]]", "--context", "2026-11-10", "--status", "done-elsewhere",
             "--why", "flyer printed early", has=("pinned ecfa0889",))
    p = h.expect("stale -n 0 lists every finished pin", "todo_items", "stale", "-n", "0",
                 has=("ecfa0889", "pin: done-elsewhere", "flyer printed early", "evidence days: 2026-11-10"))
    m = re.match(r"(\d+) with the pin saying finished/dead", p.stdout)
    # The pristine example has one done-elsewhere pin; the pin made above is the second.
    check("stale counts the new pin as finished (2 in all)", m and int(m.group(1)) == 2, p.stdout)
    todo = h.edit("todo.txt", "Beds: buy cardboard", "[x] Beds: buy cardboard")
    h.expect("ticked item's pin is orphaned", "todo_items", "orphans", has=("71529b90",))
    h.expect("pin an orphaned id", "todo_items", "pin", "71529b90", "--notes", "[[raised-beds]]", "--status", "dead",
             "--why", "bought already", has=("pinned 71529b90",))
    with open(h.path("pins", "todo.jsonl"), encoding="utf-8") as f:
        last = json.loads(f.read().splitlines()[-1])
    check("orphan pin keeps the item's last known first line",
          last["first"] == "Beds: buy cardboard and mulch for the first layer" and last["status"] == "dead", last)
    p = h.expect("a dead pin is no longer an orphan", "todo_items", "orphans")
    check("orphans is empty again", not p.stdout.strip(), p.stdout)
    h.write("todo.txt", todo)


def todo_sources(h):
    todo = h.edit("todo.txt", "----- done above, open below -----\n", "")
    h.expect("missing split marker warns", "todo_items", "list", "--source", "all", where="stderr",
             has=("split marker '----- done above, open below -----' not found", "treating every item as done"))
    p = h.expect("missing split marker: nothing open", "todo_items", "list")
    check("no open items", not p.stdout.strip(), p.stdout)
    p = h.expect("missing split marker: every todo.txt item is done", "todo_items", "list", "--source", "done")
    check("6 done items", len(lines(p)) == 6, p.stdout)
    h.write("todo.txt", todo)
    yml = h.edit("harness.yaml", "path: todo.txt", "path: missing-todo.txt")
    h.expect("missing todo.path warns", "todo_items", "list", where="stderr",
             has=("todo.path is set but", "missing-todo.txt does not exist"))
    h.expect("orphans refuses while a source is missing", "todo_items", "orphans", code="nonzero", where="stderr",
             has=("orphans: cannot tell, because a configured source is missing",))
    h.write("harness.yaml", yml)


def odd_stamps(h):
    log = h.edit("thinking.txt", "11/09/2026\n", "11/09/2026\n13/01/2026\n")
    p = h.expect("dates lists a stamp that is not a real date", "thought_log", "dates",
                 has=("NOTDATE line 49  '13/01/2026' is not a real date read as mdy", "REPEAT  2026-11-06"))
    check("...and warns once on stderr", "1 date stamp(s) are not real dates" in p.stderr, p.stderr)
    h.expect("a bad stamp stays ordinary text", "thought_log", "show", "2026-11-09", has=("13/01/2026",))
    h.write("thinking.txt", log)


def note_corners(h, tmp):
    h.expect("note new with a singular kind and --type", "note", "new", "place", "tool-library", "Tool library",
             "Shared tools at the community hall.", "--type", "workshop", has=("created",))
    check("singular kind maps to its folder, --type wins",
          h.read("concordance", "places", "tool-library.md").startswith("---\ntype: workshop\n"))
    other = os.path.join(tmp, "elsewhere")
    os.makedirs(other)
    h.expect("note --root writes outside the harness", "note", "--root", other, "new", "concepts", "loose-thread",
             "Loose thread", "Kept outside the harness.", has=("created",))
    check("--root note is in the other folder", os.path.isfile(os.path.join(other, "concepts", "loose-thread.md")))
    h.expect("--root show finds it", "note", "--root", other, "show", "loose-thread", has=("# Loose thread",))
    h.expect("without --root the harness does not have it", "note", "show", "loose-thread", code="nonzero",
             where="stderr", has=("no note 'loose-thread'",))
    h.expect("log into a note with no ## Log section", "note", "log", "dana", "2026-11-09: took the minutes",
             has=("logged (new ## Log section)",))
    h.expect("multi-line log text", "note", "log", "dana", "2026-11-10: sent the minutes round\nwith the budget",
             has=("logged ->",))
    h.expect("multi-line text is indented under its bullet", "note", "show", "dana", has=(
        "## Log\n- 2026-11-09: took the minutes\n- 2026-11-10: sent the minutes round\n  with the budget\n",))
    link_values(h)


def link_values(h):
    """depends_on / part_of values that priority.py or links.py would misread are refused, file untouched."""
    note = ("concordance", "projects", "tool-shed-roof.md")
    before = h.read(*note)
    dep_err, part_err = "depends_on must be a list of [[slug]] links", "part_of must be one [[slug]] link"
    refused = [  # (what, key, value, must have, must lack)
        ("depends_on with stripped quotes (PowerShell 5.1)", "depends_on", "[[[roof-permit]]]",
         (dep_err, "PowerShell 5.1", '\'[\\"[[other-note]]\\"]\''), ()),
        ("depends_on as a plain string", "depends_on", '"[[roof-permit]]"', (dep_err,), ("PowerShell",)),
        ("depends_on with a mapping item", "depends_on", "[{a: 1}]", (dep_err,), ()),
        ("depends_on with a bare slug", "depends_on", "[roof-permit]", (dep_err,), ()),
        ("depends_on with spaces inside the brackets", "depends_on", '["[[ roof-permit ]]"]', (dep_err,), ()),
        ("depends_on with a label", "depends_on", '["[[roof-permit|the permit]]"]', (dep_err,), ()),
        ("empty depends_on", "depends_on", "~", ("note.py unset tool-shed-roof depends_on",), ()),
        ("part_of as a list", "part_of", '["[[garden-season]]"]', (part_err,), ("PowerShell",)),
        ("part_of as a bare slug", "part_of", "garden-season", (part_err,), ()),
        ("part_of with stripped quotes (PowerShell 5.1)", "part_of", "[[garden-season]]",
         (part_err, "PowerShell 5.1", '\'\\"[[parent-note]]\\"\''), ()),
    ]
    for what, key, value, has, lacks in refused:
        h.expect(f"set {what} is refused", "note", "set", "tool-shed-roof", key, value, code=1, where="stderr",
                 has=has, lacks=lacks + ("warning",))
    check("refused sets leave the note byte-identical", h.read(*note) == before)
    h.expect("refused set on a missing note names the note, no link warning", "note", "set", "no-such", "depends_on",
             '["[[nope]]"]', code=1, where="stderr", has=("no note 'no-such'",), lacks=("warning",))

    p = h.expect("set depends_on in single-quoted YAML is accepted", "note", "set", "tool-shed-roof", "depends_on",
                 "[' [[roof-permit]] ']", has=("set depends_on",))
    check("  ... with no warning", p.stderr == "", p.stderr)
    h.expect("set depends_on to an empty list is accepted", "note", "set", "tool-shed-roof", "depends_on", "[]",
             has=("set depends_on",))
    p = h.expect("set depends_on naming no note warns", "note", "set", "tool-shed-roof", "depends_on",
                 '["[[roof-permit]]", "[[no-such-note]]", "[[no-such-note]]"]', where="stderr",
                 has=("[[no-such-note]], which has no note yet",))
    check("  ... once per slug", p.stderr.count("no-such-note") == 1, p.stderr)
    check("  ... and writes it", 'depends_on: ["[[roof-permit]]", "[[no-such-note]]", "[[no-such-note]]"]'
          in h.read(*note))
    h.expect("set depends_on with the wrong case warns", "note", "set", "tool-shed-roof", "depends_on",
             '["[[Roof-Permit]]"]', where="stderr", has=("[[Roof-Permit]], which differs in case from [[roof-permit]]",))
    h.expect("set part_of naming no note warns", "note", "set", "tool-shed-roof", "part_of", '"[[no-such-parent]]"',
             where="stderr", has=("part_of names [[no-such-parent]], which has no note yet",))
    p = h.expect("set part_of back", "note", "set", "tool-shed-roof", "part_of", '"[[garden-season]]"',
                 has=("set part_of",))
    check("  ... with no warning", p.stderr == "", p.stderr)
    h.expect("set depends_on back", "note", "set", "tool-shed-roof", "depends_on", '["[[roof-permit]]"]',
             has=("set depends_on",))
    check("note is back to how it was", h.read(*note) == before)

    h.expect("new --part-of with two words is refused", "note", "new", "projects", "odd-part", "Odd", "Odd one.",
             "--part-of", "two words", code=1, where="stderr", has=("--part-of must be a slug",))
    check("  ... and creates no file", not os.path.exists(h.path("concordance", "projects", "odd-part.md")))
    h.expect("new --part-of with the wrong case warns", "note", "new", "projects", "odd-part", "Odd", "Odd one.",
             "--part-of", "[[Garden-Season]]", where="stderr", has=("differs in case from [[garden-season]]",))
    os.remove(h.path("concordance", "projects", "odd-part.md"))


def count_log_entries(h):
    h.expect("log a bullet newer than the last mention", "note", "log", "compost-bins", "2026-11-01: pallets arrived",
             has=("logged",))
    h.expect("with a thought log, Log bullets do not count by default", "priority", "explain", "compost-bins",
             has=("(since 2026-10-12)",))
    yml = h.read("harness.yaml")
    h.write("harness.yaml", yml + "\naging:\n  count_log_entries: true\n")
    h.expect("aging.count_log_entries: true counts them", "priority", "explain", "compost-bins",
             has=("(since 2026-11-01)",))
    h.write("harness.yaml", yml)


def settings(h, tmp):
    yml = h.read("harness.yaml")
    elsewhere = os.path.join(tmp, "settings", "other.yaml")
    os.makedirs(os.path.dirname(elsewhere))
    with open(elsewhere, "w", encoding="utf-8") as f:
        f.write(yml.replace("owner: the garden committee", "owner: the plot holders"))
    env = {"HARNESS_CONFIG": elsewhere}
    h.expect("HARNESS_CONFIG: settings from another folder", "review", "--stdout", env=env,
             has=("## Questions for the plot holders",))
    h.expect("HARNESS_CONFIG: relative paths still taken from the root", "thought_log", "stats", env=env,
             has=("segments: 16",))
    p = h.run("priority", env={"HARNESS_CONFIG": elsewhere + ".missing"})
    check("HARNESS_CONFIG at a missing file: one line, non-zero",
          p.returncode != 0 and "does not exist" in p.stderr and len(p.stderr.strip().splitlines()) == 1, p.stderr)
    h.edit("harness.yaml", "owner: the garden committee\n", "owner:\nconcordance:\n")
    h.expect("`key:` left empty means the default", "review", "--stdout", has=("## Questions for the owner",))
    h.expect("empty concordance: still finds the notes", "links", "check", has=("17 notes, 0 broken links",))  # 16 + tool-library
    h.write("harness.yaml", "owner: [unclosed\n")
    for s in ("priority", "todo_items"):
        p = h.run(s)
        check(f"invalid YAML: {s} stops naming the file, no traceback",
              p.returncode != 0 and "harness.yaml: not valid YAML" in p.stderr.splitlines()[0]
              and "Traceback" not in p.stderr, p.stderr)
    h.write("harness.yaml", yml)


def review_repos(h, tmp):
    if not shutil.which("git"):
        skip("review.repos", "git is not installed")
        return
    repo = os.path.join(tmp, "plot-plans-repo")
    stamp = "2001-02-03T12:00:00"
    env = dict(os.environ, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp, GIT_AUTHOR_NAME="Tester",
               GIT_AUTHOR_EMAIL="tester@example.invalid", GIT_COMMITTER_NAME="Tester",
               GIT_COMMITTER_EMAIL="tester@example.invalid",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")  # the fixture ignores the user's git config
    steps = [["init", "-q", repo], ["-C", repo, "checkout", "-q", "-b", "master"],
             ["-C", repo, "commit", "-q", "--allow-empty", "-m", "start"], ["-C", repo, "checkout", "-q", "-b", "side"],
             ["-C", repo, "commit", "-q", "--allow-empty", "-m", "side work"], ["-C", repo, "checkout", "-q", "master"]]
    for args in steps:
        p = subprocess.run(["git", *args], capture_output=True, text=True, env=env)
        if not check(f"git {' '.join(args[2:] if args[0] == '-C' else args[:1])}", p.returncode == 0, p.stderr):
            return
    yml = h.read("harness.yaml")
    h.write("harness.yaml", yml + f"\nreview:\n  repos:\n    - path: '{repo.replace(os.sep, '/')}'\n"
                                  "      name: plot-plans\n      main: master\n      stale_days: 30\n")
    h.expect("review.repos lists the old side branch", "review", "--stdout",
             has=("## Long-lived branches (plot-plans)", "- side: 1 ahead, 0 behind; last commit 2001-02-03"))
    h.write("harness.yaml", yml)
