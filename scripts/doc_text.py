"""Read the thought log or the todo file as lines of text, whether it is plain text, .docx or .odt.

The format is chosen by the file's extension (any case): .docx is a Word document (also what
Google Docs exports), .odt a LibreOffice one, and anything else is plain UTF-8 text read exactly
as before, so a .txt file gives the same lines, line numbers and hashes it always has.

A document becomes one line per paragraph. Headings are paragraphs too, so a date typed as a
heading still starts a day, and an empty paragraph is an empty line, so blank paragraphs still
separate todo items. Inside a paragraph, tabs stay tabs and a soft line break (Shift+Enter)
starts a new line. Line numbers the tools report for a document count these lines.

What is kept, and why:
  - only the body: headers, footers, footnotes and comments are not part of what was written
    that day, and would put the same text into every day or between items;
  - tracked changes read as if accepted: deleted text is dropped, inserted text kept;
  - a link keeps its address, as `text <url>` (just the text when the text is the address),
    because the address is often the part worth tracking;
  - table cells become lines of their own, rows top to bottom, cells left to right.
Standard library only (zipfile and expat; ElementTree for the small .rels files): the harness
depends on nothing but PyYAML. The XML is read as a stream, so a long document is never held as a
tree in memory.

Converting a long document takes seconds, and one command can read the same file more than once
(review.py reads the thought log for mentions and again for its inline todos), so the lines are
kept twice over: in memory for the rest of the command, and on disk in .cache/ in the harness
root for the next command. Both are keyed by the file's absolute path, size and modification
time, so saving the document makes them stale at once. The cache folder ignores itself in git
(.cache/.gitignore holds `*`) and is safe to delete; a cache that cannot be written (read-only
folder, a file held open on Windows) is simply not used. Plain text is never cached.

A document that cannot be read (not a zip, a part missing, broken XML) stops the script with one
message naming the file, like a bad setting does. Nothing here ever writes to the file.
"""
import hashlib, json, os, re, sys, tempfile, zipfile, zlib
import xml.etree.ElementTree as ET
from xml.parsers import expat

DOCUMENTS = (".docx", ".odt")
# Cache files are keyed by CACHE_VERSION and a hash of this file's own source, so any change to
# the conversion rewrites old cache files even when nobody remembers to bump CACHE_VERSION.
# (Bumping it still works, e.g. to invalidate on a change elsewhere.)
CACHE_VERSION = 1
_SOURCE = []       # [hash of this file's bytes], filled at the first document read
_MEMO = {}         # key -> lines, for the life of one command
# Set HARNESS_DOC_TRACE=1 to have each document read say on stderr where its lines came from
# (parsed, cache or memory); the smoke test uses it to see the cache at work.
TRACE = bool(os.environ.get("HARNESS_DOC_TRACE"))

# WordprocessingML, in its usual (transitional) and its strict namespace.
W = {"http://schemas.openxmlformats.org/wordprocessingml/2006/main",
     "http://purl.oclc.org/ooxml/wordprocessingml/main"}
R = {"http://schemas.openxmlformats.org/officeDocument/2006/relationships",
     "http://purl.oclc.org/ooxml/officeDocument/relationships"}
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
XLINK = "http://www.w3.org/1999/xlink"


def read_lines(path):
    """The file's content as lines, each ending in "\\n" like iterating a text file does.

    Plain text is streamed straight from the file (an early `break` stops reading), decoded as
    UTF-8 with a leading byte-order mark dropped and bad bytes replaced, exactly as the tools
    always read it. A .docx or .odt is converted whole (or taken from the cache) as a list."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in DOCUMENTS:
        return _text_lines(path)
    full = os.path.normcase(os.path.abspath(path))
    st = os.stat(path)
    key = [CACHE_VERSION, _source_hash(), full, st.st_size, st.st_mtime_ns]
    memo = json.dumps(key)
    if memo in _MEMO:
        _trace("memory", path)
        return _MEMO[memo]  # shared: callers copy before changing it
    cache = _cache_file(full)
    got = _cache_read(cache, key)
    if got is not None:
        lines, warnings = got
        _trace("cache", path)
    else:
        lines, warnings = _convert(path, ext)
        _trace("parsed", path)
        _cache_write(cache, key, lines, warnings)
    for w in warnings:
        print(f"doc_text: {path}: {w}", file=sys.stderr)
    _MEMO[memo] = lines
    return lines


def _source_hash():
    """Short hash of this module's source, read once per process and only when a document is read."""
    if not _SOURCE:
        with open(os.path.abspath(__file__), "rb") as f:
            _SOURCE.append(hashlib.sha1(f.read()).hexdigest()[:12])
    return _SOURCE[0]


