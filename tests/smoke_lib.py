"""Helpers shared by tests/smoke_test.py and tests/smoke_edges.py: checks, and harness copies to run scripts on."""
import hashlib, os, re, shutil, subprocess, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "scripts")
EXAMPLE = os.path.join(REPO, "example")
ALL_SCRIPTS = ["config", "note", "priority", "links", "review", "thought_log", "todo_items"]
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok or not detail else f"\n      {detail}"))
    return ok


def skip(name, why):
    print(f"SKIP  {name} ({why})")


def tree_hash(top):
    h = hashlib.sha1()
    for d, dirs, files in sorted(os.walk(top)):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(d, f)
            h.update(os.path.relpath(p, top).encode())
            with open(p, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()


class Harness:
    """A fresh copy of example/ in a temp folder, and a way to run scripts against it."""

    def __init__(self, tmp, name):
        self.tmp = tmp
        self.root = os.path.join(tmp, name)
        shutil.copytree(EXAMPLE, self.root)
        self.cwd = tmp  # deliberately not the root: HARNESS_ROOT alone must find it

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def run(self, script, *args, env=None):
        """Run scripts/SCRIPT.py with HARNESS_ROOT set to this copy (plus any extra env).
        -B: no __pycache__ is written into scripts/."""
        e = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
        e.update(HARNESS_ROOT=self.root, PYTHONIOENCODING="utf-8", **(env or {}))
        return subprocess.run([sys.executable, "-B", os.path.join(SCRIPTS, script + ".py"), *args],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              env=e, cwd=self.cwd)

    def expect(self, name, script, *args, has=(), lacks=(), code=0, where="stdout", env=None):
        """Run, then check the exit code (a number, or "nonzero") and what the output has / lacks."""
        p = self.run(script, *args, env=env)
        out = p.stdout if where == "stdout" else p.stderr
        bad = []
        if (code == "nonzero" and p.returncode == 0) or (code != "nonzero" and p.returncode != code):
            bad.append(f"exit {p.returncode}, wanted {code}")
        bad += [f"missing {s!r}" for s in has if s not in out]
        bad += [f"unexpected {s!r}" for s in lacks if s in out]
        if "Traceback" in p.stderr:
            bad.append("Traceback in stderr")
        detail = "; ".join(bad) + f"\n      stdout: {p.stdout[-600:]!r}\n      stderr: {p.stderr[-600:]!r}"
        check(name, not bad, detail)
        return p

    def read(self, *parts):
        with open(self.path(*parts), encoding="utf-8") as f:
            return f.read()

    def write(self, rel, text):
        with open(self.path(rel), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    def edit(self, rel, old, new):
        """Replace the one occurrence of OLD in a file of the copy; returns the original text."""
        text = self.read(rel)
        assert text.count(old) == 1, f"{rel}: {old!r} occurs {text.count(old)} times"
        self.write(rel, text.replace(old, new))
        return text


def lines(p):
    return [l for l in p.stdout.splitlines() if l.strip()]


def ranked(p):
    """Slugs of the score rows in priority.py's ranked list (not the reason lines under them)."""
    return re.findall(r"^\s*-?\d+\.\d  (\S+)", p.stdout, re.M)
