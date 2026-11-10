"""Compare a freshly built Bible module with a reference module (design section 7).

``compare(new_db, ref_db)`` takes two database paths and returns a plain dict;
``write_report(result, directory)`` writes ``<abbr>-compare.json`` plus a text summary.

Per book: verses new / ref / both / only-new / only-ref, text equality after NFC and
whitespace collapse (first 3 differences), ``formatting.source_verses`` equality,
``interlinear_word`` row counts, alignment coverage (target words covered by an
interlinear row with a Strong's number / all words) and the number of verses with no
alignment at all.

Pass rule: identical verse sets and at least 99.5 % of the common verses equal in text
ignoring ALL whitespace (``text_equal_nospace_pct``); the strict ``text_equal_pct`` is reported too.
By default only the books present in the new module are compared (a partial NT is not
failed for lacking the OT); the books only the reference has are listed under
``books_only_ref``.  Pass ``strict_books=True`` to compare every book.
"""
import json
import os
import re
import sqlite3
import unicodedata

from . import books

__all__ = ["compare", "write_report", "normalize_text", "format_summary", "PASS_TEXT_PCT"]

PASS_TEXT_PCT = 99.5
_MAX_LIST = 10


def normalize_text(s):
    return " ".join(unicodedata.normalize("NFC", s or "").split())


def normalize_nospace(s):
    """NFC, then drop ALL whitespace (including between words)."""
    return "".join(unicodedata.normalize("NFC", s or "").split())


def _module_info(conn):
    try:
        row = conn.execute("SELECT abbreviation, module_uuid, language_code FROM module_info "
                           "WHERE info_id = 1").fetchone()
    except sqlite3.Error:
        row = None
    return {"abbreviation": row[0], "module_uuid": row[1], "language_code": row[2]} if row else {}


def _source_verses(formatting):
    if not formatting:
        return []
    try:
        data = json.loads(formatting)
    except ValueError:
        return []
    return data.get("source_verses") or [] if isinstance(data, dict) else []


def _extra_positions(extra):
    out = []
    for part in (extra or "").split(","):
        part = part.strip()
        if not part:
            continue
        a, _, b = part.partition("-")
        try:
            out.append((int(a), int(b or a)))
        except ValueError:
            continue
    return out


def _load(conn):
    """{verse_id: (text, formatting, word_count)} and {verse_id: covered word count}, row counts."""
    verses = {}
    for vid, text, fmt, wc in conn.execute("SELECT verse_id, text, formatting, word_count FROM bible_verse"):
        verses[vid] = (text or "", fmt, wc if wc is not None else len((text or "").split()))
    covered = {}
    rows = {}
    try:
        cur = conn.execute("SELECT verse_id, word_position_start, word_position_end, extra_word_positions, "
                           "strongs_number FROM interlinear_word")
    except sqlite3.Error:
        cur = []
    for vid, a, b, extra, strongs in cur:
        rows[vid] = rows.get(vid, 0) + 1
        if not strongs:
            continue
        s = covered.setdefault(vid, set())
        s.update(range(a, b + 1))
        for x, y in _extra_positions(extra):
            s.update(range(x, y + 1))
    return verses, covered, rows


def _empty_book():
    return {"verses_new": 0, "verses_ref": 0, "verses_both": 0, "only_new": 0, "only_ref": 0,
            "only_new_ids": [], "only_ref_ids": [], "text_equal": 0, "text_equal_nospace": 0, "text_diff": 0, "diffs": [],
            "source_verses_equal": 0, "source_verses_diff": 0, "source_verse_diffs": [],
            "interlinear_new": 0, "interlinear_ref": 0, "words": 0, "words_covered": 0,
            "zero_alignment_verses": 0}


