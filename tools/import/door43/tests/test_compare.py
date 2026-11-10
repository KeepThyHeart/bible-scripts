import json
import os
import sqlite3
import tempfile
import unittest

from door43lib import compare


def make_db(path, verses, rows=(), abbr="X"):
    """verses: {verse_id: (text, formatting_or_None)}; rows: (verse_id, start, end, extra, strongs)."""
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE module_info (info_id INTEGER PRIMARY KEY, module_uuid TEXT, abbreviation TEXT, language_code TEXT);
        CREATE TABLE bible_verse (verse_id INTEGER PRIMARY KEY, text TEXT, formatting TEXT, word_count INTEGER);
        CREATE TABLE interlinear_word (interlinear_id INTEGER PRIMARY KEY, verse_id INTEGER,
            word_position_start INTEGER, word_position_end INTEGER, extra_word_positions TEXT, strongs_number TEXT);
    """)
    conn.execute("INSERT INTO module_info VALUES (1, 'u', ?, 'xx')", (abbr,))
    for vid, (text, fmt) in verses.items():
        conn.execute("INSERT INTO bible_verse VALUES (?,?,?,?)", (vid, text, fmt, len(text.split())))
    for r in rows:
        conn.execute("INSERT INTO interlinear_word (verse_id, word_position_start, word_position_end, "
                     "extra_word_positions, strongs_number) VALUES (?,?,?,?,?)", r)
    conn.commit()
    conn.close()


class CompareTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.new = os.path.join(self.tmp.name, "new.db")
        self.ref = os.path.join(self.tmp.name, "ref.db")

    def test_equal_modules_pass_and_coverage(self):
        v = {64001001: ("one two three four", None), 64001002: ("five six", None)}
        make_db(self.new, v, rows=[(64001001, 0, 1, None, "G0001"), (64001001, 3, 3, None, "G0002"),
                                   (64001001, 2, 2, None, None)], abbr="NEW")
        make_db(self.ref, v, abbr="REF")
        r = compare.compare(self.new, self.ref)
        self.assertTrue(r["pass"])
        self.assertTrue(r["verse_sets_identical"])
        self.assertEqual(r["text_equal_pct"], 100.0)
        b = r["books"]["3JN"]
        self.assertEqual((b["verses_new"], b["verses_ref"], b["verses_both"]), (2, 2, 2))
        # words 0,1,3 covered (the row without a Strong's number does not count) of 6 words
        self.assertEqual((b["words"], b["words_covered"]), (6, 3))
        self.assertEqual(b["coverage_pct"], 50.0)
        self.assertEqual(b["zero_alignment_verses"], 1)
        self.assertEqual((b["interlinear_new"], b["interlinear_ref"]), (3, 0))
        self.assertEqual(r["new"]["abbreviation"], "NEW")

    def test_extra_positions_count_as_coverage(self):
        v = {64001001: ("a b c d e f g", None)}
        make_db(self.new, v, rows=[(64001001, 0, 0, "2-3,5", "G0001")])
        make_db(self.ref, v)
        b = compare.compare(self.new, self.ref)["books"]["3JN"]
        self.assertEqual(b["words_covered"], 4)

    def test_text_diff_after_nfc_and_whitespace(self):
        make_db(self.new, {64001001: ("e\u0301  tr\u00e9s  bon", None), 64001002: ("same", None)})
        make_db(self.ref, {64001001: ("\u00e9 tr\u0065\u0301s bon", None), 64001002: ("same", None)})
        r = compare.compare(self.new, self.ref)
        self.assertEqual(r["books"]["3JN"]["text_equal"], 2)
        make_db(self.ref, {64001001: ("different", None), 64001002: ("same", None)})
        r = compare.compare(self.new, self.ref)
        b = r["books"]["3JN"]
        self.assertEqual((b["text_equal"], b["text_diff"]), (1, 1))
        self.assertEqual(b["diffs"][0]["verse_id"], 64001001)
        self.assertEqual(r["text_equal_pct"], 50.0)
        self.assertFalse(r["pass"])

    def test_pass_threshold(self):
        n = 200
        new = {64001000 + i: ("w%d" % i, None) for i in range(1, n + 1)}
        ref = dict(new)
        ref[64001001] = ("changed", None)          # 199/200 = 99.5 %
        make_db(self.new, new)
        make_db(self.ref, ref)
        self.assertTrue(compare.compare(self.new, self.ref)["pass"])
        ref[64001002] = ("changed", None)          # 99.0 %
        make_db(self.ref, ref)
        self.assertFalse(compare.compare(self.new, self.ref)["pass"])

    def test_nospace_measure_drives_pass(self):
        make_db(self.new, {64001001: ("ab cd", None), 64001002: ("x y", None)})
        make_db(self.ref, {64001001: ("abcd", None), 64001002: ("x  y", None)})
        r = compare.compare(self.new, self.ref)
        self.assertEqual(r["text_equal_pct"], 50.0)
        self.assertEqual(r["text_equal_nospace_pct"], 100.0)
        self.assertTrue(r["pass"])

    def test_diffs_limited_to_three(self):
        new = {64001000 + i: ("a%d" % i, None) for i in range(1, 8)}
        make_db(self.new, new)
        make_db(self.ref, {k: ("b", None) for k in new})
        self.assertEqual(len(compare.compare(self.new, self.ref)["books"]["3JN"]["diffs"]), 3)

    def test_missing_verse_fails(self):
        make_db(self.new, {64001001: ("a", None), 64001002: ("b", None)})
        make_db(self.ref, {64001001: ("a", None), 64001003: ("c", None)})
        r = compare.compare(self.new, self.ref)
        b = r["books"]["3JN"]
        self.assertEqual((b["only_new"], b["only_ref"], b["verses_both"]), (1, 1, 1))
        self.assertEqual(b["only_new_ids"], [64001002])
        self.assertEqual(b["only_ref_ids"], [64001003])
        self.assertFalse(r["verse_sets_identical"])
        self.assertFalse(r["pass"])

    def test_source_verses_compared(self):
        sv = json.dumps({"v": 1, "source_verses": [{"chapter": 1, "verse": 15, "start": 9, "end": 17}]})
        sv2 = json.dumps({"v": 1, "source_verses": [{"chapter": 1, "verse": 15, "start": 8, "end": 17}]})
        make_db(self.new, {64001014: ("a", sv), 64001001: ("b", None)})
        make_db(self.ref, {64001014: ("a", sv2), 64001001: ("b", '{"v":1}')})
        b = compare.compare(self.new, self.ref)["books"]["3JN"]
        self.assertEqual((b["source_verses_equal"], b["source_verses_diff"]), (1, 1))
        self.assertEqual(b["source_verse_diffs"][0]["verse_id"], 64001014)

    def test_books_only_in_ref_not_compared_unless_strict(self):
        make_db(self.new, {64001001: ("a", None)})
        make_db(self.ref, {64001001: ("a", None), 1001001: ("g", None)})
        r = compare.compare(self.new, self.ref)
        self.assertTrue(r["pass"])
        self.assertEqual(r["books_only_ref"], ["GEN"])
        self.assertFalse(compare.compare(self.new, self.ref, strict_books=True)["pass"])

    def test_write_report(self):
        make_db(self.new, {64001001: ("a", None)}, abbr="AB-C")
        make_db(self.ref, {64001001: ("a", None)})
        r = compare.compare(self.new, self.ref)
        j, t = compare.write_report(r, os.path.join(self.tmp.name, "reports"))
        self.assertTrue(j.endswith("AB-C-compare.json"))
        with open(j, encoding="utf-8") as fh:
            self.assertTrue(json.load(fh)["pass"])
        with open(t, encoding="utf-8") as fh:
            self.assertIn("PASS", fh.read())


if __name__ == "__main__":
    unittest.main()
