"""Rank concordance notes so background work surfaces before it is forgotten.

Every .md file under concordance/ is a note; its name (without .md) is its slug,
referenced elsewhere as [[slug]]. A note takes part in ranking when its front
matter has a `state`. All numbers are optional; defaults are below.

  ---
  type: project
  state: active | waiting | parked | blocked | proposed | dormant | done | dead
  base: 3                    # how much it matters on its own
  age_rate: 0.02             # priority gained per day since last touched (parked: 0.005)
  last_touched: 2026-03-11   # optional; see "last touched" below
  mentions: ['(?i)\bgarden\b']  # regexes that count as "touched" in the thought log (add-on)
  nudge_every: 14            # days; once exceeded, adds 1 per extra nudge period
  depends_on: ["[[other]]"]  # this waits until every dependency is done (loops are reported)
  done_on: 2026-09-01        # when state became done (gives dependents an unblock bump)
  knobs:                     # push down (positive) or lift (negative)
    - amount: 4
      set: 2026-10-09
      half_life: 60          # days; omit for no decay
      until: 2027-01-01      # or "[[slug]] done"; omit for unconditional
      why: told to wait for the new boards
  ---

score = base + age_rate*days_untouched + nudge_overdue + unblock_bump - knobs
Pressure: a dependency is ranked at least PRESSURE x the score of whatever waits on it,
so an unimportant blocker rises when something important is stuck behind it.

Last touched is the newest of: `last_touched`; the latest thought-log day matching `mentions`
(when the thought log add-on is configured); and the newest dated "## Log" bullet (when
aging.count_log_entries is on, the default without a thought log).

The default list leaves out proposed/dormant notes (until confirmed; they do not age either) and
notes still waiting on a live dependency; --all shows them. When the owner confirms a proposed or
dormant note (moves it to a ranked state), set last_touched to the confirmation date so aging
starts from there.

Usage:
  python priority.py [-n 15] [--all]     ranked list with reasons
  python priority.py explain SLUG
"""
import argparse, datetime, glob, math, os, re, sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import thought_log

HARNESS = config.ROOT
CONCORDANCE = config.CONCORDANCE
LINK = re.compile(r"\[\[([^\]|#]+)")
LOG_BULLET = re.compile(r"^- (\d{4}-\d{2}-\d{2})", re.M)
DEFAULTS = {"base": 1.0, "age_rate": 0.02}
STATE_AGE_RATE = {"parked": 0.005}  # parked on purpose: still ages, but slowly
PRESSURE = 0.9         # share of a dependent's score a blocker inherits
UNBLOCK_BUMP = 3.0     # added when the last dependency finishes, halves every 14 days
UNBLOCK_HALF_LIFE = 14
CLOSED = {"done", "dead"}
UNRANKED = {"proposed", "dormant"} | CLOSED
TODAY = datetime.date.today()


def as_date(v):
    if isinstance(v, datetime.date):
        return v
    return datetime.date.fromisoformat(str(v)) if v else None


def note_paths():
    """Note files. Names starting uppercase or '_' (e.g. INDEX.md) are generated, not notes."""
    return [p for p in glob.glob(os.path.join(CONCORDANCE, "**", "*.md"), recursive=True)
            if not (os.path.basename(p)[0].isupper() or os.path.basename(p).startswith("_"))]