def compare(new_db, ref_db, strict_books=False):
    new_conn = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(new_db), uri=True)
    ref_conn = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(ref_db), uri=True)
    try:
        info_new, info_ref = _module_info(new_conn), _module_info(ref_conn)
        nv, ncov, nrows = _load(new_conn)
        rv, _rcov, rrows = _load(ref_conn)
    finally:
        new_conn.close()
        ref_conn.close()

    new_books = {vid // 1000000 for vid in nv}
    ref_books = {vid // 1000000 for vid in rv}
    only_ref_books = sorted(ref_books - new_books)
    scope = (new_books | ref_books) if strict_books else new_books

    per = {}
    for vid in sorted(set(nv) | set(rv)):
        b = vid // 1000000
        if b not in scope:
            continue
        bk = per.setdefault(b, _empty_book())
        in_new, in_ref = vid in nv, vid in rv
        if in_new:
            bk["verses_new"] += 1
            bk["interlinear_new"] += nrows.get(vid, 0)
            wc = nv[vid][2]
            cov = len([i for i in ncov.get(vid, ()) if i < wc])
            bk["words"] += wc
            bk["words_covered"] += cov
            if cov == 0:
                bk["zero_alignment_verses"] += 1
        if in_ref:
            bk["verses_ref"] += 1
            bk["interlinear_ref"] += rrows.get(vid, 0)
        if in_new and in_ref:
            bk["verses_both"] += 1
            if normalize_text(nv[vid][0]) == normalize_text(rv[vid][0]):
                bk["text_equal"] += 1
            else:
                bk["text_diff"] += 1
                if len(bk["diffs"]) < 3:
                    bk["diffs"].append({"verse_id": vid, "new": nv[vid][0], "ref": rv[vid][0]})
            if normalize_nospace(nv[vid][0]) == normalize_nospace(rv[vid][0]):
                bk["text_equal_nospace"] += 1
            sn, sr = _source_verses(nv[vid][1]), _source_verses(rv[vid][1])
            if sn == sr:
                bk["source_verses_equal"] += 1
            else:
                bk["source_verses_diff"] += 1
                if len(bk["source_verse_diffs"]) < 3:
                    bk["source_verse_diffs"].append({"verse_id": vid, "new": sn, "ref": sr})
        elif in_new:
            bk["only_new"] += 1
            if len(bk["only_new_ids"]) < _MAX_LIST:
                bk["only_new_ids"].append(vid)
        else:
            bk["only_ref"] += 1
            if len(bk["only_ref_ids"]) < _MAX_LIST:
                bk["only_ref_ids"].append(vid)

    totals = _empty_book()
    for bk in per.values():
        for k, v in bk.items():
            if isinstance(v, int):
                totals[k] += v
    for k in ("only_new_ids", "only_ref_ids", "diffs", "source_verse_diffs"):
        totals.pop(k)
    both = totals["verses_both"]
    text_pct = 100.0 * totals["text_equal"] / both if both else 0.0
    nospace_pct = 100.0 * totals["text_equal_nospace"] / both if both else 0.0
    coverage = 100.0 * totals["words_covered"] / totals["words"] if totals["words"] else 0.0
    sets_identical = totals["only_new"] == 0 and totals["only_ref"] == 0
    passed = bool(both) and sets_identical and nospace_pct >= PASS_TEXT_PCT
    names = {}
    for b in per:
        try:
            names[b] = books.usfm_code(b)
        except ValueError:
            names[b] = str(b)
    return {
        "new": dict(info_new, path=os.path.basename(new_db)),
        "ref": dict(info_ref, path=os.path.basename(ref_db)),
        "strict_books": bool(strict_books),
        "books": {names[b]: dict(per[b], book_number=b, coverage_pct=_pct(per[b]["words_covered"], per[b]["words"]))
                  for b in sorted(per)},
        "books_only_ref": [books.usfm_code(b) for b in only_ref_books],
        "totals": totals,
        "verse_sets_identical": sets_identical,
        "text_equal_pct": round(text_pct, 3),
        "text_equal_nospace_pct": round(nospace_pct, 3),
        "alignment_coverage_pct": round(coverage, 3),
        "pass_threshold_pct": PASS_TEXT_PCT,
        "pass": passed,
    }


def _pct(a, b):
    return round(100.0 * a / b, 3) if b else 0.0


def format_summary(result):
    n, r, t = result["new"], result["ref"], result["totals"]
    lines = ["Compare %s (new) with %s (ref): %s" % (n.get("abbreviation") or n.get("path"),
                                                    r.get("abbreviation") or r.get("path"),
                                                    "PASS" if result["pass"] else "FAIL"),
             "verse sets identical: %s; text equal %.3f%% strict, %.3f%% ignoring whitespace (need %.1f%%); "
             "alignment coverage %.3f%%" % (
                 result["verse_sets_identical"], result["text_equal_pct"], result["text_equal_nospace_pct"],
                 result["pass_threshold_pct"],
                 result["alignment_coverage_pct"]),
             "totals: new %d, ref %d, both %d, only-new %d, only-ref %d; interlinear rows new %d / ref %d; "
             "verses with zero alignment %d" % (t["verses_new"], t["verses_ref"], t["verses_both"], t["only_new"],
                                                t["only_ref"], t["interlinear_new"], t["interlinear_ref"],
                                                t["zero_alignment_verses"])]
    if result["books_only_ref"]:
        lines.append("books only in ref (not compared): " + " ".join(result["books_only_ref"]))
    lines.append("")
    lines.append("book  new  ref both onlyN onlyR textEq textDiff srcVerseDiff  rowsN rowsR  cover%  zeroAln")
    for code, b in result["books"].items():
        lines.append("%-4s %4d %4d %4d %5d %5d %6d %8d %12d %6d %5d %7.2f %8d" % (
            code, b["verses_new"], b["verses_ref"], b["verses_both"], b["only_new"], b["only_ref"],
            b["text_equal"], b["text_diff"], b["source_verses_diff"], b["interlinear_new"], b["interlinear_ref"],
            b["coverage_pct"], b["zero_alignment_verses"]))
    for code, b in result["books"].items():
        for d in b["diffs"]:
            lines.append("")
            lines.append("%s text diff %d:" % (code, d["verse_id"]))
            lines.append("  new: " + d["new"])
            lines.append("  ref: " + d["ref"])
        for d in b["source_verse_diffs"]:
            lines.append("%s source_verses diff %d: new=%s ref=%s" % (code, d["verse_id"], d["new"], d["ref"]))
        if b["only_new_ids"]:
            lines.append("%s only in new: %s" % (code, b["only_new_ids"]))
        if b["only_ref_ids"]:
            lines.append("%s only in ref: %s" % (code, b["only_ref_ids"]))
    return "\n".join(lines) + "\n"


def write_report(result, directory):
    """Write ``<abbr>-compare.json`` and ``<abbr>-compare.txt``; return the two paths."""
    os.makedirs(directory, exist_ok=True)
    abbr = result["new"].get("abbreviation") or os.path.splitext(result["new"].get("path") or "module")[0]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", abbr)
    jpath = os.path.join(directory, stem + "-compare.json")
    tpath = os.path.join(directory, stem + "-compare.txt")
    with open(jpath, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    with open(tpath, "w", encoding="utf-8") as fh:
        fh.write(format_summary(result))
    return jpath, tpath
