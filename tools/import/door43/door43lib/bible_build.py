"""Word-aligned USFM (Door43) -> Bible module ``bible_<ABBR>.db`` (design section 7).

``build(archive, source_row, out_dir, ref_db=None, versification=None)`` parses every
book of the archive (``usfm_aligned``), maps ufw verses to KJV (``versification``),
folds variant verse divisions into their host verse exactly as sword2bible's
``mergeVerseResults`` does, writes ``bible_verse`` and ``interlinear_word`` rows in
the form sword2bible writes (0-based inclusive word offsets), and finishes with the
module identity.  The result is a report dict.
"""
import json
import os
import sqlite3

from . import books, compare as compare_mod, identity, schema, usfm_aligned, versification as vers_mod
from .fetch import release_url
from .versification import UnmappedVerse

__all__ = ["build", "Frag", "merge_frags", "convert_morphology", "BuildError"]

LICENSE_SPDX = "CC-BY-SA-4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
ALIGN_HOLDERS = {"BSOJ": "BSOJ / BCS", "Door43-Catalog": "BCS"}
NEW_MODULE_ABBR = {"fr_lsg": "LSG1910-NT"}   # no 0220 counterpart: a new module
ALN_SUFFIX = "-ALN"
SCHEMES = {"schemes": {"hbo": "oshm", "grc": "uw-greek"}}


class BuildError(RuntimeError):
    pass


def convert_morphology(raw):
    """Hebrew ``He,Ncmsc`` -> ``HNcmsc`` (OSHM style), Aramaic ``Ar,`` -> ``A``, ``:`` -> ``/``.
    Greek is stored verbatim. Empty -> None."""
    if not raw:
        return None
    if raw.startswith("He,"):
        return "H" + raw[3:].replace(":", "/")
    if raw.startswith("Ar,"):
        return "A" + raw[3:].replace(":", "/")
    return raw


class Frag:
    """A verse (or part of one) ready to store: words, formatting pieces, alignment rows."""

    def __init__(self):
        self.words = []
        self.paragraph_start = False
        self.lines = []          # [{"start","end","level"}]
        self.heading = ""
        self.spans = []          # [{"type","start","end"}]
        self.rows = []           # dicts: strongs, meta, lemma_x, morph, content, spans, gloss
        self.source_verses = []  # [{"chapter","verse","start","end"}]

    @classmethod
    def from_verse(cls, vr):
        f = cls()
        f.words = list(vr.words)
        f.paragraph_start = vr.paragraph_start
        f.lines = [dict(x) for x in vr.lines]
        f.heading = vr.heading
        f.spans = [dict(x) for x in vr.spans]
        for r in vr.rows:
            f.rows.append({"strongs": r.strongs_number, "meta": dict(r.strongs_meta), "lemma_x": r.lemma,
                           "morph": r.morph, "content": r.x_content, "spans": list(r.spans),
                           "gloss": r.gloss(vr.words)})
        return f

    @property
    def word_count(self):
        return len(self.words)

    @property
    def text(self):
        return " ".join(self.words)

    def formatting(self):
        """The ``formatting`` JSON as sword_common's buildFormattingJson writes it, or None."""
        f = {"v": 1}
        block = {}
        if self.paragraph_start:
            block["paragraph_start"] = True
        if self.lines:
            block["lines"] = self.lines
        if self.heading:
            block["heading"] = self.heading
        if block:
            f["block"] = block
        if self.spans:
            f["spans"] = self.spans
        if self.source_verses:
            f["source_verses"] = self.source_verses
        return json.dumps(f, ensure_ascii=False, separators=(",", ":")) if len(f) > 1 else None


def _shift_spans(spans, n):
    return [(a + n, b + n) for a, b in spans]


def merge_frags(first, second):
    """Mirror of sword2bible's mergeVerseResults: ``second`` is appended to ``first``."""
    m = Frag()
    m.words = first.words + second.words
    shift = first.word_count
    m.paragraph_start = first.paragraph_start
    m.heading = first.heading or second.heading
    m.lines = [dict(x) for x in first.lines] + [dict(x, start=x["start"] + shift, end=x["end"] + shift)
                                                 for x in second.lines]
    if m.lines:
        m.lines[0]["start"] = 0
        m.lines[-1]["end"] = m.word_count - 1
        for k in range(len(m.lines) - 1):
            m.lines[k]["end"] = m.lines[k + 1]["start"] - 1
    m.spans = [dict(x) for x in first.spans] + [dict(x, start=x["start"] + shift, end=x["end"] + shift)
                                                 for x in second.spans]
    m.rows = [dict(r) for r in first.rows] + [dict(r, spans=_shift_spans(r["spans"], shift)) for r in second.rows]
    m.source_verses = [dict(x) for x in first.source_verses] + [
        dict(x, start=x["start"] + shift, end=x["end"] + shift) for x in second.source_verses]
    return m


