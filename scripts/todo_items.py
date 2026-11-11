"""Read the todo file and the thought log's inline todo section live, and track which items are pinned to notes.

Optional add-on. Sources, each switched on only when configured in harness.yaml:
  todo.path                        the todo file. With todo.split_marker set, everything above the
                                   line starting with the marker is done and everything below is open;
                                   without it the whole file is open.
  thought_log.inline_todo_marker   everything after this line in the thought log is an inline todo list.
With neither configured, every command reports no items, and review.py leaves its todo section out.

Items are separated by blank lines. Lines starting with "|" under an item are progress updates on it.

An item's id is a short hash of its first line, so it survives edits to the rest
of the item. Pins live in pins/todo.jsonl, one line per pin (later lines win):
  {"id", "first", "notes": ["[[slug]]", ...], "context": ["2024-03-12", ...], "status", "why"}
status: unknown (default) | pinned | dead | done-elsewhere

Usage:
  python todo_items.py list   [--source open|done|inline|all] [--unpinned]
  python todo_items.py show   ID [ID ...]
  python todo_items.py pin    ID --notes "[[a]]" "[[b]]" [--context DATE ...] [--status pinned] [--why TEXT]
  python todo_items.py unpin  ID            (appends a status=unknown record; history stays in the file)
  python todo_items.py orphans              pins still marked pinned whose item is gone (first line edited,
                                            e.g. ticked [x], or the item was removed)
  python todo_items.py stale  [-n 3]        open/inline items the harness thinks are finished (pin says
                                            done-elsewhere/dead first, then items whose notes are all done);
                                            Ticking an item in the list drops it off.
                                            A still-real item under a done note: re-pin it to a live note.

pin/unpin also work on an orphaned id: the record keeps the "first" line from its last pin.
"""
import argparse, datetime, hashlib, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

PINS = config.PINS
SPLIT = config.TODO_SPLIT
INLINE_TODO = config.INLINE_TODO


def _blocks(lines):
    block = []
    for line in lines:
        if line.strip():
            block.append(line.rstrip("\n"))
        elif block:
            yield block
            block = []
    if block:
        yield block


def _item(block, source, pos):
    first = block[0].strip()
    m = re.match(r"^([A-Za-z0-9_ .\-]{2,30}):\s", first)
    return {
        "id": hashlib.sha1(re.sub(r"\s+", " ", first).lower().encode()).hexdigest()[:8],
        "source": source,
        "pos": pos,  # position within its source, 0 = first in the file
        "subject": m.group(1) if m and not first.lower().startswith("http") else "",
        "first": first,
        "text": "\n".join(block),
        "updates": sum(1 for l in block if l.startswith("|")),
    }


def items():
    """Every item from the configured sources; [] when none is configured.
    A configured source whose file is missing is warned about on stderr (see cmd_orphans)."""
    for key, path in config.missing_sources():
        print(f"todo_items: {key} is set but {path} does not exist; its items are left out", file=sys.stderr)
    out = []
    if config.todo_enabled():
        with open(config.TODO, encoding="utf-8-sig", errors="replace") as f:
            lines = f.readlines()
        cut = next((i for i, l in enumerate(lines) if SPLIT and l.startswith(SPLIT)), None)
        if not SPLIT:  # no split marker configured: the whole file is open
            out += [_item(b, "open", i) for i, b in enumerate(_blocks(lines))]
        elif cut is None:  # configured but missing: treat it all as done rather than flood the open list
            print(f"todo_items: split marker {SPLIT!r} not found in {config.TODO}; treating every item as done",
                  file=sys.stderr)
            out += [_item(b, "done", i) for i, b in enumerate(_blocks(lines))]
        else:
            out += [_item(b, "done", i) for i, b in enumerate(_blocks(lines[:cut]))]
            out += [_item(b, "open", i) for i, b in enumerate(_blocks(lines[cut + 1:]))]
    if INLINE_TODO and config.thought_log_enabled():
        with open(config.THOUGHT_LOG, encoding="utf-8", errors="replace") as f:
            inline, on = [], False
            for line in f:
                if line.startswith(INLINE_TODO):
                    on = True
                    continue
                if on:
                    inline.append(line)
        out += [_item(b, "inline", i) for i, b in enumerate(_blocks(inline))]
    seen = {}
    for it in out:  # identical first lines (bare URLs etc.) get -2, -3 suffixes
        seen[it["id"]] = seen.get(it["id"], 0) + 1
        if seen[it["id"]] > 1:
            it["id"] += f"-{seen[it['id']]}"
    return out


