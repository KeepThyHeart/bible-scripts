import unittest
from kth_prosody.symbols import kokoro_symbols
from kth_prosody.align import text_words, phoneme_words, align


class Symbols(unittest.TestCase):
    def test_oy_becomes_one_symbol(self):
        self.assertEqual(kokoro_symbols('ɡɹˈeɪt dʒˈɔɪ, dɪstɹˈɔɪ'), 'ɡɹˈeɪt dʒˈY, dɪstɹˈY')

    def test_saith_uses_kokoro_long_a(self):
        self.assertEqual(kokoro_symbols('ðˈʌs sˈeɪᵻθ ðə lˈɔːɹd;'), 'ðˈʌs sˈAᵻθ ðə lˈɔːɹd;')
        self.assertEqual(kokoro_symbols('hiː sˈeɪᵻθ.'), 'hiː sˈAᵻθ.')

    def test_other_long_a_unchanged(self):
        self.assertEqual(kokoro_symbols('sˈeɪ ˌʌntʊ jˈeɪ'), 'sˈeɪ ˌʌntʊ jˈeɪ')

    def test_oil_still_aligns(self):
        vocab = {c: i + 1 for i, c in enumerate(sorted(set("abcdefghijklmnopqrstuvwxyzæðɑɔəɚɛɪʃʊʌθɹɜɡŋˈˌːɾʒʔY ,.;:!?")))}
        tw = text_words('with oil')
        pw = phoneme_words(kokoro_symbols('wɪð ˈɔɪl'), vocab)
        self.assertEqual(align(tw, pw, set()), (0, 1))


if __name__ == '__main__':
    unittest.main()