def _trace(source, path):
    if TRACE:
        print(f"doc_text: {source} {os.path.basename(path)}", file=sys.stderr)


def _convert(path, ext):
    """(lines, warnings) from a .docx or .odt; SystemExit naming the file when it cannot be read."""
    warnings = []
    try:
        with zipfile.ZipFile(path) as z:
            paras = _docx(z, warnings) if ext == ".docx" else _odt(z)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, KeyError, ET.ParseError, expat.ExpatError,
            zlib.error, EOFError, NotImplementedError, RuntimeError, ValueError) as e:
        # RuntimeError: an encrypted zip member; NotImplementedError: an unknown compression method.
        raise SystemExit(f"{path}: cannot read it as a {ext} document ({e}). Is it damaged or "
                         f"password-protected? Re-save it, or save a .txt copy and point the setting there.")
    return [line + "\n" for p in paras for line in p.split("\n")], warnings


# ------------------------------------------------------------------ cache

def _cache_file(full):
    """.cache/<hash of the absolute path>.json in the harness root, or None when it cannot be made.
    Creating the folder also writes .cache/.gitignore, so no harness needs a .gitignore change."""
    import config  # only here: the rest of this module does not need harness.yaml
    folder = os.path.join(config.ROOT, ".cache")
    try:
        os.makedirs(folder, exist_ok=True)
        ignore = os.path.join(folder, ".gitignore")
        if not os.path.exists(ignore):
            with open(ignore, "w", encoding="utf-8", newline="\n") as f:
                f.write("*\n")
    except OSError:
        return None
    return os.path.join(folder, "doc-" + hashlib.sha1(full.encode("utf-8")).hexdigest()[:16] + ".json")


