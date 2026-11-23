"""Search the concordance's Log entries the way a mail search box filters mail.

A result is one Log entry: a top-level "- " bullet in a note's "## Log" section, with its
continuation lines (indented lines up to the next bullet, heading or unindented line). Any other
unindented line, such as a trailing "Todos: ..." line or a closing remark, is not an entry. An
entry's date is the first YYYY-MM-DD before any letter in its bullet ("- 2026-10-05: ...",
"- 2026-10-05..10-09: ..." -> 2026-10-05); an entry without one has no date.

Usage:
  python query.py [--notes] [--json [--added]] [--limit N] QUERY...

QUERY is one or more words (they are joined with spaces). Every term must match (AND);
`a OR b` (OR in capitals) needs either, and binds tighter: `a OR b c` is `(a OR b) c`.
`-term` excludes; parentheses group, and `-( ... )` excludes a group. AND may be written but
is not needed.

Entry terms (case-insensitive):
  word  "a phrase"        substring of the entry's text (continuation lines included)
  re:PATTERN              regex search in the entry's text; quote it if it has spaces: re:"a b"
  after:YYYY-MM-DD        entry date on or after; before:YYYY-MM-DD  entry date before.
                          An undated entry matches neither (so -after: does match it).
  added-after:YYYY-MM-DD  the day the entry's first line was committed (git blame, committer
  added-before:YYYY-MM-DD time, local date), on or after / before. Uncommitted lines count as
                          added today. Needs the concordance to be in a git work tree.
Note terms (the entry's note must match; values case-insensitive, [[slug]] or slug):
  note:SLUG               that note
  kind:K                  its type (front-matter `type`, else its folder's): kind:project or
                          kind:projects
  state:S                 its state
  part_of:SLUG            SLUG itself or any note below it in the part_of tree
  links:SLUG              the note links to [[SLUG]] anywhere in its text
  FIELD:VALUE             any other front-matter key: equal to VALUE, or VALUE is one of its
                          items when it is a list; a note without the key does not match

A word with a colon in it (a web address) is read as FIELD:VALUE; quote it to search for it.
Quoted text cannot contain a double quote (use re: with \\x22).

Output: one entry per result, by entry date (undated last), then slug, then file order:
  <slug>  <first line of the entry>
          <continuation lines>
and a count line. --notes lists matching notes instead (slug, kind, state, matching entries).
--json prints a list of {slug, kind, path, date, added, text}. `added` (the day the entry's
first line was committed) is filled in only when the query has an added-* term or --added is
given, and is null when git cannot tell (no git work tree, or git blame fails on that file).
--limit N shows the first N results (or notes). Any other word starting with -- is an error;
search for one by putting it after `--`.

For added-* terms, git blame runs only on files committed to (or changed) since the earliest
added-* date, several at once; on every candidate file when the history has a commit dated
earlier than its parent, or the date has no representable local midnight (see Added).

Read-only. Exit 0 with matches, 1 with none, 2 on a bad query or option, or when an added-*
term cannot be evaluated (no git work tree, git missing, git blame failing).

Examples:
  python query.py added-after:2026-11-01                  every entry written since Nov 1
  python query.py part_of:garden-season after:2026-10-01 -state:done
  python query.py '"permit desk" OR re:pallets?' --notes
"""
import argparse, concurrent.futures, datetime, json, os, re, subprocess, sys, time

sys.dont_write_bytecode = True  # read-only: no __pycache__ either
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import links
import priority

TODAY = datetime.date.today()
HEADING = re.compile(r"^#{1,2} ")  # as priority.log_section
ENTRY_DATE = re.compile(r"^[^A-Za-z]*?(\d{4}-\d{2}-\d{2})")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
KEY = re.compile(r"^([A-Za-z_][\w-]*):(.*)$", re.S)
DATE_OPS = {"after", "before", "added-after", "added-before"}
NOTE_OPS = {"note", "kind", "state", "part_of", "links"}


