import unittest

from door43lib.arabic_norm import build_word_set, normalize_arabic, strip_diacritics
from door43lib.strongs import extract_strongs, normalize_strong


class StrongsTest(unittest.TestCase):
    def test_table(self):
        cases = [
            ('G00270', ('G0027', {})),
            ('G00271', ('G0027', {'x_strong': 'G00271'})),
            ('G4245', ('G4245', {})),
            ('H5921a', ('H5921', {'x_strong': 'H5921a'})),
            ('b:H3117', ('H3117', {'prefix': 'b'})),
            ('c:H0408', ('H0408', {'prefix': 'c'})),
            ('c:b:H0408', ('H0408', {'prefix': 'c:b'})),
            ('l', (None, {})),
            ('l:', (None, {'prefix': 'l'})),
            ('H430', ('H0430', {})),
            ('', (None, {})),
            ('X123', (None, {})),
        ]
        for raw, want in cases:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_strong(raw), want)

    def test_extract(self):
        text = 'Números de Strong: H136, H0136a, G5538; G2316 e H5921a.'
        self.assertEqual(extract_strongs(text), ['H0136', 'G5538', 'G2316', 'H5921'])
        self.assertEqual(extract_strongs(None), [])


class ArabicTest(unittest.TestCase):
    def test_simple_letters_and_digits(self):
        out, c = normalize_arabic('\u06be\u06a9 \u06f1\u06f2')
        self.assertEqual(out, '\u0647\u0643 \u0661\u0662')
        self.assertEqual(c['06BE->0647'], 1)
        self.assertEqual(c['06A9->0643'], 1)
        self.assertEqual(c['06F0-9->0660-9'], 2)

    def test_farsi_yeh_default(self):
        out, c = normalize_arabic('\u0639\u0644\u06cc \u0641\u06cc')
        self.assertEqual(out, '\u0639\u0644\u064a \u0641\u064a')
        self.assertEqual(c, {'06CC->064A': 2})

    def test_final_alef_maksura_from_wordlist(self):
        words = build_word_set(['\u0639\u0644\u0649 \u0641\u064e\u064a'])
        # \u0639\u0644\u0649 known with 0649 only; \u0641\u064a known with 064A
        out, c = normalize_arabic('\u0639\u0644\u06cc \u0641\u06cc \u06cc\u0648\u0645', words)
        self.assertEqual(out, '\u0639\u0644\u0649 \u0641\u064a \u064a\u0648\u0645')
        self.assertEqual(c['06CC->0649'], 1)
        self.assertEqual(c['06CC->064A'], 2)  # \u0641\u064a and the initial yeh

    def test_both_spellings_known_prefers_yeh(self):
        words = {'\u0639\u0644\u0649', '\u0639\u0644\u064a'}
        out, _ = normalize_arabic('\u0639\u0644\u06cc', words)
        self.assertEqual(out, '\u0639\u0644\u064a')

    def test_final_with_diacritic(self):
        words = build_word_set(['\u0645\u0648\u0633\u0649'])
        out, _ = normalize_arabic('\u0645\u0648\u0633\u06cc\u064e', words)
        self.assertEqual(out, '\u0645\u0648\u0633\u0649\u064e')

    def test_untouched_text(self):
        s = 'Hello, \u0627\u0644\u0644\u0647 123!'
        self.assertEqual(normalize_arabic(s), (s, {}))

    def test_strip(self):
        self.assertEqual(strip_diacritics('\u0641\u064e\u0640\u064a'), '\u0641\u064a')


if __name__ == '__main__':
    unittest.main()