def _cache_read(cache, key):
    """(lines, warnings) when CACHE holds this exact KEY; None when missing, stale or damaged.
    The lines are a JSON list, so tabs, trailing spaces, empty lines and carriage returns inside
    a paragraph come back exactly."""
    if not cache:
        return None
    try:
        with open(cache, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("key") != key:
            return None
        lines, warnings = data["lines"], data.get("warnings", [])
        if isinstance(lines, list) and all(isinstance(l, str) and l.endswith("\n") for l in lines) \
                and isinstance(warnings, list):
            return lines, warnings
    except (OSError, ValueError, AttributeError, KeyError, TypeError):
        pass
    return None


def _cache_write(cache, key, lines, warnings):
    """Write to a temp file next to the cache, then swap it in, so a reader never sees half a file.
    Any failure (a read-only folder; on Windows, another process holding the file open) just
    leaves the cache out: the next command converts the document again."""
    if not cache:
        return
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(cache), prefix=".tmp-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"key": key, "lines": lines, "warnings": warnings}, f, ensure_ascii=False)
        os.replace(tmp, cache)
        tmp = None
    except OSError:
        pass
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def _text_lines(path):
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        yield from f


def _same(text, url):
    """True when a link's text is already its address, so writing both would only repeat it."""
    t = text.strip()
    if url.lower().startswith("mailto:") and t == url[7:]:
        return True
    bare = lambda u: u[:-1] if u.endswith("/") else u  # https://x.org/a/ is written as https://x.org/a
    return bare(t) == bare(url)


class _Paragraphs:
    """Collects paragraph texts from a stream of XML events. Subclasses say which elements are
    paragraphs, which hold text, and which are skipped with everything inside them."""

    def __init__(self):
        self.out = []      # finished paragraphs, in document order
        self.paras = []    # open paragraphs (a text box can hold paragraphs inside a paragraph)
        self.links = []    # open links: (url or None, paragraph pieces, where the link text starts)
        self.path = []     # (namespace, local name) of each open element
        self.skip = 0      # > 0 while inside a skipped element: depth below it

    def parse(self, stream):
        p = expat.ParserCreate(namespace_separator=" ")
        p.buffer_text = True  # text in one piece per run, not split at every entity and buffer edge
        p.StartElementHandler, p.EndElementHandler = self._start, self._end
        p.CharacterDataHandler = self._chars

        def refuse(*_):  # office files never declare entities; one that does is not one of them
            raise ValueError("the XML declares entities")
        p.EntityDeclHandler = refuse
        p.ParseFile(stream)
        return self

    def _start(self, name, attrs):
        ns, _, local = name.rpartition(" ")
        self.path.append((ns, local))
        if self.skip:
            self.skip += 1
            self.skipped(ns, local)
        elif self.skips(ns, local, attrs):
            self.skip = 1
        else:
            self.start(ns, local, attrs)

    def _end(self, name):
        ns, _, local = name.rpartition(" ")
        self.path.pop()
        if self.skip:
            self.skip -= 1
        else:
            self.end(ns, local)

    def _chars(self, data):
        if not self.skip and self.paras:
            self.chars(data)

    def skipped(self, ns, local):
        pass

    def add(self, text):
        if self.paras:
            self.paras[-1].append(text)

    def open_para(self):
        self.paras.append([])

    def close_para(self):
        self.out.append("".join(self.paras.pop()))

    def open_link(self, url):
        pieces = self.paras[-1] if self.paras else []
        self.links.append((url, pieces, len(pieces)))

    def close_link(self):
        url, pieces, at = self.links.pop()
        self.wrap(url, pieces, at)

    def wrap(self, url, pieces, at):
        """Write the address after a link's text, unless the text already is the address."""
        if not url:
            return
        text = "".join(pieces[at:])
        if not text.strip():
            pieces.append(f"<{url}>")
        elif not _same(text, url):
            pieces.append(f" <{url}>")


# ------------------------------------------------------------------ .docx

def _part_rels(z, part):
    """Id -> (target, type, external?) from a part's relationships file; {} when it has none."""
    folder, name = os.path.split(part)
    try:
        data = z.read(f"{folder}/_rels/{name}.rels" if folder else f"_rels/{name}.rels")
    except KeyError:
        return {}
    out = {}
    for rel in ET.fromstring(data):
        if rel.tag.rpartition("}")[2] == "Relationship":
            out[rel.get("Id")] = (rel.get("Target", ""), rel.get("Type", ""), rel.get("TargetMode") == "External")
    return out


def _is_address(target):
    """True for a link target that is an address (has a scheme like https: or mailto:, or is a
    network path), not a part inside the document such as media/image1.png."""
    return bool(re.match(r"[A-Za-z][A-Za-z0-9+.\-]*:|//|\\\\", target))


def _docx(z, warnings):
    # The main part is named by the package's own relationships; it is word/document.xml in practice.
    main = next((t.lstrip("/") for t, kind, _ in _part_rels(z, "").values() if kind.endswith("/officeDocument")),
                "word/document.xml")
    # Some writers leave TargetMode="External" off a hyperlink; its target still reads as an address.
    links = {i: t for i, (t, kind, external) in _part_rels(z, main).items()
             if external or (kind.endswith("/hyperlink") and _is_address(t))}
    with z.open(main) as f:
        reader = _Docx(links).parse(f)
    if reader._hiding():
        # A field opened and never closed (damaged, or half of it deleted with tracked changes):
        # everything after it was taken for field code. Read again with field codes ignored, so
        # the rest of the log is never lost. Instructions are still never output, but HYPERLINK
        # fields lose their address and a result nested in another field's instruction (inside
        # IF, say) shows as text.
        warnings.append("a Word field is never closed; the document was read with field codes ignored "
                        "(links made with fields lose their address; text inside IF fields may show)")
        with z.open(main) as f:
            reader = _Docx(links, fields=False).parse(f)
    return reader.finish()


def _hyperlink_field(instr):
    """The address of a HYPERLINK field instruction, None for any other field or an internal link.
    e.g. HYPERLINK "https://x.org/a" \\o "tooltip"   or   HYPERLINK \\l "_Toc1" (internal)."""
    m = re.match(r'\s*HYPERLINK\b(.*)', instr, re.S | re.I)
    if not m:
        return None
    url = anchor = None
    tokens = [q if q or not u else u for q, u in re.findall(r'"([^"]*)"|(\S+)', m.group(1))]
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.startswith("\\") and len(t) == 2:
            if t.lower() in ("\\l", "\\o", "\\t") and i + 1 < len(tokens):
                if t.lower() == "\\l":
                    anchor = tokens[i + 1]
                i += 1
        elif url is None:
            url = t
        i += 1
    if url and anchor and "#" not in url:
        url += "#" + anchor
    return url


class _Docx(_Paragraphs):
    """word/document.xml: w:p paragraphs, w:t text, tracked deletions and field codes left out."""

    # Skipped with everything inside: deleted text, paragraph and run properties (tab stops would
    # otherwise read as tabs), content-control settings, and the fallback copy of a text box.
    SKIP = {"del", "moveFrom", "delText", "delInstrText", "pPr", "rPr", "sdtPr", "sdtEndPr"}

    def __init__(self, links, fields=True):
        super().__init__()
        self.rels = links
        self.use_fields = fields  # False: fldChar is ignored (the fallback for a field never closed)
        self.page_end = {}  # id(paragraph pieces) -> length just after its latest page-break newline
        self.body = False
        self.fields = []    # open complex fields: {"instr": [...], "result": (pieces, at) or None}
        self.carry = ""     # text of a paragraph whose mark was deleted: it runs on into the next
        self.joined = False
        self.instr = False  # inside w:instrText

    def skips(self, ns, local, attrs):
        return (ns in W and local in self.SKIP) or (ns == MC and local == "Fallback")

    def skipped(self, ns, local):
        # A deleted paragraph mark (w:pPr/w:rPr/w:del) joins this paragraph to the next one.
        if (ns in W and local in ("del", "moveFrom") and len(self.paras) == 1
                and [n for _, n in self.path[-3:-1]] == ["pPr", "rPr"]):
            self.joined = True

    def _hiding(self):
        """True while inside a field's instruction: field codes are never content."""
        return any(f["result"] is None for f in self.fields)

    def _linked(self):
        """True inside a link that already has an address: an inner one would print it twice."""
        return any(url for url, _, _ in self.links) or any(f.get("url") for f in self.fields)

    def start(self, ns, local, attrs):
        if ns not in W:
            return
        if local == "body":
            self.body = True
        elif not self.body:
            return
        elif local == "p":
            self.open_para()
            if len(self.paras) == 1:
                if self.carry:
                    self.add(self.carry)
                self.carry, self.joined = "", False
        elif local == "tbl" and self.carry and not self.paras:
            self.out.append(self.carry)  # a joined paragraph does not run on into a table's first cell
            self.carry = ""
        elif local == "hyperlink":  # opened even inside a field code, so that end() pops the right one
            rid = next((v for k, v in attrs.items() if k.rpartition(" ")[0] in R and k.endswith(" id")), None)
            url = self.rels.get(rid) if rid and not self._hiding() and not self._linked() else None
            anchor = next((v for k, v in attrs.items() if k.rpartition(" ")[0] in W and k.endswith(" anchor")), None)
            if url and anchor and "#" not in url:
                url += "#" + anchor
            self.open_link(url)
        elif local == "fldSimple":
            self.open_link(None if self._hiding() or self._linked()
                           else _hyperlink_field(self._attr(attrs, "instr") or ""))
        elif self._hiding():
            if local == "instrText":
                self.instr = True
        elif local in ("tab", "ptab"):
            self.add("\t")
        elif local == "br":
            if self._attr(attrs, "type") == "page":
                self._page_break()
            else:
                self.add("\n")
        elif local == "cr":
            self.add("\n")
        elif local == "noBreakHyphen":
            self.add("-")
        if local == "fldChar" and self.use_fields:
            self._field(self._attr(attrs, "fldCharType"))

    def _page_break(self):
        """A hard page break starts a new line (a date typed after one must still start a day),
        but never adds a blank line at either end of a paragraph: blank lines separate todo items.
        The start is handled here, the end when the paragraph closes."""
        if not self.paras:
            return
        pieces = self.paras[-1]
        text = "".join(pieces)
        if text and not text.endswith("\n"):
            pieces.append("\n")
            self.page_end[id(pieces)] = len(pieces)

    def _close_page(self, pieces):
        at = self.page_end.pop(id(pieces), None)
        if at is not None and not "".join(pieces[at:]):
            del pieces[at - 1]  # nothing followed the page break in this paragraph

    @staticmethod
    def _attr(attrs, local):
        return next((v for k, v in attrs.items() if k.rpartition(" ")[2] == local), None)

    def _field(self, kind):
        if kind == "begin":
            self.fields.append({"instr": [], "result": None})
        elif kind == "separate" and self.fields:
            f = self.fields[-1]
            hidden_or_linked = self._hiding_outside(f) or self._linked()
            pieces = self.paras[-1] if self.paras else []
            f["result"] = (pieces, len(pieces))
            # The address is written only for a field whose text shows (not one nested in another
            # field's instruction, e.g. inside IF) and that is not already inside a link with one.
            f["url"] = None if hidden_or_linked else _hyperlink_field("".join(f["instr"]))
        elif kind == "end" and self.fields:
            f = self.fields.pop()
            if f["result"] is not None and f.get("url"):
                pieces, at = f["result"]
                if self.paras and pieces is not self.paras[-1]:  # the result ran over a paragraph end
                    pieces, at = self.paras[-1], 0
                self.wrap(f["url"], pieces, at)

    def _hiding_outside(self, field):
        return any(f["result"] is None for f in self.fields if f is not field)

    def end(self, ns, local):
        if ns not in W or not self.body:
            return
        if local == "body":
            self.body = False
        elif local == "p" and self.paras:
            self._close_page(self.paras[-1])
            if self.joined and len(self.paras) == 1:
                self.carry = "".join(self.paras.pop())
            else:
                self.close_para()
        elif local == "instrText":
            self.instr = False
        elif local in ("hyperlink", "fldSimple") and self.links:
            self.close_link()

    def chars(self, data):
        ns, local = self.path[-1]
        if not self.instr and not (ns in W and local == "t"):
            return
        if self.instr:
            if self.fields:
                self.fields[-1]["instr"].append(data)
        elif not self._hiding():
            self.add(data)

    def finish(self):
        if self.carry:
            self.out.append(self.carry)
        return self.out


# ------------------------------------------------------------------ .odt

def _odt(z):
    with z.open("content.xml") as f:
        return _Odt().parse(f).out


class _Odt(_Paragraphs):
    """content.xml: text:p and text:h paragraphs inside office:text."""

    # The tracked-changes log (it holds the deleted text), comments, and footnotes/endnotes.
    SKIP = {(TEXT, "tracked-changes"), (TEXT, "changed-region"), (OFFICE, "annotation"), (TEXT, "note")}

    def __init__(self):
        super().__init__()
        self.body = 0
        self.ws = True  # the last character written was white space, or the line just started

    def skips(self, ns, local, attrs):
        return (ns, local) in self.SKIP

    def start(self, ns, local, attrs):
        if ns == OFFICE and local == "text":
            self.body += 1
        if not self.body or ns != TEXT:
            return
        if local in ("p", "h"):
            self.open_para()
            self.ws = True
        elif local == "s":
            self.add(" " * int(attrs.get(f"{TEXT} c") or 1))
            self.ws = False
        elif local == "tab":
            self.add("\t")
            self.ws = False
        elif local == "line-break":
            self.add("\n")
            self.ws = True
        elif local == "a":
            href = attrs.get(f"{XLINK} href") or ""
            self.open_link(None if href.startswith("#") else href)  # "#..." is a place in this document

    def end(self, ns, local):
        if ns == OFFICE and local == "text":
            self.body -= 1
        if not self.body or ns != TEXT:
            return
        if local in ("p", "h") and self.paras:
            self.close_para()
        elif local == "a" and self.links:
            self.close_link()

    def chars(self, data):
        # ODF collapses runs of spaces, tabs and newlines in the XML to one space and drops it at the
        # start of a paragraph; real repeated spaces and tabs are written as text:s and text:tab.
        if not self.body or self.path[-1][0] != TEXT:  # e.g. a drawing's title inside a paragraph
            return
        data = re.sub(r"[ \t\r\n]+", " ", data)
        if self.ws and data.startswith(" "):
            data = data[1:]
        if data:
            self.add(data)
            self.ws = data.endswith(" ")
