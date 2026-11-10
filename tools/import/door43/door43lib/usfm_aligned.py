"""Parse word-aligned USFM 3 (unfoldingWord style) into Bible-module records.

Design section 7, "Parse".  Mirrors sword2bible's text rules (sword_common.cpp):
words are split on ASCII whitespace and joined with single spaces (a ``flushWord``
only fires on whitespace, so ``L’`` + ``\\w ancien\\w*`` is one word), and the
``formatting`` JSON uses the same keys:

    {"v":1,"block":{"paragraph_start":true,"lines":[{"start":0,"end":8,"level":1}],
                    "heading":"..."},
     "spans":[{"type":"words_of_christ","start":6,"end":17}]}

Word offsets are 0-based and inclusive.  Public API: ``parse_book``,
``parse_book_file``, ``book_code``, ``dedupe_books``, the result classes and
``DuplicateBookError``.  ``\\f``/``\\x`` notes are dropped and counted.
"""
import json
import re

__all__ = ["parse_book", "parse_book_file", "book_code", "dedupe_books",
           "BookResult", "VerseResult", "AlignRow", "DuplicateBookError"]

_WS = " \t\n\r\x0b\x0c"

_TOK = re.compile(
    r"\\zaln-s\s*\|(?P<zaln>.*?)\\\*"
    r"|\\zaln-e\s*\\\*"
    r"|\\[A-Za-z0-9]+-[se]\s*(?:\|[^\\]*)?\\\*"
    r"|\\(?P<mk>\+?[A-Za-z][A-Za-z0-9]*)(?P<star>\*?)"
    r"|\\\*",
    re.S)
_ATTR = re.compile(r'([A-Za-z0-9_-]+)\s*=\s*"([^"]*)"')
_VNUM = re.compile(r"\s*(\d+)(?:\s*-\s*(\d+))?([a-z]?)[ \t]?")
_CNUM = re.compile(r"\s*(\d+)")
_INNER = re.compile(r"\\\+?[A-Za-z][A-Za-z0-9]*\*?")

# paragraph markers that open a paragraph
_PARA = {"p", "pi", "pi1", "pi2", "pi3", "pc", "pr", "po", "pm", "pmo", "pmc", "pmr", "ph", "ph1", "ph2", "ph3"}
# line markers that only end poetry / prose continuation
_PLAIN = {"m", "mi", "nb", "b", "cls", "li", "li1", "li2", "li3", "li4", "lim", "lim1", "lim2", "lim3",
          "lh", "lf"}
_POETRY = {"q": 1, "q1": 1, "q2": 2, "q3": 3, "q4": 3, "qr": 1, "qc": 1, "qd": 1,
           "qm": 1, "qm1": 1, "qm2": 2, "qm3": 3}
_HEADING = {"s", "s1", "s2", "s3", "s4", "s5", "d", "ms", "ms1", "ms2", "ms3"}
_IGNORE_LINE = {"id", "ide", "usfm", "h", "h1", "h2", "h3", "toc1", "toc2", "toc3", "mt", "mt1", "mt2",
                "mt3", "mt4", "mte", "mte1", "mte2", "imt", "is", "ip", "ib", "im", "io", "iot", "ior",
                "ie", "r", "mr", "sr", "rem", "cl", "cd", "sp", "lit", "restore", "periph", "sts",
                "cc", "qa", "ili", "ipi", "imi", "ipq", "imq", "ipr", "iq", "ili1", "iq1", "iq2",
                "imt1", "imt2", "is1", "is2", "io1", "io2", "io3", "ib"}
_NOTES = {"f", "fe", "x", "ef", "ex"}          # dropped and counted
_DROPPED = {"fig", "ca", "va", "vp", "rq", "cp"}  # dropped silently
_WJ = {"wj"}


class DuplicateBookError(ValueError):
    pass


class AlignRow:
    """One x-content group of a verse (``interlinear_word`` row source)."""

    def __init__(self, strongs_raw, lemma, morph, x_content, occurrence, occurrences):
        self.strongs_raw = strongs_raw
        self.lemma = lemma
        self.morph = morph
        self.x_content = x_content
        self.occurrence = occurrence
        self.occurrences = occurrences
        self.word_indexes = []
        self.spans = []            # contiguous (start, end) runs, ascending
        self.strongs_number = None  # normalised via strongs.normalize_strong
        self.strongs_meta = {}

    @property
    def start(self):
        return self.spans[0][0]

    @property
    def end(self):
        return self.spans[0][1]

    @property
    def extra_word_positions(self):
        """``"12-14,18"`` for spans after the first, or None."""
        extra = self.spans[1:]
        if not extra:
            return None
        return ",".join(str(a) if a == b else "%d-%d" % (a, b) for a, b in extra)

    def gloss(self, words):
        return " ".join(words[i] for i in self.word_indexes if i < len(words))

    def __repr__(self):
        return "AlignRow(%r, %r, spans=%r)" % (self.strongs_raw, self.x_content, self.spans)