class QueryError(Exception):
    pass


def fail(msg):
    print(f"query.py: {msg}", file=sys.stderr)
    sys.exit(2)


# ---------------------------------------------------------------- entries

def build_entry(e_line, lines):
    m = ENTRY_DATE.match(lines[0])
    date = None
    if m:
        try:
            date = datetime.date.fromisoformat(m.group(1))
        except ValueError:
            pass
    rest = lines[1:]
    indent = min((len(l) - len(l.lstrip()) for l in rest), default=0)
    return {"line": e_line, "date": date, "first": lines[0].strip(),
            "rest": [l[indent:].rstrip() for l in rest]}


def log_entries(note):
    """Log entries of a note, in file order: line (1-based in the file), date, first, rest."""
    text = note["text"]
    body = priority.front_matter(text)[1]
    offset = text.count("\n", 0, len(text) - len(body))  # lines before the body
    found, cur, on = [], None, False
    for i, raw in enumerate(body.split("\n")):
        line = raw.rstrip("\r")
        if HEADING.match(line):
            on, cur = line.strip().lower() == "## log", None
            continue
        if not on:
            continue
        if line.startswith("- "):
            cur = (offset + i + 1, [line[2:]])
            found.append(cur)
        elif not line.strip():
            continue  # a blank line inside an entry does not end it
        elif line[0] in " \t" and cur:
            cur[1].append(line)
        else:
            cur = None  # "Todos: ...", a remark: not an entry, and it ends the one before
    return [build_entry(n, ls) for n, ls in found]


def entry_text(e):
    return "\n".join([e["first"], *e["rest"]])


# ---------------------------------------------------------------- git

def git(*args, cwd):
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise QueryError("added-after:/added-before: need git, which is not installed")


def require_work_tree():
    p = git("rev-parse", "--is-inside-work-tree", cwd=priority.CONCORDANCE)
    if p.returncode != 0 or p.stdout.strip() != "true":
        why = (p.stderr.strip().splitlines() or [""])[0]
        raise QueryError(f"added-after:/added-before: need the concordance ({priority.CONCORDANCE}) "
                         f"to be in a git work tree" + (f" ({why})" if why else ""))


OLD = "old"  # every line of the file was committed before the earliest added-* date of the query


def blame_file(path):
    """{line: local date of the commit that added it} for a note; lines missing from it are not
    committed yet. {} for a file never committed. Raises QueryError when git blame fails otherwise."""
    folder, name = os.path.split(os.path.abspath(path))
    p = git("blame", "--line-porcelain", "--", name, cwd=folder)
    if p.returncode != 0:
        # The index may spell the name in another case (renamed outside git on a case-insensitive
        # disk); git blame and a plain ls-files are case-sensitive, so look it up ignoring case.
        same = git("ls-files", "-z", "--", f":(icase,literal){name}", cwd=folder)
        spelled = [f for f in same.stdout.split("\0") if f and "/" not in f]
        tracked = same.returncode == 0 and bool(spelled)
        if tracked and name not in spelled:
            p = git("blame", "--line-porcelain", "--", spelled[0], cwd=folder)
    if p.returncode != 0:
        has_head = git("rev-parse", "--verify", "-q", "HEAD", cwd=folder).returncode == 0
        if tracked and has_head:
            raise QueryError(f"git blame failed on {links.rel(path)}: "
                             + (p.stderr.strip().splitlines() or ["no message"])[0])
        return {}  # never committed: every line counts as added today
    days, line, sha = {}, None, None
    for row in p.stdout.splitlines():
        if row.startswith("\t"):
            continue
        parts = row.split(" ")
        if len(parts) >= 3 and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", parts[0]):
            sha, line = parts[0], int(parts[2])
        elif parts[0] == "committer-time" and line is not None and sha.strip("0"):
            days[line] = datetime.datetime.fromtimestamp(int(parts[1])).date()
    return days


