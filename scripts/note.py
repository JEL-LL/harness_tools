"""Safe, minimal edits to concordance notes (for scan agents; avoids ad-hoc edit scripts).

Finds a note by slug anywhere under concordance/. Everything not being changed is kept
byte-for-byte, including the file's line endings (LF or CRLF). Writes are atomic.

Usage:
  note.py new KIND SLUG "Title" "One-line summary" [--type T] [--state S]
              [--mentions REGEX ...] [--aliases A ...] [--part-of SLUG]
  note.py log SLUG "YYYY-MM-DD: text"      dated bullet into ## Log, in date order
  note.py question SLUG "text"             bullet appended to ## Open questions
  note.py set SLUG KEY VALUE               front-matter field; VALUE is YAML ([a, b], 3, 2026-10-09)
  note.py unset SLUG KEY
  note.py show SLUG

KIND is a concordance folder (the `kinds` setting in harness.yaml; default projects hardware
machines people concepts places orgs) or its singular type name. Multi-line log/question text:
later lines are indented 2 spaces.
Tests point the script elsewhere with --root DIR or env HARNESS_CONCORDANCE.
"""
import argparse, glob, os, re, sys, tempfile

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

KINDS = config.KINDS
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
BULLET_DATE = re.compile(r"^- (\d{4}-\d{2}-\d{2})")
HEADING = re.compile(r"^#{1,2} ")


def die(msg):
    print(f"note.py: {msg}", file=sys.stderr)
    sys.exit(1)


# ---------- files ----------

def note_paths(root):
    """Same rule as priority.py: names starting uppercase or '_' are generated, not notes."""
    return [p for p in glob.glob(os.path.join(root, "**", "*.md"), recursive=True)
            if not (os.path.basename(p)[0].isupper() or os.path.basename(p).startswith("_"))]


def find(root, slug, must=True):
    hits = [p for p in note_paths(root) if os.path.splitext(os.path.basename(p))[0].lower() == slug.lower()]
    if must and not hits:
        die(f"no note '{slug}' under {root}")
    if len(hits) > 1:
        die(f"slug '{slug}' is not unique: {hits}")
    return hits[0] if hits else None


class Note:
    def __init__(self, path):
        self.path = path
        with open(path, encoding="utf-8", newline="") as f:
            text = f.read()
        self.bom = text.startswith("﻿")
        if self.bom:
            text = text[1:]
        self.lines = text.splitlines(keepends=True)
        crlf = sum(l.endswith("\r\n") for l in self.lines)
        self.nl = "\r\n" if crlf * 2 > len(self.lines) else "\n"

    def text(self):
        return ("﻿" if self.bom else "") + "".join(self.lines)

    def save(self):
        d = os.path.dirname(self.path)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".note-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(self.text())
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    def L(self, s):
        """A new line with this file's line ending."""
        return s + self.nl

    def ensure_final_newline(self):
        if self.lines and not self.lines[-1].endswith("\n"):
            self.lines[-1] += self.nl

    # ----- front matter -----
    def fm_range(self):
        """(start, end) line indexes of the front-matter body (between the --- lines), or None."""
        if not self.lines or self.lines[0].rstrip("\r\n") != "---":
            return None
        for i in range(1, len(self.lines)):
            if self.lines[i].rstrip("\r\n") == "---":
                return 1, i
        die(f"{self.path}: unterminated front matter")

    def meta(self):
        r = self.fm_range()
        if not r:
            return {}
        m = yaml.safe_load("".join(self.lines[r[0]:r[1]])) or {}
        if not isinstance(m, dict):
            die(f"{self.path}: front matter is not a mapping")
        return m

    # ----- sections -----
    def section(self, name):
        """(heading index, end index) for '## name' (end = next heading or EOF), or None."""
        start = None
        r = self.fm_range()
        first = r[1] + 1 if r else 0
        for i in range(first, len(self.lines)):
            s = self.lines[i].rstrip("\r\n")
            if start is None:
                if s.strip().lower() == f"## {name}".lower():
                    start = i
            elif HEADING.match(s):
                return start, i
        return (start, len(self.lines)) if start is not None else None


# ---------- helpers ----------

def bullet_lines(text):
    text = text.strip()
    if text.startswith("- "):
        text = text[2:]
    parts = [p.rstrip() for p in text.replace("\r\n", "\n").split("\n")]
    parts = [p for p in parts if p.strip()]
    if not parts:
        die("empty text")
    return ["- " + parts[0]] + ["  " + p.strip() for p in parts[1:]]


def entries(lines, lo, hi):
    """Bullet entries in lines[lo:hi]: list of (start, end_exclusive, first_line)."""
    out, i = [], lo
    while i < hi:
        s = lines[i].rstrip("\r\n")
        if s.startswith("- "):
            j = i + 1
            while j < hi and lines[j].rstrip("\r\n")[:1] in (" ", "\t") and lines[j].strip():
                j += 1
            out.append((i, j, s))
            i = j
        else:
            i += 1
    return out


