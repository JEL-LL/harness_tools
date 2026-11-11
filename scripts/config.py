"""Where the harness lives and what it is connected to, read from an optional harness.yaml.

The harness root is, in order:
  1. the HARNESS_ROOT environment variable,
  2. the nearest folder at or above the current directory that holds harness.yaml,
  3. the folder above this scripts/ folder (scripts copied into the harness itself).

The settings file is HARNESS_CONFIG when that environment variable is set, else harness.yaml in
the root. Relative paths in it are taken from the root either way.

Every setting is optional, and a key set to nothing (`key:`) means "use the default". With no
harness.yaml at all you get the core: notes in concordance/, ranking, links, and reviews. The
thought log and todo add-ons switch on only when their files are configured, and everything
else keeps working without them. See harness.example.yaml (repo root) for every key.

The whole file is checked against SCHEMA when it is loaded: an unknown key (usually a typo), a
section that is not a mapping, or a value of the wrong kind stops every script with one message
naming the key, rather than switching a feature off silently or failing later with a traceback.
"""
import os, re

import yaml

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
CONFIG_NAME = "harness.yaml"


def _find_root():
    env = os.environ.get("HARNESS_ROOT")
    if env:
        return os.path.abspath(env)
    d = os.getcwd()
    while True:
        if os.path.isfile(os.path.join(d, CONFIG_NAME)):
            return d
        up = os.path.dirname(d)
        if up == d:
            break
        d = up
    return os.path.dirname(SCRIPTS)


ROOT = _find_root()
_path = os.environ.get("HARNESS_CONFIG") or os.path.join(ROOT, CONFIG_NAME)
if os.environ.get("HARNESS_CONFIG") and not os.path.isfile(_path):
    raise SystemExit(f"HARNESS_CONFIG points at {_path}, which does not exist")
CFG = {}
if os.path.isfile(_path):
    try:
        with open(_path, encoding="utf-8-sig") as f:
            CFG = yaml.safe_load(f) or {}
    except UnicodeDecodeError as e:
        raise SystemExit(f"{_path}: not UTF-8 text ({e}); save it as UTF-8")
    except yaml.YAMLError as e:
        raise SystemExit(f"{_path}: not valid YAML: {e}")
    if not isinstance(CFG, dict):
        raise SystemExit(f"{_path}: expected a mapping at the top level")


def _bad(key, want):
    raise SystemExit(f"{_path}: setting '{key}' should be {want}")


# ---------- schema: each checker returns None when the value is fine, else what it should be ----------

def _text(v):
    return None if isinstance(v, str) else "text (quote it if it looks like a number)"


def _path_value(v):
    return None if isinstance(v, str) and v.strip() else "a non-empty path"


def _boolean(v):
    return None if isinstance(v, bool) else "true or false (unquoted)"


def _kinds(v):
    ok = isinstance(v, dict) and v and all(isinstance(k, str) and k.strip() and isinstance(t, str) and t.strip()
                                          for k, t in v.items())
    return None if ok else "a mapping of folder name to type, e.g. {projects: project}"


def _date_line(v):
    if not isinstance(v, str):
        return "a regular expression in quotes"
    try:
        groups = re.compile(v).groups
    except re.error as e:
        return f"a valid regular expression ({e})"
    return None if groups == 3 else f"a regex with exactly three groups (day, month, year in some order); it has {groups}"


def _date_order(v):
    ok = isinstance(v, str) and sorted(v.lower()) == ["d", "m", "y"]
    return None if ok else "the letters d, m and y in the order date_line's groups appear, e.g. mdy"


def _weekdays(v):
    ok = isinstance(v, list) and all(isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in v)
    return None if ok else "a list of weekday numbers 0-6 (0 = Monday), e.g. [5]"


def _repo_ok(r):
    if not isinstance(r, dict) or not set(r) <= {"path", "name", "main", "stale_days"}:
        return False
    if not (isinstance(r.get("path"), str) and r["path"].strip()):
        return False
    if any(r.get(k) is not None and not isinstance(r[k], str) for k in ("name", "main")):
        return False
    sd = r.get("stale_days")
    return sd is None or (isinstance(sd, int) and not isinstance(sd, bool) and sd >= 0)


def _repos(v):
    ok = isinstance(v, list) and all(_repo_ok(r) for r in v)
    return None if ok else ("a list of mappings with keys path (required), name and main (text), "
                            "stale_days (a whole number of days), e.g. [{path: ~/code/app, main: main}]")


