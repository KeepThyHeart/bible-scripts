import json
import os
import tempfile
import unittest

from door43lib.versification import Merge, UnmappedVerse, Versification


class VersificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v = Versification.load()

    def test_identity(self):
        self.assertEqual(self.v.map_verse(64, 1, 14), (1, 14))
        self.assertEqual(self.v.map_verse(1, 1, 1), (1, 1))
        self.assertEqual(self.v.map_verse(29, 3, 21), (3, 21))  # Joel 3:21 (English-shaped OT)

    def test_3jn_merge(self):
        self.assertEqual(self.v.map_verse(64, 1, 15), (1, 14))
        m = self.v.merge_info(64, 1, 15)
        self.assertEqual((m.into_ch, m.into_v, m.prefix), (1, 14, False))
        self.assertIsNone(self.v.merge_info(64, 1, 14))

    def test_rev_merge_prefix(self):
        self.assertEqual(self.v.map_verse(66, 12, 18), (13, 1))
        self.assertTrue(self.v.merge_info(66, 12, 18).prefix)
        self.assertEqual(self.v.map_verse(66, 13, 1), (13, 1))

    def test_unmapped_raises(self):
        for args in ((64, 1, 16), (29, 4, 1), (1, 51, 1), (1, 1, 0), (67, 1, 1)):
            with self.subTest(args=args), self.assertRaises(UnmappedVerse):
                self.v.map_verse(*args)

    def test_verse_id(self):
        self.assertEqual(self.v.map_verse_id(64001015), 64001014)
        self.assertEqual(self.v.map_verse_id(66012018), 66013001)

    def test_rules_apply_and_shape_checked(self):
        data = {'versification': 'x', 'rules': [{'b': 29, 'c': 2, 'from': 28, 'to': 32, 'dc': 3, 'off': -27}],
                'merges': []}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'v.json')
            with open(p, 'w') as f:
                json.dump(data, f)
            v = Versification.load(p)
        self.assertEqual(v.map_verse(29, 2, 28), (3, 1))
        self.assertEqual(v.map_verse(29, 2, 27), (2, 27))
        bad = Versification([{'b': 29, 'c': 2, 'from': 1, 'to': 1, 'dc': 2, 'off': 99}], [], v.kjv_counts)
        with self.assertRaises(UnmappedVerse):
            bad.map_verse(29, 2, 1)

    def test_no_shape_means_no_check(self):
        v = Versification([], [Merge(1, 1, 2, 1, 1, False, '')], None)
        self.assertEqual(v.map_verse(1, 9, 99), (9, 99))
        self.assertEqual(v.map_verse(1, 1, 2), (1, 1))


if __name__ == '__main__':
    unittest.main()
