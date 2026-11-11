"""Read a running thought log live as a sequence of dated segments, and track scan passes over them.

Optional add-on: set thought_log.path in harness.yaml. Without it, every command says so and
exits, and segments() returns [] so the rest of the harness carries on.

A segment starts at a line matching thought_log.date_line (default: a line holding only
mm/dd/yyyy). The same date can appear more than once (typos, re-opened days), so a segment's
id is the date plus an occurrence suffix when needed: 2024-01-02, 2024-01-02#2. An inline todo
section at the end of the file (thought_log.inline_todo_marker) is not part of any day.

Each scan pass lives in scans/<pass>/ with:
  pass.md        - the intent of the pass (what it is looking for)
  ledger.jsonl   - one line per segment scanned: {"id", "hash", "scanned"}
A segment whose text changed since it was scanned (e.g. today's day still being
written) is considered unscanned again.

Usage:
  python thought_log.py stats
  python thought_log.py show 2024-01-02 [2024-01-02#2 ...]
  python thought_log.py next  --pass PASS [-n N] [--kb KB]   newest unscanned first
  python thought_log.py mark  --pass PASS ID [ID ...]
  python thought_log.py grep  REGEX [-n N] [-c] [-a] [-w W]  most recent segments matching
  python thought_log.py dates                                flag repeated / out-of-order date stamps
"""
import argparse, datetime, hashlib, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

SCANS = config.SCANS
DATE_RE = re.compile(config.DATE_LINE)
INLINE_TODO = config.INLINE_TODO


BAD_STAMPS = []  # (line, stamp text, reason) for date-like lines that are not real dates; see segments()


def _iso(m):
    """(ISO date, None) from a date_line match, or (None, reason) when it is not a real date under date_order."""
    parts = dict(zip(config.DATE_ORDER, m.groups()))
    try:
        return datetime.date(int(parts["y"]), int(parts["m"]), int(parts["d"])).isoformat(), None
    except (TypeError, ValueError) as e:  # TypeError: an optional group matched nothing
        return None, str(e)