class VerseResult:
    def __init__(self, chapter, verse, verse_end=None):
        self.chapter = chapter
        self.verse = verse
        self.verse_end = verse_end or verse
        self.words = []
        self.rows = []
        self.paragraph_start = False
        self.lines = []            # [{"start","end","level"}]
        self.heading = ""
        self.spans = []            # [{"type","start","end"}]
        self.footnotes_dropped = 0

    @property
    def text(self):
        return " ".join(self.words)

    @property
    def blocks(self):
        """The ``formatting.block`` dict (possibly empty)."""
        b = {}
        if self.paragraph_start:
            b["paragraph_start"] = True
        if self.lines:
            b["lines"] = list(self.lines)
        if self.heading:
            b["heading"] = self.heading
        return b

    def formatting(self):
        """The ``formatting`` dict as sword2bible writes it, or None."""
        f = {"v": 1}
        if self.blocks:
            f["block"] = self.blocks
        if self.spans:
            f["spans"] = list(self.spans)
        return f if len(f) > 1 else None

    def formatting_json(self):
        f = self.formatting()
        return None if f is None else json.dumps(f, ensure_ascii=False, separators=(",", ":"))


class BookResult:
    def __init__(self):
        self.code = None
        self.chapters = {}
        self.footnotes_dropped = 0
        self.warnings = []

    def verse_count(self):
        return sum(len(v) for v in self.chapters.values())


def _attrs(s):
    return dict(_ATTR.findall(s))


def _int(s):
    return int(s) if s and s.isdigit() else None


def _normalize(raw):
    try:
        from . import strongs
    except ImportError:  # strongs.py not available: rows keep strongs_raw only
        return None, {}
    return strongs.normalize_strong(raw)


