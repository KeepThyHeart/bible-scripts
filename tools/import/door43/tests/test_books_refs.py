import unittest

from door43lib import books
from door43lib.refs import Ref, RefError, parse_reference


class BooksTest(unittest.TestCase):
    def test_codes(self):
        self.assertEqual(books.book_number('GEN'), 1)
        self.assertEqual(books.book_number('3jn'), 64)
        self.assertEqual(books.book_number('65-3JN'), 64)
        self.assertEqual(books.book_number('29-JOL.usfm'), 29)
        self.assertEqual(books.book_number('REV'), 66)
        self.assertEqual(books.book_number('13-1CH'), 13)
        self.assertEqual(books.book_number('1jn'), 62)

    def test_all_66(self):
        self.assertEqual([books.book_number(c) for c in books.USFM_CODES], list(range(1, 67)))
        self.assertEqual(books.usfm_code(64), '3JN')

    def test_unknown(self):
        with self.assertRaises(books.UnknownBook):
            books.book_number('XXX')
        self.assertIsNone(books.try_book_number('front'))

    def test_verse_id(self):
        self.assertEqual(books.verse_id(64, 1, 14), 64001014)
        self.assertEqual(books.split_verse_id(64001014), (64, 1, 14))


class RefsTest(unittest.TestCase):
    def p(self, s, **kw):
        return parse_reference(s, **kw)

    def test_tsv_forms(self):
        self.assertEqual(self.p('front:intro', book=64), Ref(64, None, None, None, None, 'book'))
        self.assertEqual(self.p('1:intro', book=64), Ref(64, 1, None, 1, None, 'chapter'))
        self.assertEqual(self.p('1:5', book=64), Ref(64, 1, 5, 1, 5, 'verse'))
        self.assertEqual(self.p('01:05', book='3JN'), Ref(64, 1, 5, 1, 5, 'verse'))
        self.assertEqual(self.p('1:3-5'), Ref(None, 1, 3, 1, 5, 'passage'))
        self.assertEqual(self.p('1:20-2:3'), Ref(None, 1, 20, 2, 3, 'passage'))

    def test_list(self):
        r = self.p('1:3,5')
        self.assertEqual((r.ch, r.v1, r.ch2, r.v2, r.level), (1, 3, 1, 5, 'passage'))
        self.assertEqual(r.pieces, ((1, 3, 1, 3), (1, 5, 1, 5)))
        r = self.p('1:9,3-4')
        self.assertEqual((r.v1, r.v2), (3, 9))

    def test_degenerate_range_is_verse(self):
        self.assertEqual(self.p('1:5-5').level, 'verse')

    def test_malformed(self):
        for bad in (':', '', '   ', '1', '1:', ':5', 'a:b', '1:5-', '1:5-3', '1:3,', 'front:foo', '1:x'):
            with self.subTest(bad=bad), self.assertRaises(RefError):
                self.p(bad)
        with self.assertRaises(RefError):
            parse_reference(None)

    def test_rc(self):
        self.assertEqual(self.p('rc://*/tn/help/1jn/01/05'), Ref(62, 1, 5, 1, 5, 'verse'))
        self.assertEqual(self.p('rc://xx/tn/help/3JN/01/14'), Ref(64, 1, 14, 1, 14, 'verse'))
        self.assertEqual(self.p('rc://*/tn/help/exo/12'), Ref(2, 12, None, 12, None, 'chapter'))
        self.assertEqual(self.p('rc://*/tn/help/exo'), Ref(2, None, None, None, None, 'book'))
        self.assertEqual(self.p('rc: //*/tn/help/jol/02/28'), Ref(29, 2, 28, 2, 28, 'verse'))
        with self.assertRaises(RefError):
            self.p('rc://*/tn/help/zzz/01/01')
        with self.assertRaises(RefError):
            self.p('rc://*/tw/dict/bible/kt/god')

    def test_relative(self):
        self.assertEqual(self.p('./05.md', book=64, chapter=1), Ref(64, 1, 5, 1, 5, 'verse'))
        self.assertEqual(self.p('../02/07.md', book=64, chapter=1), Ref(64, 2, 7, 2, 7, 'verse'))
        self.assertEqual(self.p('../../1jn/01/05.md', book=64, chapter=1), Ref(62, 1, 5, 1, 5, 'verse'))
        self.assertEqual(self.p('./intro.md', book=64, chapter=3), Ref(64, 3, None, 3, None, 'chapter'))
        self.assertEqual(self.p('../front/intro.md', book=64, chapter=3), Ref(64, None, None, None, None, 'book'))
        with self.assertRaises(RefError):
            self.p('./nope.md', book=64, chapter=1)


if __name__ == '__main__':
    unittest.main()