def _extra(spans):
    extra = spans[1:]
    if not extra:
        return None
    return ",".join(str(a) if a == b else "%d-%d" % (a, b) for a, b in extra)


def _row_values(verse_id, r):
    meta = {"lemma": r["lemma_x"]}
    meta.update({k: v for k, v in r["meta"].items() if k in ("x_strong", "prefix")})
    meta = {k: v for k, v in meta.items() if v is not None}
    return (verse_id, r["spans"][0][0], r["spans"][0][1], r["content"], r["strongs"],
            convert_morphology(r["morph"]), "strong:" + r["strongs"], r["gloss"] or None,
            _extra(r["spans"]), json.dumps(meta, ensure_ascii=False, separators=(",", ":")))


def _book_texts(archive):
    files = []
    for name in sorted(archive.names()):
        low = name.lower()
        if low.endswith((".usfm", ".sfm")) and "/" not in name.strip("/"):
            files.append((name, archive.read_text(name)))
    if not files:
        for name in sorted(archive.names()):
            if name.lower().endswith((".usfm", ".sfm")):
                files.append((name, archive.read_text(name)))
    return usfm_aligned.dedupe_books(files)


def _assemble(archive, vers, report):
    """Parse all books; return {verse_id: Frag}."""
    out = {}
    for name, text in _book_texts(archive):
        code = usfm_aligned.book_code(text)
        bnum = books.try_book_number(code)
        if bnum is None:
            report["skipped_books"].append(code)
            continue
        book = usfm_aligned.parse_book(text)
        report["books"].append(code)
        report["footnotes_dropped"] += book.footnotes_dropped
        report["warnings"].extend(book.warnings)
        pending = {}
        for ch in sorted(book.chapters):
            for v in sorted(book.chapters[ch]):
                vr = book.chapters[ch][v]
                if not vr.words:
                    report["empty_verses"].append("%s %d:%d" % (code, ch, v))
                    continue
                if vr.verse_end != vr.verse:
                    report["bridged_verses"].append("%s %d:%d-%d" % (code, ch, v, vr.verse_end))
                frag = Frag.from_verse(vr)
                own = {"chapter": ch, "verse": v, "start": 0, "end": frag.word_count - 1}
                try:
                    c2, v2 = vers.map_verse(bnum, ch, v)
                except UnmappedVerse:
                    if not report.get("allow_unmapped"):
                        raise
                    report.setdefault("dropped_unmapped", []).append("%s %d:%d" % (code, ch, v))
                    continue
                hid = books.verse_id(bnum, c2, v2)
                m = vers.merge_info(bnum, ch, v)
                if m is not None:
                    frag.source_verses = [own]
                    host = out.get(hid)
                    if host is not None:
                        out[hid] = merge_frags(host, frag)
                        report["merged_verses"].append("%s %d:%d -> %d:%d" % (code, ch, v, c2, v2))
                    else:
                        pending[hid] = (frag, m.prefix)
                    continue
                if hid in pending:
                    pfrag, prefix = pending.pop(hid)
                    frag.source_verses = [own]
                    frag = merge_frags(pfrag, frag) if prefix else merge_frags(frag, pfrag)
                    report["merged_verses"].append("%s %d:%d <- held fragment" % (code, c2, v2))
                if hid in out:
                    report["warnings"].append("%s %d:%d maps onto an existing verse %d; concatenated" % (
                        code, ch, v, hid))
                    out[hid] = merge_frags(out[hid], frag)
                else:
                    out[hid] = frag
        for hid in pending:
            report["warnings"].append("variant fragment for verse_id %d had no host verse; text NOT imported" % hid)
    return out


def _kjv_shape(verses, vers, report):
    counts = vers.kjv_counts
    if not counts:
        return
    present = {}
    for vid in verses:
        b, c, v = books.split_verse_id(vid)
        present.setdefault(b, set()).add((c, v))
    holes = []
    for b, have in sorted(present.items()):
        for c, n in enumerate(counts.get(b, []), 1):
            for v in range(1, n + 1):
                if (c, v) not in have:
                    holes.append("%s %d:%d" % (books.usfm_code(b), c, v))
    report["kjv_holes"] = {"count": len(holes), "sample": holes[:20]}