def has_entry(note, lo, hi, new):
    want = [l.rstrip() for l in new]
    for a, b, _ in entries(note.lines, lo, hi):
        if [l.rstrip("\r\n").rstrip() for l in note.lines[a:b]] == want:
            return True
    return False


def yaml_inline(value):
    """One-line flow YAML for a value (no document-end marker)."""
    s = yaml.safe_dump(value, default_flow_style=True, sort_keys=False, allow_unicode=True, width=10 ** 6)
    s = s.rstrip("\n")
    if s.endswith("\n..."):
        s = s[:-4]
    return s.rstrip("\n")


# ---------- commands ----------

def cmd_new(root, a):
    kind = a.kind.lower()
    folder = next((k for k in KINDS if k.lower() == kind), None) or \
        next((k for k, v in KINDS.items() if v.lower() == kind), None)
    if not folder:
        die(f"unknown kind '{a.kind}' (one of {', '.join(KINDS)})")
    if not SLUG_RE.match(a.slug):
        die(f"bad slug '{a.slug}' (lowercase-hyphenated)")
    hit = find(root, a.slug, must=False)
    if hit:
        die(f"slug '{a.slug}' already exists: {hit}")
    meta = {"type": a.type or KINDS[folder]}
    if a.state:
        meta["state"] = a.state
    if a.aliases:
        meta["aliases"] = a.aliases
    if a.mentions:
        for m in a.mentions:
            try:
                re.compile(m)
            except re.error as e:
                die(f"bad --mentions regex {m!r}: {e}")
        meta["mentions"] = a.mentions
    if a.part_of:
        p = a.part_of.strip("[]")
        meta["part_of"] = f"[[{p}]]"
    fm = []
    for k, v in meta.items():
        if k == "part_of":
            fm.append(f'{k}: "{v}"')
        elif k == "mentions":  # regexes always single-quoted, as in existing notes
            fm.append(f"{k}: [" + ", ".join("'" + m.replace("'", "''") + "'" for m in v) + "]")
        else:
            fm.append(f"{k}: {yaml_inline(v)}")
    text = "---\n" + "\n".join(fm) + "\n---\n" + f"# {a.title.strip()}\n{a.summary.strip()}\n\n## Log\n"
    if yaml.safe_load(text.split("---\n")[1]) != meta:
        die("internal: front matter did not round-trip")
    os.makedirs(os.path.join(root, folder), exist_ok=True)
    path = os.path.join(root, folder, a.slug + ".md")
    with open(path, "x", encoding="utf-8", newline="") as f:
        f.write(text)
    print(f"created {path}")


def cmd_log(root, a):
    new = bullet_lines(a.text)
    m = DATE_RE.match(new[0][2:])
    if not m:
        die('log text must start with "YYYY-MM-DD"')
    date = m.group(1)
    note = Note(find(root, a.slug))
    sec = note.section("Log")
    if sec is None:
        note.ensure_final_newline()
        oq = note.section("Open questions")
        block = [note.L("## Log")] + [note.L(l) for l in new] + [note.L("")]
        if oq:
            if oq[0] > 0 and note.lines[oq[0] - 1].strip():
                block.insert(0, note.L(""))
            note.lines[oq[0]:oq[0]] = block
        else:
            if note.lines and note.lines[-1].strip():
                block.insert(0, note.L(""))
            note.lines.extend(block[:-1])
        note.save()
        print(f"logged (new ## Log section) -> {note.path}")
        return
    h, end = sec
    if has_entry(note, h + 1, end, new):
        print(f"duplicate, skipped: {note.path}")
        return
    ents = entries(note.lines, h + 1, end)
    dated = [(e, BULLET_DATE.match(e[2])) for e in ents]
    dated = [(e, mm.group(1)) for e, mm in dated if mm]
    at = None
    for e, d in dated:
        if d > date:
            at = e[0]
            break
    if at is None:
        at = ents[-1][1] if ents else h + 1
    if at == len(note.lines):
        note.ensure_final_newline()
    note.lines[at:at] = [note.L(l) for l in new]
    note.save()
    print(f"logged -> {note.path}")


def cmd_question(root, a):
    new = bullet_lines(a.text)
    note = Note(find(root, a.slug))
    note.ensure_final_newline()
    sec = note.section("Open questions")
    if sec is None:
        block = [note.L("## Open questions")] + [note.L(l) for l in new]
        if note.lines and note.lines[-1].strip():
            block.insert(0, note.L(""))
        note.lines.extend(block)
    else:
        h, end = sec
        if has_entry(note, h + 1, end, new):
            print(f"duplicate, skipped: {note.path}")
            return
        at = end
        while at > h + 1 and not note.lines[at - 1].strip():
            at -= 1
        note.lines[at:at] = [note.L(l) for l in new]
    note.save()
    print(f"question added -> {note.path}")