def front_matter(text):
    """(meta, body) of a note's text, as every script reads it. Raises yaml.YAMLError on bad YAML;
    meta may be a non-mapping when the front matter is not a mapping."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    return yaml.safe_load(text[3:end]) or {}, text[end + 4:]


def load_notes(errors=None):
    """errors: optional list; when given, unreadable notes are skipped and (path, message) appended."""
    notes = {}
    for path in note_paths():
        slug = os.path.splitext(os.path.basename(path))[0]
        try:
            text = open(path, encoding="utf-8").read()
            meta, body = front_matter(text)
            if errors is not None and not isinstance(meta, dict):
                raise ValueError("front matter is not a mapping")
        except Exception as e:
            if errors is None:
                raise
            errors.append((path, f"{type(e).__name__}: {e}".splitlines()[0]))
            continue
        notes[slug] = {"slug": slug, "path": path, "meta": meta, "body": body,
                       "links": set(LINK.findall(text)) - {slug}}
    return notes


def slugs(values):
    """Slugs named by a depends_on value: a list of [[slug]] (or bare slug) items, or one string.
    Anything else (a number, a mapping) names nothing; note.py refuses to write such values."""
    if not values or not isinstance(values, (str, list)):
        return []
    if isinstance(values, str):
        values = [values]
    out = []
    for v in values:
        m = LINK.search(str(v))
        out.append(m.group(1) if m else str(v))
    return out


def loop_through(graph, slug, within=None):
    """A shortest dependency loop through SLUG in GRAPH (slug -> slugs it depends on), as
    [slug, b, ...] meaning slug -> b -> ... -> slug; [slug] when it depends on itself; [] if none.
    WITHIN, when given, limits the search to those slugs (a loop never leaves its own group)."""
    allowed = graph if within is None else within
    parent, queue = {}, []
    for d in dict.fromkeys(graph.get(slug, ())):
        if d == slug:
            return [slug]
        if d in graph and d in allowed and d not in parent:
            parent[d] = slug
            queue.append(d)
    for v in queue:  # breadth first; the queue grows while it is read
        for d in graph[v]:
            if d == slug:
                path = [v]
                while path[-1] != slug:
                    path.append(parent[path[-1]])
                return path[::-1]
            if d in graph and d in allowed and d not in parent:
                parent[d] = v
                queue.append(d)
    return []


def dependency_cycles(graph):
    """One loop for every group of notes that wait on each other (a strongly connected group of
    GRAPH, slug -> slugs it depends on), each from loop_through() of the group's first slug, in
    slug order. Every note in such a group waits on another forever. Iterative Tarjan, so deep
    graphs need no recursion."""
    index, low, on_stack, stack, groups = {}, {}, set(), [], []
    for root in sorted(graph):
        if root in index:
            continue
        work = [(root, iter(sorted(set(graph[root]))))]
        index[root] = low[root] = len(index)
        stack.append(root)
        on_stack.add(root)
        while work:
            v, deps = work[-1]
            w = next(deps, None)
            if w is not None:
                if w not in graph:
                    continue
                if w not in index:
                    index[w] = low[w] = len(index)
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, iter(sorted(set(graph[w])))))
                elif w in on_stack:
                    low[v] = min(low[v], index[w])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                group = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    group.append(w)
                    if w == v:
                        break
                if len(group) > 1 or v in graph[v]:
                    groups.append((min(group), set(group)))
    return [loop_through(graph, s, within) for s, within in sorted(groups, key=lambda g: g[0])]


def loop_text(loop):
    return " -> ".join(loop + loop[:1])


def live_cycles(notes):
    """Loops among notes that are tracked and not done or dead: the ones that hide notes."""
    live = {s for s, n in notes.items() if n["meta"].get("state") and n["meta"]["state"] not in CLOSED}
    return dependency_cycles({s: [d for d in slugs(notes[s]["meta"].get("depends_on")) if d in live]
                              for s in live})


def last_mentions(notes):
    """Latest thought-log date matching each note's `mentions` patterns, one pass over the log.
    Empty when the thought log add-on is not configured."""
    pats = {s: [re.compile(p) for p in n["meta"].get("mentions") or []] for s, n in notes.items()}
    pats = {s: p for s, p in pats.items() if p}
    found = {}
    if not pats:
        return found
    for seg in reversed(thought_log.segments()):
        for s in [s for s in pats if s not in found]:
            if any(p.search(seg["text"]) for p in pats[s]):
                found[s] = as_date(seg["date"])
        if len(found) == len(pats):
            break
    return found


def log_section(body):
    """Text of the note's '## Log' section (up to the next heading), or ''."""
    out, on = [], False
    for line in body.splitlines():
        if re.match(r"^#{1,2} ", line):
            on = line.strip().lower() == "## log"
            continue
        if on:
            out.append(line)
    return "\n".join(out)


def last_log_entry(n):
    """Newest date of a '- YYYY-MM-DD' bullet under '## Log', ignoring dates after today; or None."""
    dates = []
    for d in LOG_BULLET.findall(log_section(n["body"])):
        try:
            d = datetime.date.fromisoformat(d)
        except ValueError:
            continue
        if d <= TODAY:
            dates.append(d)
    return max(dates, default=None)


def condition_met(until, notes):
    if until is None:
        return False
    m = LINK.search(str(until))
    if m:
        dep = notes.get(m.group(1))
        return bool(dep) and dep["meta"].get("state") in CLOSED
    return TODAY >= as_date(until)