def _write_content(path, verses, report):
    conn = schema.create_db(path, "bible", overwrite=True)
    nrows = skipped = morph = 0
    with conn:
        for vid in sorted(verses):
            f = verses[vid]
            conn.execute("INSERT INTO bible_verse (verse_id, text, formatting, word_count) VALUES (?,?,?,?)",
                         (vid, f.text, f.formatting(), f.word_count))
            for r in sorted(f.rows, key=lambda r: r["spans"][0][0]):
                if not r["strongs"]:
                    skipped += 1
                    continue
                vals = _row_values(vid, r)
                conn.execute("INSERT INTO interlinear_word (verse_id, word_position_start, word_position_end, "
                             "original_word, strongs_number, morphology, lemma, gloss, extra_word_positions, "
                             "metadata) VALUES (?,?,?,?,?,?,?,?,?,?)", vals)
                nrows += 1
                morph += 1 if vals[5] else 0
    report["interlinear_rows"] = nrows
    report["rows_skipped_no_strongs"] = skipped
    report["morphology_rows"] = morph
    return conn


def _overrides_forbid(ref_meta):
    """forbid_text of the matching module-overrides.json entry (trademark guard), via the sword tool."""
    mod = identity._overrides_module()
    path = mod.DEFAULT_OVERRIDES
    name = (ref_meta or {}).get("sword_module")
    if not name or not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        entry = json.load(fh).get("modules", {}).get(name) or {}
    return entry.get("forbid_text", [])


def _ref_info(ref_db):
    conn = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(ref_db), uri=True)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM module_info WHERE info_id = 1").fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _module_fields(archive, source, ref, reuse, abbr, lang):
    manifest = {}
    if archive.exists("manifest.yaml"):
        manifest = archive.manifest() or {}
    dc = manifest.get("dublin_core", {}) or {}
    owner, repo, tag = source["owner"], source["repo"], source["tag"]
    holder = ALIGN_HOLDERS.get(owner, owner)
    align = "Word alignment and Strong's: © %s, CC BY-SA 4.0" % holder
    align_note = ("Word alignment and Strong's numbers from Door43 %s/%s %s (%s), CC BY-SA 4.0." %
                  (owner, repo, tag, holder))
    meta = {"derivative": True, "door43_language": source.get("door43_lang") or lang,
            "door43": {"owner": owner, "repo": repo, "tag": tag, "manifest_version": dc.get("version"),
                       "sources": dc.get("source", [])}}
    if ref is not None:
        rmeta = json.loads(ref.get("metadata") or "{}")
        text_lic = ref.get("copyright") or ref.get("license_spdx") or ""
        attribution = " ".join(x for x in (rmeta.get("attribution"), align_note) if x)
        notices = rmeta.get("notices") or []
        if isinstance(notices, str):
            notices = [notices]
        notices = list(notices) + ["The word alignment and Strong's data are licensed CC BY-SA 4.0; the combined "
                                   "file is distributed under CC BY-SA 4.0."]
        meta.update({k: rmeta[k] for k in ("trademark_removed", "original_source") if k in rmeta})
        meta["attribution"] = attribution
        meta["notices"] = notices
        meta["component_licenses"] = {"text": ref.get("license_spdx"), "alignment": LICENSE_SPDX}
        meta["modifications"] = " ".join(x for x in (
            rmeta.get("modifications"),
            "Word alignment and Strong's numbers added from the Door43 aligned USFM %s %s." % (repo, tag)) if x)
        cols = {k: ref.get(k) for k in ("full_name", "author", "publisher", "year_published", "description")}
        cols["right_to_left"] = ref.get("right_to_left") or 0
    else:
        text_lic = dc.get("rights") or LICENSE_SPDX
        lic_text = archive.read_text("LICENSE.md") if archive.exists("LICENSE.md") else ""
        cr = identity.copyright_line(lic_text)
        meta["attribution"] = " ".join(x for x in (cr, align_note) if x)
        meta["notices"] = ["The licensor does not endorse this module or its use."]
        meta["component_licenses"] = {"text": dc.get("rights"), "alignment": LICENSE_SPDX}
        meta["modifications"] = ("Converted from Door43 %s %s aligned USFM to the Bible app module format; verse "
                                 "numbering mapped to KJV." % (repo, tag))
        cols = {"full_name": dc.get("title") or abbr, "author": dc.get("creator"), "publisher": dc.get("publisher"),
                "year_published": identity.year_from_issued(dc.get("issued")), "description": dc.get("description"),
                "right_to_left": 1 if (dc.get("language", {}) or {}).get("direction") == "rtl" else 0}
        if cr:
            text_lic = cr
    cols.update({
        "abbreviation": abbr, "language_code": lang, "content_version": "door43-" + tag,
        "copyright": "Text: %s. %s" % (text_lic.rstrip("."), align),
        "license_spdx": LICENSE_SPDX, "license_url": LICENSE_URL,
        "source_url": release_url(source), "versification": "kjv-english", "metadata": meta})
    return cols