def key(path):
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


class Added:
    """The day each Log line was added, from git blame, run only for the files that need it.

    With SINCE (the earliest added-* date in the query) a prefilter first asks git which files
    could hold a line added on or after SINCE: files that a commit at or after local midnight of
    SINCE lists (every commit touching the concordance is read, merges included, renames listed
    as a delete plus an add), files that differ from HEAD (staged or not), and files marked
    assume-unchanged or skip-worktree (whose edits git diff may not show). Those are blamed,
    tracked or not. Other tracked files hold only older lines (OLD); untracked files hold only
    today's. Blame dates are local committer dates, so a line's date is on or after SINCE exactly
    when its commit's time is at or after that midnight.

    Why that is enough: a line blamed on commit C is, at HEAD, in a file whose current name was
    listed either by C itself or by a later commit on the way from C to HEAD that renamed the
    file. When no commit is older than its parents, those later commits are no older than C, so
    if C is at or after the cutoff, the current name was listed by a commit at or after it. A
    commit older than one of its parents (a rebase, a wrong clock) breaks that: a rename dated
    "before" the cutoff can carry a new line to a name no recent commit lists. So the prefilter
    is dropped (blame decides) when any commit reachable from HEAD is older than one of its
    parents, when a note is in neither of git's tracked and untracked lists (paths that do not
    line up), when git prints a quoted path, and when SINCE has no local midnight the platform
    can represent (before 1970 on some systems, or far in the future).

    QUERY_BLAME_ALL=1 in the environment turns the prefilter off (the tests compare the two)."""

    def __init__(self, since=None):
        self.lines, self.failed, self.tracked, self.touched = {}, set(), None, None
        if since and not os.environ.get("QUERY_BLAME_ALL"):
            self._prefilter(since)

    def _prefilter(self, since):
        # Every git command runs in the concordance folder and prints paths relative to it
        # (ls-files without --full-name, --relative), and keys are built by joining those onto
        # CONCORDANCE as written: never git's resolved top level, which differs from the notes'
        # paths when the harness is reached through a link, junction or substituted drive.
        cwd = priority.CONCORDANCE
        under = lambda rel: key(os.path.join(cwd, rel))
        try:
            cutoff = time.mktime(since.timetuple())  # local midnight
        except (OverflowError, OSError, ValueError):
            return  # no prefilter: blame decides
        if git("rev-parse", "--verify", "-q", "HEAD", cwd=cwd).returncode != 0:
            if git("rev-parse", "--is-inside-work-tree", cwd=cwd).stdout.strip() == "true":
                self.tracked, self.touched = set(), set()  # no commits yet: every line is today's
            return
        files = git("ls-files", "-v", "-z", "--", ".", cwd=cwd)          # "H path"; tag != H: below
        others = git("ls-files", "-z", "--others", "--", ".", cwd=cwd)   # untracked, ignored included
        # --root: list the files a root (or shallow-boundary) commit added, whatever log.showRoot
        # says; the -c settings pin other user config that would change what log prints.
        log = git("-c", "core.quotePath=false", "-c", "log.follow=false", "-c", "log.showSignature=false",
                  "log", "--root", "-m", "--full-history", "--no-renames", "--no-color", "--relative",
                  "--format=%x01%ct", "--name-only", "--", ".", cwd=cwd)
        # Plumbing, not `git diff`: diff-index never refreshes (rewrites) .git/index. A file whose
        # timestamps changed but whose content did not is listed too, and is simply blamed.
        changed = git("-c", "core.quotePath=false", "diff-index", "HEAD", "--no-renames", "--relative",
                      "--name-only", "-z", "--", ".", cwd=cwd)
        graph = git("-c", "log.showSignature=false", "log", "--no-color", "--format=%H %ct %P", "HEAD", cwd=cwd)
        if any(p.returncode for p in (files, others, log, changed, graph)):
            return  # no prefilter: blame whatever is needed
        times, parents = {}, {}
        for row in graph.stdout.splitlines():
            h, ct, *ps = row.split()
            times[h], parents[h] = int(ct), ps
        if any(times[h] < times[p] for h, ps in parents.items() for p in ps if p in times):
            return  # committer times go backwards somewhere: see the class docstring
        touched, recent = set(), False
        for row in log.stdout.split("\n"):
            if row.startswith("\x01"):
                recent = int(row[1:]) >= cutoff
            elif row and recent:
                if row.startswith('"'):
                    return  # a quoted (oddly named) path: no prefilter rather than a wrong one
                touched.add(under(row))
        touched |= {under(f) for f in changed.stdout.split("\0") if f}  # staged or not, vs HEAD
        tracked = set()
        for f in files.stdout.split("\0"):
            if len(f) > 2:
                tracked.add(under(f[2:]))
                if f[0] != "H":  # assume-unchanged (lower case), skip-worktree (S), unmerged, ...:
                    touched.add(under(f[2:]))  # git diff may not show their edits; blame does
        # Every note must be listed as tracked or untracked. If one is not, the paths did not line
        # up (a folder name in another case, say) and the prefilter is not trusted.
        listed = tracked | {under(f) for f in others.stdout.split("\0") if f}
        if any(key(p) not in listed for p in priority.note_paths()):
            return
        self.tracked, self.touched = tracked, touched

    def known(self, path, line):
        """The day LINE was added; OLD when it is before the earliest added-* date; None when only
        git blame can tell."""
        k = key(path)
        if k in self.lines:
            return self.lines[k].get(line, TODAY)
        if self.tracked is not None and k not in self.touched:
            return TODAY if k not in self.tracked else OLD
        return None

    def blame(self, paths, strict=True, exact=False):
        """Blame the files among PATHS that need it, in parallel. strict: a failure is a QueryError;
        otherwise the file is marked failed. exact: blame OLD files too, for their real dates."""
        todo = {key(p): p for p in paths}  # git needs the path as written (case matters to it)
        todo = {k: p for k, p in todo.items() if k not in self.lines and k not in self.failed and (
            self.tracked is None or k in self.touched or (exact and k in self.tracked))}
        if not todo:
            return
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(todo))) as pool:
            futures = {k: pool.submit(blame_file, p) for k, p in sorted(todo.items())}
        for k, f in futures.items():
            try:
                self.lines[k] = f.result()
            except QueryError:
                if strict:
                    raise
                self.failed.add(k)

    def date(self, path, line):
        """The day for output, after blame(..., exact=True); None when its blame failed."""
        if key(path) in self.failed:
            return None
        d = self.known(path, line)
        return None if d is OLD else d


