import io, json, os, tempfile, unittest
import numpy as np
from kth_prosody.align import text_words, phoneme_words, align
from kth_prosody.rules import load_rules, repeated_phrases, occurrences, word_stats, plan_edits, apply_edits
from kth_prosody.worker import serve

RULES = load_rules()
SMALL = set(RULES['repeated']['smallWords'])
VOCAB = {c: i + 1 for i, c in enumerate(sorted(set("abcdefghijklmnopqrstuvwxyzæðɑɔəɚɛɪʃʊʌθɹɜɡŋˈˌːɾʒʔ ,.;:!?")))}
EXO = ["And thou shalt put therein the ark of the testimony.",
       "And thou shalt bring in the table.",
       "And thou shalt set the altar of gold.",
       "And thou shalt anoint the laver."]


class Align(unittest.TestCase):
    def test_joined_pairs(self):
        tw = text_words("the ark of the testimony")
        pw = phoneme_words("ðɪ ˈɑːɹk ʌvðə tˈɛstɪmˌoʊni", VOCAB)
        self.assertEqual(align(tw, pw, SMALL), (0, 1, 2, 2, 3))

    def test_three_word_join(self):
        tw = text_words("which went forth to and fro")
        pw = phoneme_words("wˌɪtʃ wɛnt fˈɔːɹθ tˌuːəndfɹˈoʊ", VOCAB)
        self.assertEqual(align(tw, pw, SMALL), (0, 1, 2, 3, 3, 3))

    def test_markers_count_as_their_word(self):
        self.assertEqual(text_words("the ⟦tabernacle|tˈæbɚnˌækəl⟧ of"), ['the', 'tabernacle', 'of'])

    def test_no_consistent_match(self):
        self.assertIsNone(align(text_words("and thou shalt"), phoneme_words("mˈoʊsɪz sˈɛd", VOCAB), SMALL))


class Phrases(unittest.TestCase):
    def test_formula_found_content_left_alone(self):
        self.assertEqual(repeated_phrases(EXO, RULES), [('and', 'thou', 'shalt')])

    def test_not_and_all_are_content(self):
        t = ["Thou shalt not kill.", "Thou shalt not steal.", "Thou shalt not bear false witness."]
        self.assertEqual(repeated_phrases(t, RULES), [('thou', 'shalt')])
        t = ["all the people", "all the earth", "all the land"]
        self.assertEqual(repeated_phrases(t, RULES), [])

    def test_needs_min_count(self):
        self.assertEqual(repeated_phrases(EXO[:2], RULES), [])

    def test_occurrence_and_next_content_word(self):
        ps = "ænd ðˈaʊ ʃˌælt pˌʊt ðɛɹˈɪn ðɪ ˈɑːɹk ʌvðə tˈɛstɪmˌoʊni."
        pw, occ = occurrences(EXO[0], ps, VOCAB, [('and', 'thou', 'shalt')], RULES)
        self.assertEqual(occ, [([0, 1, 2], 3)])


class Edits(unittest.TestCase):
    def setUp(self):
        self.pw = phoneme_words("ænd ðˈaʊ ʃˌælt pˌʊt", VOCAB)          # tokens 1-3, 5-8, 10-14, 16-19
        self.frames = np.full(21, 2)
        self.f0 = np.full(2 * 42, 100.0); self.en = np.zeros(2 * 42)

    def set_peak(self, word, hz):
        cs = np.concatenate([[0], np.cumsum(self.frames)]); a, b, _ = self.pw[word]
        self.f0[2 * cs[a]:2 * cs[b + 1]] = hz

    def test_over_stressed_phrase_is_softened_and_verb_lifted(self):
        self.set_peak(1, 170); self.set_peak(2, 125); self.set_peak(3, 130)
        st = word_stats(self.pw, self.frames, self.f0, self.en)
        fr2, edits = plan_edits(self.pw, [([0, 1, 2], 3)], st, self.frames, RULES)
        self.assertEqual([(i, round(hz)) for i, hz, _ in edits], [(1, 60), (2, 15), (3, -10)])
        self.assertTrue((fr2[5:9] <= self.frames[5:9]).all())
        f0, en = apply_edits(self.pw, fr2, self.f0, self.en, edits)
        self.assertLess(f0.max(), 170)

    def test_already_natural_reading_is_left_alone(self):
        self.set_peak(1, 100); self.set_peak(3, 160)
        st = word_stats(self.pw, self.frames, self.f0, self.en)
        fr2, edits = plan_edits(self.pw, [([0, 1, 2], 3)], st, self.frames, RULES)
        self.assertEqual(edits, []); self.assertTrue((fr2 == self.frames).all())


class FakeModel:
    vocab = VOCAB
    def predict(self, ps, voice, speed):
        n = len([c for c in ps if c in VOCAB]) + 2
        return dict(n=n), np.full(n, 2)
    def pitch(self, st, frames):
        k = 2 * int(np.sum(frames)); return np.full(k, 120.0), np.zeros(k)
    def decode(self, st, f0, en):
        return np.zeros(len(f0) * 300, np.float32)


class Worker(unittest.TestCase):
    def run_lines(self, *reqs):
        out = io.StringIO()
        serve(FakeModel, RULES, io.StringIO('\n'.join(json.dumps(r) for r in reqs) + '\n'), out)
        return [json.loads(l) for l in out.getvalue().splitlines()]

    def test_hello_chapter_and_errors(self):
        d = tempfile.mkdtemp()
        pieces = [dict(key=f'{i}.0', text=t, phonemes="ænd ðˈaʊ ʃˌælt pˌʊt") for i, t in enumerate(EXO, 1)]
        hello, chap, bad = self.run_lines({'op': 'hello'}, {'op': 'chapter', 'voice': 'am_michael', 'speed': 0.9, 'outDir': d, 'pieces': pieces}, {'op': 'nope'})
        self.assertEqual((hello['ok'], hello['rules']['version']), (True, RULES['version']))
        self.assertTrue(chap['ok']); self.assertEqual(len(chap['pieces']), 4)
        p = chap['pieces'][0]
        self.assertEqual(os.path.getsize(p['file']), 4 * p['samples'])
        self.assertEqual(bad['ok'], False)


if __name__ == '__main__':
    unittest.main()
