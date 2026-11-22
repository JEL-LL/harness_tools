"""Helpers shared by tests/smoke_test.py and tests/smoke_edges.py: checks, and harness copies to run scripts on."""
import hashlib, os, re, shutil, subprocess, sys, zipfile
from xml.sax.saxutils import escape

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


# ---------------------------------------------------------------- documents built at test time
# The smallest .docx and .odt that Word and LibreOffice open, written with zipfile so the repo
# holds no binary files. BODY is the XML inside w:body or office:text.

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


def write_docx(path, body, links=None, no_mode=()):
    """LINKS: relationship id -> external address, for w:hyperlink r:id. Ids in NO_MODE are written
    without TargetMode="External", as some generators do."""
    mode = lambda i: "" if i in no_mode else ' TargetMode="External"'
    rels = "".join(f'<Relationship Id="{i}" Type="{R_NS}/hyperlink" Target="{escape(u)}"{mode(i)}/>'
                   for i, u in (links or {}).items())
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" '
                   'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels", f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{PKG}">'
                   f'<Relationship Id="rId1" Type="{R_NS}/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                   f'<w:document xmlns:w="{W_NS}" xmlns:r="{R_NS}"><w:body>{body}</w:body></w:document>')
        z.writestr("word/_rels/document.xml.rels",
                   f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{PKG}">{rels}</Relationships>')


def write_odt(path, body):
    mime = "application/vnd.oasis.opendocument.text"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mimetype"), mime, compress_type=zipfile.ZIP_STORED)  # first, uncompressed
        z.writestr("META-INF/manifest.xml",
                   '<?xml version="1.0" encoding="UTF-8"?><manifest:manifest '
                   'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">'
                   f'<manifest:file-entry manifest:full-path="/" manifest:media-type="{mime}"/>'
                   '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
                   '</manifest:manifest>')
        z.writestr("content.xml",
                   '<?xml version="1.0" encoding="UTF-8"?>\n<office:document-content '
                   'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                   'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
                   'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
                   'xmlns:dc="http://purl.org/dc/elements/1.1/" '
                   'xmlns:xlink="http://www.w3.org/1999/xlink" office:version="1.2">'
                   f'<office:body><office:text>{body}</office:text></office:body></office:document-content>')


def docx_paras(text, heading=None):
    """One w:p per line of TEXT; lines matching the regex HEADING are styled as headings."""
    out = []
    for line in text.split("\n")[:-1] if text.endswith("\n") else text.split("\n"):
        style = '<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>' if heading and re.match(heading, line) else ""
        run = f'<w:r><w:t xml:space="preserve">{escape(line)}</w:t></w:r>' if line else ""
        out.append(f"<w:p>{style}{run}</w:p>")
    return "".join(out)


def odt_paras(text, heading=None):
    """One text:p (or text:h, for lines matching HEADING) per line of TEXT."""
    out = []
    for line in text.split("\n")[:-1] if text.endswith("\n") else text.split("\n"):
        body = re.sub(r"  +", lambda m: f' <text:s text:c="{len(m.group(0)) - 1}"/>', escape(line))
        body = re.sub(r"^ ", "<text:s/>", body).replace("\t", "<text:tab/>")
        tag = 'text:h text:outline-level="1"' if heading and re.match(heading, line) else "text:p"
        out.append(f"<{tag}>{body}</{tag.split()[0]}>")
    return "".join(out)