# ---------------------------------------------------------------- query: tokens

def tokenize(q):
    """Tokens: "(", ")", "-" (negation, glued to what follows), "OR", "AND", and
    ("term", key or None, value, quoted)."""
    out, i, n = [], 0, len(q)
    while i < n:
        c = q[i]
        if c.isspace():
            i += 1
        elif c in "()":
            out.append(c)
            i += 1
        elif c == "-":
            if i + 1 >= n or q[i + 1].isspace() or q[i + 1] == ")":
                raise QueryError("'-' must be followed by a term, e.g. -word or -state:done")
            out.append("-")
            i += 1
        elif c == '"':
            end = q.find('"', i + 1)
            if end < 0:
                raise QueryError(f"unclosed quote: {q[i:]}")
            out.append(("term", None, q[i + 1:end], True))
            i = end + 1
        else:
            j, depth = i, 0
            while j < n and not q[j].isspace() and q[j] != '"':
                if q[j] == "(":
                    depth += 1
                elif q[j] == ")":
                    if depth == 0:
                        break  # closes a group opened before this word
                    depth -= 1
                j += 1
            word = q[i:j]
            m = KEY.match(word)
            if m and not m.group(2) and j < n and q[j] == '"':  # key:"quoted value"
                end = q.find('"', j + 1)
                if end < 0:
                    raise QueryError(f"unclosed quote: {q[i:]}")
                out.append(("term", m.group(1), q[j + 1:end], True))
                i = end + 1
            elif m:
                if not m.group(2):
                    raise QueryError(f"nothing after '{word}' (quote it to search for the text)")
                out.append(("term", m.group(1), m.group(2), False))
                i = j
            elif j < n and q[j] == '"':
                raise QueryError(f"a quote inside a word: {q[i:j + 1]}")
            else:
                out.append(word if word in ("OR", "AND") else ("term", None, word, False))
                i = j
    return out