def own_score(n, notes, mentioned):
    """(score, why, facts): why is the human-readable reasons; facts holds the same numbers
    structured: touched, days_untouched, nudge_overdue, unblocked_on, knob_total."""
    meta, why = n["meta"], []
    base = float(meta.get("base", DEFAULTS["base"]))
    score = base
    why.append(f"base {base:g}")
    sources = [as_date(meta.get("last_touched")), mentioned.get(n["slug"])]
    if config.COUNT_LOG_ENTRIES:
        sources.append(last_log_entry(n))
    touched = max([d for d in sources if d], default=None)
    facts = {"touched": touched, "days_untouched": None, "nudge_overdue": 0.0,
             "unblocked_on": None, "knob_total": 0.0}
    if touched and meta.get("state") in UNRANKED:
        touched = None  # unconfirmed (proposed/dormant): no aging until the owner confirms; confirming sets last_touched
    if touched:
        days = (TODAY - touched).days
        facts["days_untouched"] = days
        rate = meta.get("age_rate", STATE_AGE_RATE.get(meta.get("state"), DEFAULTS["age_rate"]))
        age = days * float(rate)
        score += age
        why.append(f"+{age:.1f} untouched {days}d (since {touched})")
        every = meta.get("nudge_every")
        if every and days > every:
            over = (days - every) / every
            score += over
            facts["nudge_overdue"] = over
            why.append(f"+{over:.1f} nudge overdue (every {every}d)")
    deps = slugs(meta.get("depends_on"))
    if deps and all(notes.get(d, {}).get("meta", {}).get("state") in CLOSED for d in deps):
        # A dead (or done) dependency may have no done_on; it simply gives no date for the bump.
        finished = max((d for d in (as_date(notes[d]["meta"].get("done_on")) for d in deps) if d), default=None)
        if finished:
            bump = UNBLOCK_BUMP * 0.5 ** ((TODAY - finished).days / UNBLOCK_HALF_LIFE)
            score += bump
            facts["unblocked_on"] = finished
            why.append(f"+{bump:.1f} unblocked {finished}")
    for k in meta.get("knobs") or []:
        if condition_met(k.get("until"), notes):
            continue
        amt = float(k.get("amount", 0))
        set_on = as_date(k.get("set")) or TODAY
        if k.get("half_life"):
            amt *= 0.5 ** ((TODAY - set_on).days / float(k["half_life"]))
        score -= amt
        facts["knob_total"] -= amt
        label = "suppressed" if amt >= 0 else "lifted"
        why.append(f"{-amt:+.1f} {label} ({k.get('why', 'knob')})")
    return score, why, facts


def rank(notes):
    """(rows, missing). Each row: slug, score, state, blocked_by, why (reason strings), unranked,
    pressure_from (slug lifting it, or None), and own_score's facts: touched, days_untouched,
    nudge_overdue (0 if none), unblocked_on, knob_total (net knob effect on the score)."""
    mentioned = last_mentions(notes)
    live = {s: n for s, n in notes.items() if n["meta"].get("state") and n["meta"]["state"] not in CLOSED}
    own = {s: own_score(n, notes, mentioned) for s, n in live.items()}
    eff = {s: own[s][0] for s in live}
    why = {s: list(own[s][1]) for s in live}
    pressure_from = {s: None for s in live}
    waiting = {s: [d for d in slugs(n["meta"].get("depends_on")) if d in live] for s, n in live.items()}
    # Push pressure down dependency chains; repeat to let it travel several hops (cycles just stop growing).
    for _ in range(len(live)):
        changed = False
        for s, deps in waiting.items():
            for d in deps:
                if PRESSURE * eff[s] > eff[d] + 1e-9:
                    eff[d] = PRESSURE * eff[s]
                    why[d] = [w for w in why[d] if not w.startswith("pressure")] + [f"pressure from [[{s}]] -> {eff[d]:.1f}"]
                    pressure_from[d] = s
                    changed = True
        if not changed:
            break
    rows = []
    for s, n in live.items():
        state = n["meta"]["state"]
        blocked = bool(waiting[s])
        rows.append({"slug": s, "score": eff[s], "state": state, "blocked_by": waiting[s],
                     "why": why[s], "unranked": state in UNRANKED or blocked,
                     "pressure_from": pressure_from[s], **own[s][2]})
    missing = sorted({d for n in live.values() for d in slugs(n["meta"].get("depends_on")) if d not in notes})
    return sorted(rows, key=lambda r: -r["score"]), missing


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="rank", choices=["rank", "explain"])
    ap.add_argument("slug", nargs="?")
    ap.add_argument("-n", type=int, default=15)
    ap.add_argument("--all", action="store_true", help="include blocked, proposed and dormant")
    a = ap.parse_args()
    if not os.path.isdir(CONCORDANCE):
        sys.exit(f"no concordance folder at {CONCORDANCE} (set HARNESS_ROOT or run inside a harness)")
    notes = load_notes()
    rows, missing = rank(notes)
    if a.cmd == "explain":
        r = next((r for r in rows if r["slug"] == a.slug), None)
        if not r:
            sys.exit(f"{a.slug} is not ranked (no state, or done/dead)")
        print(f"{r['slug']}  {r['score']:.1f}  [{r['state']}]")
        for w in r["why"]:
            print("  ", w)
        if r["blocked_by"]:
            print("   waiting on:", ", ".join(r["blocked_by"]))
        return
    shown = [r for r in rows if a.all or not r["unranked"]][: a.n]
    for r in shown:
        tag = f"waits on {', '.join(r['blocked_by'])}" if r["blocked_by"] else r["state"]
        print(f"{r['score']:6.1f}  {r['slug']:34} {tag}")
        print(f"        {'; '.join(r['why'])}")
    if missing:
        print("\ndepends_on names with no note yet:", ", ".join(missing))
    loops = live_cycles(notes)
    if loops:
        print("\ndepends_on loops (these notes wait on each other forever):",
              "; ".join(loop_text(l) for l in loops))


if __name__ == "__main__":
    main()
