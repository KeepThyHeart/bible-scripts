import json
import os
import shutil
import sqlite3
import tempfile
import unittest
import zipfile

from door43lib import bible_build, fetch, schema, usfm_aligned


def zverse(n, words, strong="G0001", extra=""):
    """A verse whose every word is its own aligned group."""
    parts = []
    for i, w in enumerate(words):
        parts.append('\\zaln-s |x-strong="%s" x-lemma="lem%d" x-morph="Gr,N" x-occurrence="1" x-occurrences="1" '
                     'x-content="c%d_%d"\\*\\w %s|x-occurrence="1" x-occurrences="1"\\w*\\zaln-e\\*' % (
                         strong, i, n, i, w))
    return "\\v %d %s%s\n" % (n, " ".join(parts), extra)


def book_3jn():
    s = "\\id 3JN test\n\\c 1\n\\p\n"
    for n in range(1, 14):
        s += zverse(n, ["w%d" % n, "x%d" % n])
    s += zverse(14, ["a%d" % i for i in range(9)])        # 9 words
    s += "\\q1\n" + zverse(15, ["b%d" % i for i in range(3)], strong="b:H3117")
    return s


def book_rev():
    s = "\\id REV test\n\\c 12\n\\p\n" + zverse(17, ["x", "y"]) + zverse(18, ["f1", "f2", "f3"])
    s += "\\c 13\n" + zverse(1, ["h1", "h2"])
    return s


MANIFEST = """dublin_core:
  title: 'Test Bible'
  creator: 'tester'
  publisher: 'pub'
  issued: '2022-10-21'
  rights: 'CC BY-SA 4.0'
  version: '1'
  language:
    direction: 'rtl'
    identifier: 'xx'
"""
SRC = {"id": "bible-xx-tst", "owner": "BSOJ", "repo": "xx_tst", "tag": "v1", "lang": "xx", "door43_lang": "xx"}