class _Parser:
    def __init__(self, text):
        self.text = text
        self.book = BookResult()
        self.sink = "ignore"
        self.chapter = 0
        self.pending_para = False
        self.pending_heading = []
        self.heading_buf = ""
        self.poetry = 0        # level carried while poetry continues
        self.v = None          # open verse state or None

    # ---- verse lifecycle -------------------------------------------------
    def open_verse(self, num, end):
        self.close_verse()
        vr = VerseResult(self.chapter, num, end)
        st = {"vr": vr, "buf": "", "idx": None, "zstack": [], "groups": [], "wj": [],
              "starts": [], "start_level": self.poetry, "trimmed_synth": False}
        if self.pending_para:
            vr.paragraph_start = True
            self.pending_para = False
        if self.pending_heading:
            vr.heading = " ".join(self.pending_heading)
            self.pending_heading = []
        if self.poetry:
            st["starts"].append((0, self.poetry, False))
        self.v = st
        self.sink = "verse"

    def next_word_index(self):
        st = self.v
        return len(st["vr"].words)

    def flush_word(self):
        st = self.v
        if st["buf"]:
            st["vr"].words.append(st["buf"])
            st["buf"] = ""

    def add_text(self, chunk, indexes=None):
        st = self.v
        for ch in chunk:
            if ch in _WS:
                self.flush_word()
            else:
                st["buf"] += ch
                if indexes is not None:
                    indexes.add(len(st["vr"].words))

    def close_verse(self):
        st = self.v
        if st is None:
            return
        self.v = None
        vr = st["vr"]
        if st["buf"]:
            vr.words.append(st["buf"])
            st["buf"] = ""
        n = len(vr.words)
        # unterminated groups / red-letter spans: close at the last word
        for g in st["zstack"]:
            st["groups"].append(g)
        for start in st["wj"]:
            if n and start <= n - 1:
                vr.spans.append({"type": "words_of_christ", "start": start, "end": n - 1})
        vr.spans.sort(key=lambda s: (s["start"], s["end"]))
        # poetry lines
        starts = list(st["starts"])
        trimmed_synth = False
        while starts and starts[-1][0] >= n:
            starts.pop()
            trimmed_synth = True
        if n and starts:
            if len(starts) == 1 and starts[0][2] and trimmed_synth and st["start_level"] == 0:
                starts = []
        if n and starts:
            starts[0] = (0, starts[0][1], starts[0][2])
            for i, (s, lvl, _) in enumerate(starts):
                e = starts[i + 1][0] - 1 if i + 1 < len(starts) else n - 1
                vr.lines.append({"start": s, "end": e, "level": lvl})
        # alignment rows (merge groups sharing x-content + occurrence)
        merged = {}
        order = []
        for g in sorted(st["groups"], key=lambda g: g["seq"]):  # outer group first
            if not g["idx"]:
                self.book.warnings.append("%s %d:%d empty alignment group %r" % (
                    self.book.code, vr.chapter, vr.verse, g["attrs"].get("x-content")))
                continue
            a = g["attrs"]
            key = (a.get("x-content"), a.get("x-occurrence"))
            row = merged.get(key)
            if row is None:
                row = AlignRow(a.get("x-strong"), a.get("x-lemma"), a.get("x-morph"),
                               a.get("x-content"), _int(a.get("x-occurrence")),
                               _int(a.get("x-occurrences")))
                merged[key] = row
                order.append(row)
                row._idx = set()
            row._idx |= g["idx"]
        for row in order:
            idx = sorted(row._idx)
            del row._idx
            row.word_indexes = idx
            runs = []
            for i in idx:
                if runs and runs[-1][1] + 1 == i:
                    runs[-1][1] = i
                else:
                    runs.append([i, i])
            row.spans = [(a, b) for a, b in runs]
            if row.strongs_raw:
                row.strongs_number, row.strongs_meta = _normalize(row.strongs_raw)
        order.sort(key=lambda r: r.word_indexes[0])  # stable
        vr.rows = order
        self.book.footnotes_dropped += vr.footnotes_dropped
        chap = self.book.chapters.setdefault(vr.chapter, {})
        if vr.verse in chap:
            self.book.warnings.append("duplicate verse %s %d:%d (kept first)" % (
                self.book.code, vr.chapter, vr.verse))
        else:
            chap[vr.verse] = vr

    # ---- heading sink ------------------------------------------------------
    def end_heading(self):
        h = " ".join(self.heading_buf.split())
        self.heading_buf = ""
        if h:
            self.pending_heading.append(h)

    def set_sink(self, sink):
        if self.sink == "heading" and sink != "heading":
            self.end_heading()
        self.sink = sink

    # ---- main loop ---------------------------------------------------------
    def text_chunk(self, chunk):
        if not chunk:
            return
        if self.sink == "verse" and self.v is not None:
            self.add_text(chunk)
        elif self.sink == "heading":
            self.heading_buf += chunk

    def run(self):
        text = self.text.lstrip("\ufeff")
        pos = 0
        n = len(text)
        while pos < n:
            m = _TOK.search(text, pos)
            if not m:
                self.text_chunk(text[pos:])
                break
            self.text_chunk(text[pos:m.start()])
            pos = m.end()
            if m.group("zaln") is not None:
                if self.v is not None and self.sink == "verse":
                    self.v["seq"] = self.v.get("seq", 0) + 1
                    self.v["zstack"].append({"attrs": _attrs(m.group("zaln")), "idx": set(),
                                             "seq": self.v["seq"]})
                continue
            mk = m.group("mk")
            if mk is None:
                if m.group(0).startswith("\\zaln-e") and self.v is not None and self.v["zstack"]:
                    self.v["groups"].append(self.v["zstack"].pop())
                continue
            star = bool(m.group("star"))
            base = mk.lstrip("+")
            if star:
                if base in _WJ and self.v is not None:
                    st = self.v
                    if st["wj"]:
                        start = st["wj"].pop()
                        end = len(st["vr"].words) - (0 if st["buf"] else 1)
                        if end >= start:
                            st["vr"].spans.append({"type": "words_of_christ", "start": start, "end": end})
                continue
            # opening / line markers: swallow a single separator space
            if base == "w":
                pos = self.do_word(text, pos)
                continue
            if base in _NOTES or base in _DROPPED:
                pos = self.do_drop(text, pos, base)
                continue
            if base in _WJ:
                if self.v is not None and self.sink == "verse":
                    st = self.v
                    st["wj"].append(len(st["vr"].words))
                pos = self.skip_sep(text, pos)
                continue
            if base == "id":
                eol = text.find("\n", pos)
                eol = n if eol < 0 else eol
                mm = re.match(r"\s*([A-Za-z0-9]{3})", text[pos:eol])
                if mm and self.book.code is None:
                    self.book.code = mm.group(1).upper()
                pos = eol
                self.set_sink("ignore")
                continue
            if base == "c":
                mm = _CNUM.match(text, pos)
                if mm:
                    self.close_verse()
                    self.chapter = int(mm.group(1))
                    pos = mm.end()
                    self.poetry = 0
                    self.pending_para = False
                    self.set_sink("ignore")
                continue
            if base == "v":
                mm = _VNUM.match(text, pos)
                if mm and self.chapter:
                    pos = mm.end()
                    self.open_verse(int(mm.group(1)), _int(mm.group(2)))
                continue
            self.line_marker(base)
            pos = self.skip_sep(text, pos)
        self.close_verse()
        if self.sink == "heading":
            self.end_heading()
        return self.book

    @staticmethod
    def skip_sep(text, pos):
        if pos < len(text) and text[pos] in " \t":
            return pos + 1
        return pos

    def line_marker(self, base):
        if base in _HEADING:
            self.set_sink("heading")
        elif base in _PARA:
            self.poetry = 0
            self.mark_para()
            self.set_sink("verse")
        elif base in _PLAIN:
            self.poetry = 0
            self.set_sink("verse")
        elif base in _POETRY:
            lvl = _POETRY[base]
            self.mark_line(lvl)
            self.poetry = lvl
            self.set_sink("verse")
        elif base in _IGNORE_LINE:
            self.set_sink("ignore")

    def mark_para(self):
        st = self.v
        if st is not None and not st["vr"].words and not st["buf"] and not st["vr"].paragraph_start:
            st["vr"].paragraph_start = True  # marker before any word of this verse
        else:
            self.pending_para = True

    def mark_line(self, lvl):
        st = self.v
        if st is None:
            return
        n = len(st["vr"].words) + (1 if st["buf"] else 0)
        starts = st["starts"]
        if starts and starts[-1][0] == n:
            starts[-1] = (n, lvl, False)
            return
        if not starts and n > 0:
            starts.append((0, 1, True))  # prose lead-in treated as a first line
        starts.append((n, lvl, False))

    def do_word(self, text, pos):
        pos = self.skip_sep(text, pos)
        mm = re.compile(r"\\\+?w\*").search(text, pos)
        if mm:
            body, end = text[pos:mm.start()], mm.end()
        else:
            body, end = text[pos:pos], pos
        word = body.split("|", 1)[0]
        word = _INNER.sub("", word)
        if self.sink == "heading":
            self.heading_buf += word
        elif self.sink == "verse" and self.v is not None:
            idx = set()
            self.add_text(word, idx)
            for g in self.v["zstack"]:
                g["idx"] |= idx
        return end

    def do_drop(self, text, pos, base):
        mm = re.compile(r"\\\+?%s\*" % re.escape(base)).search(text, pos)
        if mm:
            end = mm.end()
        else:
            nxt = re.compile(r"\\[vc]\s").search(text, pos)
            end = nxt.start() if nxt else len(text)
        if base in _NOTES:
            if self.v is not None:
                self.v["vr"].footnotes_dropped += 1
            else:
                self.book.footnotes_dropped += 1
        return end