# ---------------------------------------------------------------- query: parse

def parse(q):
    """Tree of ("and", [..]), ("or", [..]), ("not", node), ("term", key, value)."""
    toks = tokenize(q)
    if not toks:
        raise QueryError("empty query")
    pos = [0]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else None

    def take():
        pos[0] += 1
        return toks[pos[0] - 1]

    def all_of():
        items = []
        while peek() not in (None, ")"):
            if peek() == "AND":
                take()
                if not items or peek() in (None, ")", "OR", "AND"):
                    raise QueryError("AND needs a term on each side")
                continue
            items.append(any_of())
        return items

    def any_of():
        items = [unary()]
        while peek() == "OR":
            take()
            if peek() in (None, ")", "OR", "AND"):
                raise QueryError("OR needs a term on each side")
            items.append(unary())
        return items[0] if len(items) == 1 else ("or", items)

    def unary():
        t = take()
        if t == "-":
            return ("not", unary())
        if t == "(":
            items = all_of()
            if peek() != ")":
                raise QueryError("unclosed '('")
            take()
            if not items:
                raise QueryError("empty ( )")
            return items[0] if len(items) == 1 else ("and", items)
        if t in (")", "OR", "AND"):
            raise QueryError(f"unexpected {t}" + (f" ({t} needs a term on each side)" if t != ")" else ""))
        return term(*t[1:])

    items = all_of()
    if peek() is not None:
        raise QueryError("unmatched ')'")
    return items[0] if len(items) == 1 else ("and", items)


def bare_slug(v):
    m = priority.LINK.search(v)
    return (m.group(1) if m else v).strip().lower()


def term(key, value, quoted):
    k = key.lower() if key else None
    if k is None:
        return ("term", "text", value.lower())
    if k == "re":
        try:
            return ("term", "re", re.compile(value, re.I))
        except re.error as e:
            raise QueryError(f"bad regex in re:{value}: {e}")
    if k in DATE_OPS:
        try:
            if not ISO.match(value):
                raise ValueError
            return ("term", k, datetime.date.fromisoformat(value))
        except ValueError:
            raise QueryError(f"bad date in {key}:{value} (want YYYY-MM-DD)")
    if k == "kind":
        return ("term", "kind", config.KINDS.get(value.lower(), value).lower())
    if k in NOTE_OPS:
        return ("term", k, bare_slug(value))
    return ("term", "field", (key, bare_slug(value)))


def cost(node):
    """Rough cost of a node, so cheap tests run first and git blame runs last."""
    if node[0] == "term":
        return 2 if node[1].startswith("added") else 0 if node[1] in NOTE_OPS | {"field"} else 1
    if node[0] == "not":
        return cost(node[1])
    return max(cost(c) for c in node[1])


def order(node):
    if node[0] in ("and", "or"):
        return (node[0], sorted((order(c) for c in node[1]), key=cost))
    if node[0] == "not":
        return ("not", order(node[1]))
    return node


def added_dates(node):
    """The dates of the added-after:/added-before: terms in the tree."""
    if node[0] == "term":
        return [node[2]] if node[1].startswith("added") else []
    if node[0] == "not":
        return added_dates(node[1])
    return [d for c in node[1] for d in added_dates(c)]