SCHEMA = {
    "owner": _text,
    "concordance": _path_value,
    "scans": _path_value,
    "pins": _path_value,
    "reviews": _path_value,
    "kinds": _kinds,
    "thought_log": {"path": _path_value, "date_line": _date_line, "date_order": _date_order,
                    "inline_todo_marker": _text, "suspect_weekdays": _weekdays},
    "todo": {"path": _path_value, "split_marker": _text},
    "aging": {"count_log_entries": _boolean},
    "review": {"repos": _repos},
}


def _check(cfg, schema, prefix=""):
    for key, value in cfg.items():
        name = f"{prefix}{key}"
        if key not in schema:
            known = ", ".join(sorted(schema))
            raise SystemExit(f"{_path}: unknown setting '{name}' (known here: {known})")
        if value is None:
            continue  # `key:` with nothing after it means "use the default"
        rule = schema[key]
        if isinstance(rule, dict):
            if not isinstance(value, dict):
                _bad(name, f"a section (mapping) with keys {', '.join(rule)}")
            _check(value, rule, name + ".")
        else:
            want = rule(value)
            if want:
                _bad(name, want)


_check(CFG, SCHEMA)


def _get(*keys, default=None):
    v = CFG
    for k in keys:
        if not isinstance(v, dict) or k not in v:
            return default
        v = v[k]
    return default if v is None else v


def resolve(p):
    """A configured path: ~ and environment variables expanded, relative paths taken from ROOT."""
    if not p:
        return None
    p = os.path.expandvars(os.path.expanduser(str(p)))
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


OWNER = _get("owner", default="the owner")

CONCORDANCE = os.environ.get("HARNESS_CONCORDANCE") or resolve(_get("concordance", default="concordance"))
SCANS = resolve(_get("scans", default="scans"))
PINS = resolve(_get("pins", default=os.path.join("pins", "todo.jsonl")))
REVIEWS = resolve(_get("reviews", default="reviews"))

# Note kinds: folder name -> type written into new notes.
KINDS = _get("kinds", default={
    "projects": "project", "hardware": "hardware", "machines": "machine", "people": "person",
    "concepts": "concept", "places": "place", "orgs": "org"})

# ---- thought log add-on (optional) ----
THOUGHT_LOG = resolve(_get("thought_log", "path"))
# A day starts at a line matching this regex; its groups are read in DATE_ORDER (e.g. "mdy").
DATE_LINE = _get("thought_log", "date_line", default=r"^(\d{2})/(\d{2})/(\d{4})\s*$")
DATE_ORDER = _get("thought_log", "date_order", default="mdy").lower()
# Everything after a line starting with this marker is an inline todo list, not a day.
INLINE_TODO = _get("thought_log", "inline_todo_marker", default=None)
# Weekdays (0 = Monday ... 6 = Sunday) on which a date stamp is probably a typo.
SUSPECT_WEEKDAYS = _get("thought_log", "suspect_weekdays", default=[])

# ---- todo add-on (optional) ----
TODO = resolve(_get("todo", "path"))
# In the todo file, everything above the line starting with this marker is done, everything below is open.
# With no marker configured, the whole file is open. A configured marker that is missing from the
# file makes everything done (and todo_items warns), so a deleted marker never floods the open list.
TODO_SPLIT = _get("todo", "split_marker", default=None)


def thought_log_enabled():
    return bool(THOUGHT_LOG) and os.path.isfile(THOUGHT_LOG)


def todo_enabled():
    return bool(TODO) and os.path.isfile(TODO)


def missing_sources():
    """Configured add-on files that do not exist, as (setting, path) pairs."""
    out = []
    if THOUGHT_LOG and not os.path.isfile(THOUGHT_LOG):
        out.append(("thought_log.path", THOUGHT_LOG))
    if TODO and not os.path.isfile(TODO):
        out.append(("todo.path", TODO))
    return out


# ---- aging ----
# Without a usable thought log (none configured, or its file missing), a note's newest dated
# "## Log" bullet counts as its last touch.
COUNT_LOG_ENTRIES = _get("aging", "count_log_entries", default=not thought_log_enabled())

# ---- review ----
# Repos whose long-lived branches the review lists: [{path, name, main, stale_days}]
REVIEW_REPOS = _get("review", "repos", default=[])