def pins():
    out = {}
    if os.path.exists(PINS):
        with open(PINS, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    out[r["id"]] = r
    return out


def cmd_list(a):
    p = pins()
    for it in items():
        if a.source != "all" and it["source"] != a.source:
            continue
        if a.unpinned and p.get(it["id"], {}).get("status", "unknown") != "unknown":
            continue
        st = p.get(it["id"], {}).get("status", "?")
        print(f"{it['id']}  {it['source']:6} {it['pos']:4}  {st:8} {it['first'][:110]}")


def cmd_show(a):
    by = {it["id"]: it for it in items()}
    p = pins()
    for i in a.ids:
        it = by.get(i)
        if not it:
            print(f"[no item {i}]")
            continue
        print(f"== {i} ({it['source']} #{it['pos']}) pin: {p.get(i)}\n{it['text']}\n")


def cmd_pin(a):
    by = {it["id"]: it for it in items()}
    old = pins().get(a.id)
    if a.id in by:
        first = by[a.id]["first"]
    elif old:
        first = old["first"]  # orphan: the item's first line changed or it was removed
    else:
        sys.exit(f"no item {a.id}")
    rec = {"id": a.id, "first": first[:120], "notes": [n.strip() for v in (a.notes or []) for n in v.split(",") if n.strip()], "context": a.context or [],
           "status": a.status, "why": a.why or "", "at": datetime.date.today().isoformat()}
    os.makedirs(os.path.dirname(PINS), exist_ok=True)
    with open(PINS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print("pinned", a.id)


def cmd_unpin(a):
    a.notes, a.context, a.status, a.why = [], [], "unknown", "unpinned"
    cmd_pin(a)


def cmd_orphans(a):
    missing = config.missing_sources()
    if missing:  # every pin from that source would look orphaned; refuse rather than mislead
        sys.exit("orphans: cannot tell, because a configured source is missing: "
                 + "; ".join(f"{k} = {p}" for k, p in missing))
    ids = {it["id"] for it in items()}
    for i, r in pins().items():
        if i not in ids and r.get("status") == "pinned":
            print(f"{i}  {', '.join(r['notes'])}  {r['first'][:90]}")


def _note_states():
    """slug -> (state, done_on) from each concordance note's front matter. Same files as
    priority.note_paths(): any depth, skipping generated names (uppercase or '_' first, e.g. INDEX.md)."""
    import glob, yaml
    out = {}
    for path in glob.glob(os.path.join(config.CONCORDANCE, "**", "*.md"), recursive=True):
        name = os.path.basename(path)
        if name[0].isupper() or name.startswith("_"):
            continue
        with open(path, encoding="utf-8") as f:
            m = re.match(r"---\r?\n(.*?)\r?\n---", f.read(), re.S)
        fm = (yaml.safe_load(m.group(1)) if m else None) or {}
        out[os.path.basename(path)[:-3]] = (fm.get("state"), fm.get("done_on"))
    return out


def cmd_stale(a):
    """Open/inline items the harness thinks are finished but that are not ticked in the owner's lists yet."""
    states, p = _note_states(), pins()
    strong, weak = [], []
    for it in items():
        if it["source"] not in ("open", "inline") or it["id"] not in p:
            continue
        r = p[it["id"]]
        slugs = [s.strip("[] ") for s in r.get("notes", [])]
        if r.get("status") in ("done-elsewhere", "dead"):
            strong.append((it, f"pin: {r['status']} ({r.get('at', '?')}); {r.get('why', '')}"))
        elif r.get("status") == "pinned" and slugs and all(states.get(s, (None,))[0] in ("done", "dead") for s in slugs):
            why = ", ".join(f"[[{s}]] {states[s][0]} {states[s][1] or ''}".rstrip() for s in slugs)
            weak.append((it, f"note done: {why}"))
    found = [("pin says finished", x) for x in strong] + [("its note is done; check", x) for x in weak]
    print(f"{len(strong)} with the pin saying finished/dead, {len(weak)} whose note is done "
          f"(showing {len(found) if not a.n else min(a.n, len(found))})")
    where = {"open": os.path.basename(config.TODO or "todo"),
             "inline": os.path.basename(config.THOUGHT_LOG or "thought log") + " inline todo"}
    for kind, (it, why) in found[: a.n or None]:
        # Pin reasons often say just MM-DD; the context days carry the year, so always show them.
        days = ", ".join(p[it["id"]].get("context") or []) or "none recorded"
        print(f"\n{it['id']}  {where[it['source']]} #{it['pos']}  [{kind}]\n  {it['first'][:140]}\n  {why[:200]}"
              f"\n  evidence days: {days}")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list"); p.add_argument("--source", default="open", choices=["open", "done", "inline", "all"])
    p.add_argument("--unpinned", action="store_true"); p.set_defaults(f=cmd_list)
    p = sub.add_parser("show"); p.add_argument("ids", nargs="+"); p.set_defaults(f=cmd_show)
    p = sub.add_parser("pin"); p.add_argument("id"); p.add_argument("--notes", nargs="*", action="extend")
    p.add_argument("--context", nargs="*", action="extend"); p.add_argument("--status", default="pinned",
                   choices=["unknown", "pinned", "dead", "done-elsewhere"])
    p.add_argument("--why"); p.set_defaults(f=cmd_pin)
    p = sub.add_parser("unpin"); p.add_argument("id"); p.set_defaults(f=cmd_unpin)
    p = sub.add_parser("orphans"); p.set_defaults(f=cmd_orphans)
    p = sub.add_parser("stale"); p.add_argument("-n", type=int, default=3, help="how many to show (0 = all)")
    p.set_defaults(f=cmd_stale)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