# ---------------------------------------------------------------- query: match

class Matcher:
    def __init__(self, notes, added):
        self.notes, self.added, self.parents = notes, added, {}

    def kind(self, n):
        t = n["meta"].get("type")
        if t:
            return str(t)
        folder = os.path.relpath(n["path"], priority.CONCORDANCE).replace(os.sep, "/").split("/")[0]
        return config.KINDS.get(folder, "")

    def ancestors(self, slug):
        """SLUG and every note above it in the part_of tree (loop-safe)."""
        if slug not in self.parents:
            seen, todo = set(), [slug]
            while todo:
                s = todo.pop()
                if s in seen:
                    continue
                seen.add(s)
                n = self.notes.get(s)
                if n:
                    todo += [p.strip().lower() for p in priority.slugs(n["meta"].get("part_of"))]
            self.parents[slug] = seen
        return self.parents[slug]

    def field(self, meta, key, want):
        if key not in meta:
            return False
        v = meta[key]
        items = v if isinstance(v, list) else [v]
        return any(not isinstance(x, (dict, list)) and x is not None and bare_slug(str(x)) == want
                   for x in items)

    def test(self, node, n, e):
        """True, False, or None when an added-* term needs git blame first (three-valued logic,
        so a cheaper term that decides the answer saves the blame)."""
        op = node[0]
        if op in ("and", "or"):
            decisive, result = op == "or", not (op == "or")
            for c in node[1]:
                r = self.test(c, n, e)
                if r is decisive:
                    return decisive
                if r is None:
                    result = None
            return result
        if op == "not":
            r = self.test(node[1], n, e)
            return None if r is None else not r
        kind, v = node[1], node[2]
        meta = n["meta"]
        if kind == "text":
            return v in entry_text(e).lower()
        if kind == "re":
            return bool(v.search(entry_text(e)))
        if kind == "after":
            return e["date"] is not None and e["date"] >= v
        if kind == "before":
            return e["date"] is not None and e["date"] < v
        if kind in ("added-after", "added-before"):
            day = self.added.known(n["path"], e["line"])
            if day is None:
                return None
            if day is OLD:  # added before the query's earliest added-* date, so before V too
                return kind == "added-before"
            return day >= v if kind == "added-after" else day < v
        if kind == "note":
            return n["slug"].lower() == v
        if kind == "kind":
            return self.kind(n).lower() == v
        if kind == "state":
            return str(meta.get("state") or "").lower() == v
        if kind == "part_of":
            return v in self.ancestors(n["slug"])
        if kind == "links":
            return v in {s.strip().lower() for s in n["links"]}
        key, want = v
        return self.field(meta, key, want)


# ---------------------------------------------------------------- main