def segments():
    """Return list of dicts: id, date, line (1-based), text, hash. In file order. [] when not configured.

    A line that matches date_line but is not a real date (e.g. 13/01/2026 read as mdy) does not
    start a segment: it stays an ordinary line of the current day, is recorded in BAD_STAMPS, and
    one warning goes to stderr. `dates` lists them, so a typo never stops the rest of the harness."""
    if not config.thought_log_enabled():
        return []
    segs, cur, seen = [], None, {}
    BAD_STAMPS.clear()
    with open(config.THOUGHT_LOG, encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(f, 1):
            if INLINE_TODO and line.startswith(INLINE_TODO):
                break
            m = DATE_RE.match(line)
            date = None
            if m:
                date, why = _iso(m)
                if date is None:
                    BAD_STAMPS.append((n, m.group(0).strip(), why))
            if date:
                seen[date] = seen.get(date, 0) + 1
                sid = date if seen[date] == 1 else f"{date}#{seen[date]}"
                cur = {"id": sid, "date": date, "line": n, "lines": []}
                segs.append(cur)
            if cur is not None:
                cur["lines"].append(line)
    for s in segs:
        s["text"] = "".join(s.pop("lines"))
        s["hash"] = hashlib.sha1(s["text"].encode()).hexdigest()[:12]
    if BAD_STAMPS:
        print(f"thought_log: {len(BAD_STAMPS)} date stamp(s) are not real dates read as {config.DATE_ORDER} "
              f"(first at line {BAD_STAMPS[0][0]}: {BAD_STAMPS[0][1]!r}); kept as ordinary text. "
              f"`thought_log.py dates` lists them.", file=sys.stderr)
    return segs


def ledger(pass_name):
    path = os.path.join(SCANS, pass_name, "ledger.jsonl")
    done = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    done[r["id"]] = r["hash"]
    return done


def require_log():
    if not config.thought_log_enabled():
        sys.exit("No thought log configured (thought_log.path in harness.yaml), or the file is missing.")


def require_pass(pass_name):
    path = os.path.join(SCANS, pass_name, "pass.md")
    if not os.path.exists(path):
        sys.exit(f"No pass '{pass_name}'. Create {path} describing its intent first.")


def cmd_stats(a):
    segs = segments()
    if not segs:
        sys.exit("no dated segments found (check thought_log.date_line)")
    dups = [s["id"] for s in segs if "#" in s["id"]]
    sizes = sorted(len(s["text"]) for s in segs)
    print(f"segments: {len(segs)}  first: {segs[0]['id']}  last: {segs[-1]['id']}")
    print(f"repeated dates: {len(dups)}  e.g. {dups[:5]}")
    print(f"size bytes: median {sizes[len(sizes)//2]}  max {sizes[-1]}  total {sum(sizes)}")
    for p in sorted(os.listdir(SCANS)) if os.path.isdir(SCANS) else []:
        if os.path.isdir(os.path.join(SCANS, p)):
            done = ledger(p)
            fresh = sum(1 for s in segs if done.get(s["id"]) == s["hash"])
            print(f"pass {p}: {fresh}/{len(segs)} scanned and current")


def cmd_show(a):
    by_id = {s["id"]: s for s in segments()}
    for sid in a.ids:
        s = by_id.get(sid)
        print(s["text"] if s else f"[no segment {sid}]")


def cmd_next(a):
    require_pass(a.pass_)
    done = ledger(a.pass_)
    today = datetime.date.today().isoformat()
    picked, budget = [], a.kb * 1024
    for s in reversed(segments()):  # newest first, so new days are picked before old ones
        if s["date"] >= today and not a.include_today:
            continue  # today is still being written
        if done.get(s["id"]) == s["hash"]:
            continue
        if picked and budget - len(s["text"]) < 0:
            break
        picked.append(s)
        budget -= len(s["text"])
        if len(picked) >= a.n:
            break
    for s in picked:
        print(f"{s['id']}\tline {s['line']}\t{len(s['text'])} bytes\t{'changed' if s['id'] in done else 'new'}")


def cmd_mark(a):
    require_pass(a.pass_)
    by_id = {s["id"]: s for s in segments()}
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    with open(os.path.join(SCANS, a.pass_, "ledger.jsonl"), "a", encoding="utf-8") as f:
        for sid in a.ids:
            if sid not in by_id:
                sys.exit(f"no segment {sid}")
            f.write(json.dumps({"id": sid, "hash": by_id[sid]["hash"], "scanned": stamp}) + "\n")
    print(f"marked {len(a.ids)}")


def cmd_grep(a):
    rx = re.compile(a.regex, 0 if a.case else re.I)
    hits = 0
    for s in reversed(segments()):
        lines = [l.strip() for l in s["text"].splitlines() if rx.search(l)]
        if lines:
            shown = lines if a.all_lines else lines[:1]
            for i, l in enumerate(shown):
                prefix = f"{s['id']}: " if i == 0 else " " * (len(s['id']) + 2)
                print(prefix + l[: a.width])
            if len(lines) > len(shown):
                print(" " * (len(s['id']) + 2) + f"(+{len(lines) - len(shown)} more lines; -a shows all)")
            hits += 1
            if hits >= a.n:
                break


def _parse(d):
    return datetime.date.fromisoformat(d)


def _candidates(d):
    """Plausible typo fixes of a date: other years, swapped month/day, one digit changed."""
    s = d.strftime("%m%d%Y")
    out = set()
    for i in range(8):
        for c in "0123456789":
            t = s[:i] + c + s[i + 1:]
            try:
                out.add(datetime.date(int(t[4:]), int(t[:2]), int(t[2:4])))
            except ValueError:
                pass
    try:
        out.add(datetime.date(d.year, d.day, d.month))
    except ValueError:
        pass
    out.discard(d)
    return out


def date_anomalies(segs):
    """Yield (segment, problem, suggestions) for date stamps that look wrong.

    A stamp is suspect when it does not fit between its neighbours in file order.
    Suggestions are single-digit / month-day-swap edits of it that would fit.
    """
    ds = [_parse(s["date"]) for s in segs]
    seen = {}
    for i, s in enumerate(segs):
        seen.setdefault(s["date"], []).append(s["line"])
    for i, s in enumerate(segs):
        prev = ds[i - 1] if i else None
        nxt = ds[i + 1] if i + 1 < len(ds) else None
        d = ds[i]
        problems = []
        if prev and d < prev:
            problems.append(f"goes back {(prev - d).days} days from previous ({prev})")
        if nxt and d > nxt:
            problems.append(f"next entry ({nxt}) is {(d - nxt).days} days earlier")
        # Only blame this stamp if the neighbours agree with each other; otherwise the neighbour is the odd one.
        if problems and prev and nxt and prev <= nxt and not (prev <= d <= nxt):
            fits = sorted(c for c in _candidates(d) if prev <= c <= nxt)
            yield s, "; ".join(problems), fits
        elif problems and (prev is None or nxt is None):
            yield s, "; ".join(problems), []
        if len(seen[s["date"]]) > 1 and seen[s["date"]][0] == s["line"]:
            yield s, f"date repeated at lines {seen[s['date']]}", None


def date_outliers(segs, window=3, days=45):
    """Stamps far from the median of the stamps around them. Catches runs of wrong stamps
    (e.g. the old year written for the first week of January) that neighbour checks miss."""
    ds = [_parse(s["date"]) for s in segs]
    for i, s in enumerate(segs):
        around = sorted(ds[max(0, i - window):i] + ds[i + 1:i + 1 + window])
        if not around:
            continue
        med = around[len(around) // 2]
        if abs((ds[i] - med).days) > days:
            yield s, med


def cmd_dates(a):
    segs = segments()
    for n, stamp, why in BAD_STAMPS:
        print(f"NOTDATE line {n}  {stamp!r} is not a real date read as {config.DATE_ORDER} ({why})")
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    for s in segs:  # thought_log.suspect_weekdays: days the owner never writes, so a stamp there is likely a typo
        wd = _parse(s["date"]).weekday()
        if wd in config.SUSPECT_WEEKDAYS:
            print(f"{names[wd].upper()} {s['id']} (line {s['line']})")
    for s, med in date_outliers(segs):
        print(f"OUTLIER {s['id']} (line {s['line']})  {abs((_parse(s['date']) - med).days)} days from the stamps around it ({med})")
    for s, problem, fits in date_anomalies(segs):
        if fits is None:
            print(f"REPEAT  {s['date']}  {problem}")
        else:
            hint = f"  probably {', '.join(f.isoformat() for f in fits[:3])}" if fits else ""
            print(f"ORDER   {s['id']} (line {s['line']})  {problem}{hint}")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats").set_defaults(f=cmd_stats)
    p = sub.add_parser("show"); p.add_argument("ids", nargs="+"); p.set_defaults(f=cmd_show)
    p = sub.add_parser("next"); p.add_argument("--pass", dest="pass_", required=True)
    p.add_argument("-n", type=int, default=10); p.add_argument("--kb", type=int, default=60)
    p.add_argument("--include-today", action="store_true"); p.set_defaults(f=cmd_next)
    p = sub.add_parser("mark"); p.add_argument("--pass", dest="pass_", required=True)
    p.add_argument("ids", nargs="+"); p.set_defaults(f=cmd_mark)
    p = sub.add_parser("grep"); p.add_argument("regex"); p.add_argument("-n", type=int, default=20)
    p.add_argument("-c", "--case", action="store_true", help="case sensitive (default is case-insensitive)")
    p.add_argument("-a", "--all-lines", action="store_true", help="show every matching line in a day")
    p.add_argument("-w", "--width", type=int, default=200, help="max characters per line"); p.set_defaults(f=cmd_grep)
    sub.add_parser("dates").set_defaults(f=cmd_dates)
    a = ap.parse_args()
    require_log()
    a.f(a)


if __name__ == "__main__":
    main()