def key_block(note, key):
    """(start, end) of a top-level key and its indented/sequence continuation lines, or None."""
    r = note.fm_range()
    if not r:
        return None
    pat = re.compile(r"^" + re.escape(key) + r"\s*:")
    for i in range(r[0], r[1]):
        if pat.match(note.lines[i]):
            j = i + 1
            while j < r[1]:
                s = note.lines[j]
                if s[:1] in (" ", "\t") or s.startswith("- ") or s.rstrip("\r\n") == "-":
                    j += 1
                else:
                    break
            return i, j
    return None


def redump(note, meta):
    r = note.fm_range()
    dumped = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, default_flow_style=None, width=10 ** 6)
    new = [note.L(l) for l in dumped.splitlines()]
    if r:
        note.lines[r[0]:r[1]] = new
    else:
        note.lines[0:0] = [note.L("---")] + new + [note.L("---")]


def write_meta(note, key, value, remove):
    old = note.meta()
    want = {k: v for k, v in old.items() if k != key}
    if not remove:
        want = dict(old)
        want[key] = value
    r = note.fm_range()
    blk = key_block(note, key)
    saved = list(note.lines)
    try:
        if remove:
            if blk is None:
                print(f"'{key}' not set: {note.path}")
                return False
            del note.lines[blk[0]:blk[1]]
        else:
            raw = getattr(note, "_raw", None)
            text = raw if raw is not None else yaml_inline(value)
            line = note.L(f"{key}: {text}")
            if blk:
                note.lines[blk[0]:blk[1]] = [line]
            elif r:
                note.lines[r[1]:r[1]] = [line]
            else:
                note.lines[0:0] = [note.L("---"), line, note.L("---")]
        ok = note.meta() == want and list(note.meta()) == list(want)
    except (yaml.YAMLError, SystemExit):
        ok = False
    if not ok:
        note.lines = saved
        redump(note, want)
        if note.meta() != want:
            die("could not write front matter safely; file left unchanged")
        print("(front matter re-dumped)", file=sys.stderr)
    return True


def cmd_set(root, a):
    if not re.match(r"^[A-Za-z_][\w-]*$", a.key):
        die(f"bad key '{a.key}'")
    try:
        value = yaml.safe_load(a.value)
    except yaml.YAMLError as e:
        die(f"VALUE is not valid YAML: {e}")
    note = Note(find(root, a.slug))
    # keep the user's own spelling when it is one line and means the same thing
    try:
        if "\n" not in a.value and a.value.strip() and \
                yaml.safe_load(f"{a.key}: {a.value.strip()}") == {a.key: value}:
            note._raw = a.value.strip()
    except yaml.YAMLError:
        pass
    if note.meta().get(a.key, object()) == value and key_block(note, a.key):
        print(f"unchanged: {note.path}")
        return
    write_meta(note, a.key, value, remove=False)
    note.save()
    print(f"set {a.key} -> {note.path}")


def cmd_unset(root, a):
    note = Note(find(root, a.slug))
    if write_meta(note, a.key, None, remove=True):
        note.save()
        print(f"unset {a.key} -> {note.path}")


def cmd_show(root, a):
    path = find(root, a.slug)
    print(path)
    with open(path, encoding="utf-8") as f:
        sys.stdout.write(f.read())


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    p = argparse.ArgumentParser(description="Safe edits to concordance notes.")
    p.add_argument("--root", help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    n.add_argument("kind"); n.add_argument("slug"); n.add_argument("title"); n.add_argument("summary")
    n.add_argument("--type"); n.add_argument("--state")
    n.add_argument("--mentions", nargs="+"); n.add_argument("--aliases", nargs="+")
    n.add_argument("--part-of", dest="part_of")
    q = sub.add_parser("log"); q.add_argument("slug"); q.add_argument("text")
    q = sub.add_parser("question"); q.add_argument("slug"); q.add_argument("text")
    q = sub.add_parser("set"); q.add_argument("slug"); q.add_argument("key"); q.add_argument("value")
    q = sub.add_parser("unset"); q.add_argument("slug"); q.add_argument("key")
    q = sub.add_parser("show"); q.add_argument("slug")
    a = p.parse_args(argv)
    root = a.root or config.CONCORDANCE
    if not os.path.isdir(root):
        die(f"no concordance dir {root}")
    {"new": cmd_new, "log": cmd_log, "question": cmd_question, "set": cmd_set,
     "unset": cmd_unset, "show": cmd_show}[a.cmd](root, a)


if __name__ == "__main__":
    main()