def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    head, rest = __doc__.split("\nUsage:\n", 1)  # -h shows the docstring; argparse writes the usage line
    ap = argparse.ArgumentParser(description=head, epilog=rest.split("\n", 2)[2],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="*", help="search terms (joined with spaces); -term excludes")
    ap.add_argument("--notes", action="store_true", help="list matching notes instead of entries")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--limit", type=int, help="show at most N results")
    ap.add_argument("--added", action="store_true",
                    help="with --json, fill in `added` from git even when no added-* term is used")
    # A query word such as -state:done looks like an option to argparse, so the options are picked
    # out by hand and every other word goes to the query, in order.
    opts, words, argv = [], [], sys.argv[1:]
    while argv:
        w = argv.pop(0)
        if w in ("-h", "--help", "--notes", "--json", "--added") or w.startswith("--limit="):
            opts.append(w)
        elif w == "--limit":
            opts += [w, *argv[:1]]
            argv = argv[1:]
        elif w == "--":
            words += argv
            break
        elif w.startswith("--"):
            ap.error(f"unknown option {w} (put -- before it to search for it)")
        else:
            words.append(w)
    a = ap.parse_args(opts)
    a.query = words
    if not words:
        ap.error("no query given")
    if a.limit is not None and a.limit < 0:
        fail("--limit must be 0 or more")
    if not os.path.isdir(priority.CONCORDANCE):
        fail(f"no concordance folder at {priority.CONCORDANCE} (set HARNESS_ROOT or run inside a harness)")
    try:
        tree = order(parse(" ".join(a.query)))
        since = min(added_dates(tree), default=None)
        if since:
            require_work_tree()
        errors = []
        notes = priority.load_notes(errors)
        for path, msg in errors:
            print(f"query.py: skipped {links.rel(path)}: {msg}", file=sys.stderr)
        for n in notes.values():
            with open(n["path"], encoding="utf-8") as f:
                n["text"] = f.read()
        added = Added(since)
        m = Matcher(notes, added)
        # First pass without git blame; then blame (in parallel) only the files whose entries
        # still depend on it, and decide those.
        tested = [(n, e, m.test(tree, n, e)) for s in sorted(notes) for n in [notes[s]] for e in log_entries(n)]
        added.blame({n["path"] for n, e, r in tested if r is None})
        hits = [(n, e) for n, e, r in tested if r or (r is None and m.test(tree, n, e))]
        hits.sort(key=lambda h: (h[1]["date"] is None, h[1]["date"] or datetime.date.min, h[0]["slug"]))
        report(a, hits, m, added if since or a.added else None)
    except QueryError as e:
        fail(str(e))
    sys.exit(0 if hits else 1)


def count(n, one, many=None):
    return f"{n} {one if n == 1 else many or one + 's'}"


def report(a, hits, m, added):
    """ADDED: an Added to fill in --json's `added` from, or None to leave it null."""
    if a.notes:
        counts = {}
        for n, _ in hits:
            counts[n["slug"]] = counts.get(n["slug"], 0) + 1
        rows = [(s, m.kind(m.notes[s]), m.notes[s]["meta"].get("state"), c) for s, c in sorted(counts.items())]
        shown = rows if a.limit is None else rows[:a.limit]
        if a.json:
            print(json.dumps([{"slug": s, "kind": k, "state": None if st is None else str(st), "entries": c}
                              for s, k, st, c in shown], indent=1))
            return
        w = max((len(r[0]) for r in shown), default=0)
        for s, k, st, c in shown:
            print(f"{s:{w}}  {k:8} {'-' if st is None else str(st):9} {c}")
        print(f"{count(len(rows), 'note')}, {count(len(hits), 'entry', 'entries')}" + (f" (showing {len(shown)})" if len(shown) < len(rows) else ""))
        return
    shown = hits if a.limit is None else hits[:a.limit]
    if a.json:
        try:
            if added:
                require_work_tree()
                added.blame({n["path"] for n, _ in shown}, strict=False, exact=True)
        except QueryError:  # not in git (only with --added), or git missing: leave it null
            added = None
        day = lambda n, e: added and added.date(n["path"], e["line"])
        print(json.dumps([{"slug": n["slug"], "kind": m.kind(n), "path": links.rel(n["path"]),
                           "date": e["date"].isoformat() if e["date"] else None,
                           "added": day(n, e).isoformat() if day(n, e) else None,
                           "text": entry_text(e)} for n, e in shown], indent=1, ensure_ascii=False))
        return
    w = max((len(n["slug"]) for n, _ in shown), default=0)
    for n, e in shown:
        print(f"{n['slug']:{w}}  {e['first']}")
        for line in e["rest"]:
            print(f"{'':{w}}  {line}".rstrip())
    notes = len({n["slug"] for n, _ in hits})
    print(f"{count(len(hits), 'entry', 'entries')} in {count(notes, 'note')}" + (f" (showing {len(shown)})" if len(shown) < len(hits) else ""))


if __name__ == "__main__":
    main()