def make_zip(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for name, text in files.items():
            z.writestr("xx_tst/" + name, text)


class MorphTest(unittest.TestCase):
    def test_convert(self):
        c = bible_build.convert_morphology
        self.assertEqual(c("He,Ncmsc"), "HNcmsc")
        self.assertEqual(c("He,R:Td:Ncmsa"), "HR/Td/Ncmsa")
        self.assertEqual(c("Ar,Ncmsa"), "ANcmsa")
        self.assertEqual(c("Gr,N,,,,,DMS,"), "Gr,N,,,,,DMS,")
        self.assertIsNone(c(None))
        self.assertIsNone(c(""))


class MergeTest(unittest.TestCase):
    def frag(self, usfm, c, v):
        return bible_build.Frag.from_verse(usfm_aligned.parse_book(usfm).chapters[c][v])

    def test_merge_shifts_offsets_and_records_source(self):
        text = "\\id 3JN\n\\c 1\n\\p\n" + zverse(14, ["a%d" % i for i in range(9)]) + \
               "\\q1\n" + zverse(15, ["b0", "b1", "b2"])
        first, second = self.frag(text, 1, 14), self.frag(text, 1, 15)
        second.source_verses = [{"chapter": 1, "verse": 15, "start": 0, "end": 2}]
        m = bible_build.merge_frags(first, second)
        self.assertEqual(m.word_count, 12)
        self.assertEqual(m.text, "a0 a1 a2 a3 a4 a5 a6 a7 a8 b0 b1 b2")
        self.assertEqual([r["spans"][0] for r in m.rows][-3:], [(9, 9), (10, 10), (11, 11)])
        self.assertEqual(m.source_verses, [{"chapter": 1, "verse": 15, "start": 9, "end": 11}])
        self.assertTrue(m.paragraph_start)
        # poetry lines of the second part cover its shifted words, the first line is stretched to word 0
        self.assertEqual(m.lines[0]["start"], 0)
        self.assertEqual(m.lines[-1]["end"], 11)
        self.assertEqual(json.loads(m.formatting())["source_verses"][0]["start"], 9)

    def test_formatting_matches_parser(self):
        usfm = "\\id TST\n\\c 1\n\\s1 Head\n\\p\n" + zverse(1, ["a", "b"]).rstrip("\n") + " \\wj tail\\wj*\n"
        vr = usfm_aligned.parse_book(usfm).chapters[1][1]
        self.assertEqual(bible_build.Frag.from_verse(vr).formatting(), vr.formatting_json())


@unittest.skipUnless(schema.schema_available("bible"), "bible schema dir not found (set BIBLE_REPO)")
class BuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.zip = os.path.join(self.dir, "src.zip")
        make_zip(self.zip, {"manifest.yaml": MANIFEST, "65-3JN.usfm": book_3jn(), "66-3JN.usfm": book_3jn(),
                            "67-REV.usfm": book_rev(), "LICENSE.md": "Copyright \u00a9 2022 by Test Org\n",
                            "front/intro.md": "x"})

    def build(self, name="out", src=None, ref=None):
        with fetch.Archive(self.zip) as a:
            return bible_build.build(a, dict(SRC, **(src or {})), os.path.join(self.dir, name), ref)

    def test_build_rows_and_merges(self):
        rep = self.build()
        self.assertEqual(rep["abbreviation"], "TST")
        self.assertEqual(rep["books"], ["3JN", "REV"])           # duplicate 3JN dropped
        self.assertEqual(rep["module_uuid"], bible_build.identity.module_uuid("bible", "TST", "xx"))
        con = sqlite3.connect(rep["file"])
        self.assertTrue(rep["file"].endswith("bible_TST.db"))
        # 3JN 1:15 folded into 1:14; Rev 12:18 prefixed to 13:1
        self.assertEqual(con.execute("SELECT COUNT(*) FROM bible_verse WHERE verse_id/1000000=64").fetchone()[0], 14)
        text, fmt, wc = con.execute("SELECT text, formatting, word_count FROM bible_verse WHERE verse_id=64001014"
                                    ).fetchone()
        self.assertEqual(wc, 12)
        self.assertEqual(json.loads(fmt)["source_verses"], [{"chapter": 1, "verse": 15, "start": 9, "end": 11}])
        self.assertIsNone(con.execute("SELECT 1 FROM bible_verse WHERE verse_id=64001015").fetchone())
        rows = con.execute("SELECT word_position_start, word_position_end, original_word, strongs_number, lemma, "
                           "gloss, metadata, morphology FROM interlinear_word WHERE verse_id=64001014 "
                           "ORDER BY word_position_start").fetchall()
        self.assertEqual(len(rows), 12)
        self.assertEqual(rows[9][:6], (9, 9, "c15_0", "H3117", "strong:H3117", "b0"))
        self.assertEqual(json.loads(rows[9][6]), {"lemma": "lem0", "prefix": "b"})
        self.assertEqual(rows[0][7], "Gr,N")
        rev = con.execute("SELECT text, formatting FROM bible_verse WHERE verse_id=66013001").fetchone()
        self.assertEqual(rev[0], "f1 f2 f3 h1 h2")
        self.assertEqual(json.loads(rev[1])["source_verses"],
                         [{"chapter": 12, "verse": 18, "start": 0, "end": 2},
                          {"chapter": 13, "verse": 1, "start": 3, "end": 4}])
        self.assertEqual(con.execute("SELECT COUNT(*) FROM bible_verse WHERE verse_id=66012018").fetchone()[0], 0)
        self.assertEqual([r[0] for r in con.execute("SELECT feature_name FROM module_feature ORDER BY feature_id")],
                         ["strongs_numbers", "interlinear", "morphology"])
        info = con.execute("SELECT content_version, license_spdx, copyright, right_to_left, source_url, "
                           "versification FROM module_info").fetchone()
        self.assertEqual(info[0], "door43-v1")
        self.assertEqual(info[1], "CC-BY-SA-4.0")
        self.assertIn("Word alignment and Strong's: \u00a9 BSOJ / BCS, CC BY-SA 4.0", info[2])
        self.assertEqual(info[3:], (1, "https://git.door43.org/BSOJ/xx_tst/releases/tag/v1", "kjv-english"))
        con.close()

    def test_rows_without_strongs_are_skipped(self):
        usfm = "\\id 3JN\n\\c 1\n\\p\n" + zverse(1, ["a"], strong="l") + zverse(2, ["b"])
        make_zip(self.zip, {"manifest.yaml": MANIFEST, "65-3JN.usfm": usfm})
        rep = self.build()
        self.assertEqual(rep["interlinear_rows"], 1)
        self.assertEqual(rep["rows_skipped_no_strongs"], 1)

    def test_lsg_identity(self):
        make_zip(self.zip, {"manifest.yaml": MANIFEST, "65-3JN.usfm": book_3jn()})
        rep = self.build(src={"repo": "fr_lsg", "lang": "fr"})
        self.assertEqual(rep["abbreviation"], "LSG1910-NT")
        self.assertEqual(rep["module_uuid"], bible_build.identity.deterministic_uuid("bible:lsg1910-nt:fr"))
        self.assertFalse(rep["identity_reused"])

    def test_identity_reuse_only_when_compare_passes(self):
        first = self.build("first")
        ref = os.path.join(self.dir, "ref.db")
        shutil.copy(first["file"], ref)
        con = sqlite3.connect(ref)
        con.execute("UPDATE module_info SET abbreviation='REF', module_uuid='ref-uuid', copyright='Public domain', "
                    "license_spdx='PD', full_name='Ref name', metadata='{\"attribution\":\"Ref attribution.\","
                    "\"trademark_removed\":true}'")
        con.commit()
        con.close()
        rep = self.build("second", src={"abbr_0220": "REF"}, ref=ref)
        self.assertTrue(rep["identity_reused"])
        self.assertEqual((rep["abbreviation"], rep["module_uuid"]), ("REF", "ref-uuid"))
        self.assertTrue(rep["file"].endswith("bible_REF.db"))
        con = sqlite3.connect(rep["file"])
        copyright_, name, meta = con.execute("SELECT copyright, full_name, metadata FROM module_info").fetchone()
        self.assertTrue(copyright_.startswith("Text: Public domain. Word alignment"))
        self.assertEqual(name, "Ref name")
        meta = json.loads(meta)
        self.assertIn("Ref attribution.", meta["attribution"])
        self.assertTrue(meta["trademark_removed"])
        con.close()
        # text differs in many verses: the identity is not reused
        con = sqlite3.connect(ref)
        con.execute("UPDATE bible_verse SET text='changed' WHERE verse_id % 2 = 0")
        con.commit()
        con.close()
        rep = self.build("third", src={"abbr_0220": "REF"}, ref=ref)
        self.assertFalse(rep["identity_reused"])
        self.assertEqual(rep["abbreviation"], "REF-ALN")
        self.assertIn("failed", rep["identity_reason"])
        self.assertTrue(rep["file"].endswith("bible_REF-ALN.db"))


if __name__ == "__main__":
    unittest.main()
