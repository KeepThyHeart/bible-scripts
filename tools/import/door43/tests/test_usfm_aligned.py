import os
import unittest

from door43lib import usfm_aligned as ua

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "aligned_synthetic.usfm")


def load():
    with open(FIX, encoding="utf-8") as fh:
        return fh.read()


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.b = ua.parse_book(load())

    def test_code_and_structure(self):
        self.assertEqual(self.b.code, "TST")
        self.assertEqual(sorted(self.b.chapters), [1, 2])
        self.assertEqual(sorted(self.b.chapters[1]), [1, 2])

    def test_prefix_word_joined_and_text(self):
        v = self.b.chapters[1][1]
        self.assertEqual(v.words, ["L’ancien,", "to", "the", "friend."])
        self.assertEqual(v.text, "L’ancien, to the friend.")

    def test_nested_and_multiple_zaln_e(self):
        v = self.b.chapters[1][1]
        by = {r.x_content: r for r in v.rows}
        self.assertEqual(by["alpha"].spans, [(0, 0)])
        self.assertEqual(by["beta"].word_indexes, [2, 3])
        self.assertEqual(by["gamma"].word_indexes, [2, 3])
        self.assertEqual(by["gamma"].gloss(v.words), "the friend.")
        self.assertEqual([r.x_content for r in v.rows], ["alpha", "beta", "gamma"])

    def test_row_fields_and_strongs(self):
        r = self.b.chapters[1][1].rows[1]
        self.assertEqual(r.strongs_raw, "G00270")
        self.assertEqual((r.occurrence, r.occurrences), (1, 1))
        self.assertEqual(r.morph, "Gr,N")
        self.assertEqual(r.lemma, "bb")
        self.assertEqual(r.strongs_number, "G0027")

    def test_discontiguous_group_merges(self):
        v = self.b.chapters[1][2]
        self.assertEqual(v.text, "one and two mid three")
        day = [r for r in v.rows if r.x_content == "day"]
        self.assertEqual(len(day), 1)
        self.assertEqual(day[0].spans, [(0, 0), (4, 4)])
        self.assertEqual(day[0].extra_word_positions, "4")
        self.assertEqual(day[0].strongs_number, "H3117")
        self.assertEqual(day[0].strongs_meta.get("prefix"), "b")

    def test_prefix_only_strong_has_no_number(self):
        v = self.b.chapters[1][2]
        to = [r for r in v.rows if r.x_content == "to"][0]
        self.assertEqual(to.strongs_raw, "l")
        self.assertIsNone(to.strongs_number)

    def test_footnotes_dropped_and_counted(self):
        self.assertEqual(self.b.chapters[1][1].footnotes_dropped, 1)
        self.assertEqual(self.b.chapters[1][2].footnotes_dropped, 1)
        self.assertEqual(self.b.footnotes_dropped, 2)
        self.assertNotIn("note", self.b.chapters[1][1].text)

    def test_heading_and_paragraph(self):
        v = self.b.chapters[1][1]
        self.assertEqual(v.heading, "A heading")
        self.assertTrue(v.paragraph_start)
        self.assertEqual(v.blocks, {"paragraph_start": True, "heading": "A heading"})
        self.assertEqual(v.formatting_json(),
                         '{"v":1,"block":{"paragraph_start":true,"heading":"A heading"}}')
        self.assertFalse(self.b.chapters[1][2].paragraph_start)
        self.assertIsNone(self.b.chapters[1][2].formatting())

    def test_poetry_wj_and_d(self):
        v1 = self.b.chapters[2][1]
        self.assertEqual(v1.heading, "Superscription text")
        self.assertEqual(v1.text, "First red words tail second line")
        self.assertEqual(v1.lines, [{"start": 0, "end": 3, "level": 1},
                                    {"start": 4, "end": 5, "level": 2}])
        self.assertEqual(v1.spans, [{"type": "words_of_christ", "start": 1, "end": 2}])
        v2 = self.b.chapters[2][2]
        self.assertEqual(v2.lines, [{"start": 0, "end": 2, "level": 2}])  # poetry carries over
        self.assertEqual(v2.rows, [])
        v3 = self.b.chapters[2][3]
        self.assertEqual(v3.lines, [])
        self.assertTrue(v3.paragraph_start)
        self.assertEqual(v3.verse_end, 4)

    def test_chapter2_v1_not_paragraph(self):
        self.assertFalse(self.b.chapters[2][1].paragraph_start)


class DedupeTests(unittest.TestCase):
    A = "\\id 2CH x\n\\c 1\n\\p\n\\v 1 hello\n"

    def test_book_code(self):
        self.assertEqual(ua.book_code(self.A), "2CH")
        self.assertIsNone(ua.book_code("\\c 1"))

    def test_identical_dup_dropped(self):
        out = ua.dedupe_books([("13-2CH", self.A), ("14-2CH", self.A + "\n"),
                               ("x-1CH", "\\id 1CH\n\\c 1\n")])
        self.assertEqual([n for n, _ in out], ["13-2CH", "x-1CH"])

    def test_differing_dup_errors(self):
        with self.assertRaises(ua.DuplicateBookError):
            ua.dedupe_books([("a", self.A), ("b", self.A.replace("hello", "bye"))])


if __name__ == "__main__":
    unittest.main()