def parse_book(text):
    """Parse one aligned USFM book (str) into a BookResult."""
    return _Parser(text).run()


def book_code(text):
    """The upper-case \\id code of a USFM text, or None."""
    m = re.search(r"^\\id[ \t]+([A-Za-z0-9]{3})", text.lstrip("\ufeff"), re.M)
    return m.group(1).upper() if m else None


def parse_book_file(path):
    with open(path, encoding="utf-8-sig") as fh:
        return parse_book(fh.read())


def _canon(text):
    return "\n".join(ln.rstrip() for ln in text.lstrip("\ufeff").replace("\r\n", "\n").strip().split("\n"))


def dedupe_books(files):
    """Drop identical duplicate books; raise DuplicateBookError on differing ones.

    ``files`` is a list of ``(name, text)``.  Books are identified by their
    \\id code, never their file name.  Returns the kept ``(name, text)`` pairs
    in first-seen order.  A file with no \\id raises ValueError.
    """
    kept = {}
    out = []
    for name, text in files:
        code = book_code(text)
        if code is None:
            raise ValueError("no \\id line in %s" % name)
        if code in kept:
            first_name, first_text = kept[code]
            if _canon(first_text) != _canon(text):
                raise DuplicateBookError("%s and %s both carry \\id %s but differ" % (first_name, name, code))
            continue
        kept[code] = (name, text)
        out.append((name, text))
    return out
