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