def build(archive, source_row, out_dir, ref_db=None, versification=None):
    """Build ``out_dir/bible_<ABBR>.db`` from an aligned-USFM archive; return the report dict.

    ``ref_db`` is the path of the matching task-0220 module.  Its identity (uuid,
    abbreviation, ...) is reused only when ``compare.compare`` passes; otherwise the
    abbreviation gets the suffix ``-ALN`` and the report says why.
    """
    vers = versification or vers_mod.Versification.load()
    os.makedirs(out_dir, exist_ok=True)
    report = {"source": source_row.get("id") or source_row.get("repo"), "books": [], "skipped_books": [],
              "footnotes_dropped": 0, "warnings": [], "empty_verses": [], "bridged_verses": [],
              "merged_verses": []}
    report["allow_unmapped"] = bool(source_row.get("allow_unmapped"))
    verses = _assemble(archive, vers, report)
    if not verses:
        raise BuildError("no verses parsed from %s" % report["source"])
    report["verses"] = len(verses)
    _kjv_shape(verses, vers, report)

    tmp = os.path.join(out_dir, ".building-%d.db" % os.getpid())
    conn = _write_content(tmp, verses, report)
    lang = source_row["lang"]
    try:
        abbr0220 = source_row.get("abbr_0220")
        ref = _ref_info(ref_db) if (ref_db and abbr0220) else None
        reuse = False
        if abbr0220 and not ref_db:
            report["identity_reason"] = "no reference module given; using the -ALN suffix"
        elif ref_db and abbr0220:
            cmp = compare_mod.compare(tmp, ref_db)
            report["compare"] = {k: cmp[k] for k in ("pass", "verse_sets_identical", "text_equal_pct", "text_equal_nospace_pct",
                                                      "alignment_coverage_pct", "totals", "books_only_ref")}
            reuse = bool(cmp["pass"]) and ref is not None
            report["identity_reason"] = ("comparison with %s passed" % ref["abbreviation"] if reuse else
                                         "comparison with the 0220 module failed (verse sets identical: %s, text "
                                         "equal %.3f%%); using the -ALN suffix" % (cmp["verse_sets_identical"],
                                                                                    cmp["text_equal_pct"]))
        else:
            report["identity_reason"] = "new module (no 0220 counterpart)"
        if reuse:
            abbr = ref["abbreviation"]
            uuid = ref["module_uuid"]
            lang = ref.get("language_code") or lang
        elif abbr0220:
            abbr = abbr0220 + ALN_SUFFIX
            uuid = identity.module_uuid("bible", abbr, lang)
        else:
            abbr = NEW_MODULE_ABBR.get(source_row.get("repo")) or source_row["repo"].split("_", 1)[-1].upper()
            uuid = identity.module_uuid("bible", abbr, lang)
        cols = _module_fields(archive, source_row, ref if reuse else None, reuse, abbr, lang)
        cols["module_uuid"] = uuid
        with conn:
            schema.insert_module_info(conn, "bible", **cols)
            if report["interlinear_rows"]:
                schema.insert_feature(conn, "strongs_numbers")
                schema.insert_feature(conn, "interlinear")
            if report["morphology_rows"]:
                schema.insert_feature(conn, "morphology", SCHEMES)
        conn.execute("PRAGMA journal_mode = DELETE")
    finally:
        conn.close()

    words = sum(f.word_count for f in verses.values())
    covered = 0
    for f in verses.values():
        cov = set()
        for r in f.rows:
            if r["strongs"]:
                for a, b in r["spans"]:
                    cov.update(range(a, b + 1))
        covered += len(cov)
    report["alignment_coverage_pct"] = round(100.0 * covered / words, 3) if words else 0.0
    report.update({"abbreviation": abbr, "module_uuid": uuid, "identity_reused": reuse,
                   "license_spdx": LICENSE_SPDX})

    final = os.path.join(out_dir, "bible_%s.db" % abbr)
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(final + suffix):
            os.remove(final + suffix)
        if os.path.exists(tmp + suffix) and suffix:
            os.remove(tmp + suffix)
    os.replace(tmp, final)
    report["file"] = final

    forbid = _overrides_forbid(json.loads(ref.get("metadata") or "{}")) if ref else []
    if forbid:
        problems = identity._overrides_module().apply_overrides(final, {"forbid_text": forbid})
        report["forbid_text_problems"] = problems
    return report
