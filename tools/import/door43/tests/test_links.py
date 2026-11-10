import unittest

from door43lib import books, links, versification

V = versification.Versification.load()
UUID_TW = 'tw-uuid-1'
UUID_OBS = 'obs-uuid-1'


def reg(**kw):
    base = dict(tw_keys={'kt/god', 'other/sacrifice'}, tw_uuid=UUID_TW, obs_stories={48}, obs_uuid=UUID_OBS,
                versification=V, tw_words={'kt/god': 'Dios'})
    base.update(kw)
    return links.Registry(**base)


def vid(b, c, v):
    return books.verse_id(b, c, v)


class ScriptureTest(unittest.TestCase):
    def test_rc_verse(self):
        r = links.resolve_links('<p><a href="rc://*/tn/help/1jn/01/05">1 Jn 1:5</a></p>', reg())
        self.assertEqual(r.html, '<p><a href="#verse-62001005" class="scripture-link">1 Jn 1:5</a></p>')
        self.assertEqual(r.verse_links, [(62001005, 62001005)])
        self.assertEqual(r.stats['scripture']['resolved'], 1)

    def test_rc_any_lang_case_and_repaired(self):
        for h in ('rc://es/tn/help/1JN/01/05', 'rc: //*/tn/help/1jn/01/05', 'rc:/en/tn/help/1jn/01/05'):
            r = links.resolve_links('<a href="%s">x</a>' % h, reg())
            self.assertEqual(r.verse_links, [(62001005, 62001005)], h)

    def test_rc_localized_help_word(self):
        r = links.resolve_links('<a href="rc://es/tn/ayuda/jas/05/09">x</a>', reg())
        self.assertEqual(r.verse_links, [(vid(59, 5, 9), vid(59, 5, 9))])

    def test_chapter_only(self):
        r = links.resolve_links('<a href="rc://*/tn/help/exo/12">Ex 12</a>', reg())
        self.assertIn('href="#verse-2012001"', r.html)
        self.assertEqual(r.verse_links, [(2012001, 2012051)])

    def test_relative_forms(self):
        for h, expect in (('./05.md', vid(62, 1, 5)), ('../01/05.md', vid(62, 1, 5)),
                          ('../../3jn/01/05.md', vid(64, 1, 5))):
            r = links.resolve_links('<a href="%s">x</a>' % h, reg(), book=62, chapter=1)
            self.assertEqual(r.verse_links, [(expect, expect)], h)

    def test_relative_chapter_intro(self):
        r = links.resolve_links('<a href="../02/intro.md">x</a>', reg(), book='3JN', chapter=1)
        self.assertEqual(r.verse_links, [])  # 3JN has one chapter -> no KJV home
        self.assertEqual(r.html, 'x')
        r = links.resolve_links('<a href="../02/intro.md">x</a>', reg(), book='1JN', chapter=1)
        self.assertEqual(r.verse_links, [(62002001, 62002029)])

    def test_ufw_to_kjv_mapping(self):
        r = links.resolve_links('<a href="rc://*/tn/help/3jn/01/15">v15</a>', reg())
        self.assertEqual(r.verse_links, [(64001014, 64001014)])

    def test_unmappable_is_plain_text(self):
        r = links.resolve_links('<a href="rc://*/tn/help/3jn/01/99">v99</a>', reg())
        self.assertEqual(r.html, 'v99')
        self.assertEqual(r.verse_links, [])
        self.assertEqual(r.stats['scripture']['unresolved'], 1)
        self.assertEqual(r.stats['scripture']['samples'][0]['href'], 'rc://*/tn/help/3jn/01/99')

    def test_dedupe_and_bare_label(self):
        r = links.resolve_links('<a href="rc://*/tn/help/jhn/03/16">a</a> [[rc://*/tn/help/jhn/03/16]]', reg())
        self.assertEqual(r.verse_links, [(vid(43, 3, 16), vid(43, 3, 16))])
        self.assertIn('>JHN 3:16</a>', r.html)


class DictionaryTest(unittest.TestCase):
    def test_relative_and_rc(self):
        for h in ('../kt/god.md', './god.md', 'rc://*/tw/dict/bible/kt/god'):
            r = links.resolve_links('<a href="%s">Dios</a>' % h, reg())
            self.assertEqual(
                r.html, '<a href="#entry-kt/god" class="dictionary-link" data-module="tw-uuid-1">Dios</a>', h)

    def test_bare_uses_word(self):
        r = links.resolve_links('see [[rc://*/tw/dict/bible/kt/god]].', reg())
        self.assertEqual(
            r.html, 'see <a href="#entry-kt/god" class="dictionary-link" data-module="tw-uuid-1">Dios</a>.')

    def test_missing_entry_and_module(self):
        r = links.resolve_links('<a href="../kt/nothing.md">lbl</a>', reg())
        self.assertEqual(r.html, 'lbl')
        self.assertEqual(r.stats['dictionary']['unresolved'], 1)
        r = links.resolve_links('<a href="../kt/god.md">lbl</a>', reg(tw_uuid=None))
        self.assertEqual(r.html, 'lbl')

    def test_ambiguous_slug_is_plain(self):
        r = links.resolve_links('<a href="./x.md">lbl</a>', reg(tw_keys={'kt/x', 'names/x'}))
        self.assertEqual(r.html, 'lbl')


class OtherLinksTest(unittest.TestCase):
    def test_obs(self):
        for h in ('rc://*/tn/help/obs/48/06', 'rc://*/obs/book/obs/48/06', 'rc://*/help/obs/48/06'):
            r = links.resolve_links('<a href="%s">s</a>' % h, reg())
            self.assertEqual(r.html, '<a href="#section-48" class="book-link" data-module="obs-uuid-1">s</a>', h)
        r = links.resolve_links('<a href="rc://*/tn/help/obs/49/06">s</a>', reg())
        self.assertEqual(r.html, 's')
        r = links.resolve_links('<a href="rc://*/tn/help/obs/48/06">s</a>', reg(obs_uuid=None))
        self.assertEqual(r.html, 's')

    def test_ta_stripped(self):
        r = links.resolve_links('x <a href="rc://*/ta/man/translate/translate-names">names</a> y', reg())
        self.assertEqual(r.html, 'x names y')
        self.assertEqual(r.stats['ta']['stripped'], 1)

    def test_bare_ta_and_empty_parens(self):
        r = links.resolve_links('<p>Text (See: [[rc://*/ta/man/translate/figs-metaphor]]) end.</p>', reg())
        self.assertEqual(r.html, '<p>Text end.</p>')
        r = links.resolve_links('<p>(Ver: [[rc://*/ta/man/a]], [[rc://*/ta/man/b]]) ok</p>', reg())
        self.assertEqual(r.html, '<p> ok</p>')
        r = links.resolve_links('<p>(Ver: [[rc://*/ta/man/a]] algo)</p>', reg())
        self.assertNotIn('rc:', r.html)

    def test_http_kept(self):
        h = '<a href="https://example.org/x?a=1">site</a>'
        r = links.resolve_links(h, reg())
        self.assertEqual(r.html, h)
        self.assertEqual(r.stats['http']['resolved'], 1)

    def test_about_blank_and_unknown_scheme(self):
        r = links.resolve_links('<a href="about:blank">a</a> <a href="ftp://x/y">b</a>', reg())
        self.assertEqual(r.html, 'a b')
        self.assertEqual(r.stats['other']['unresolved'], 2)

    def test_samples_capped_at_20(self):
        html = ' '.join('<a href="rc://*/tn/help/3jn/01/%d">v</a>' % (100 + i) for i in range(30))
        r = links.resolve_links(html, reg())
        self.assertEqual(r.stats['scripture']['unresolved'], 30)
        self.assertEqual(len(r.stats['scripture']['samples']), 20)

    def test_idempotent_on_resolved_anchor(self):
        h = '<a href="#verse-62001005" class="scripture-link">x</a>'
        self.assertEqual(links.resolve_links(h, reg()).html, h)

    def test_no_registry_content(self):
        r = links.resolve_links('<a href="../kt/god.md">g</a> <a href="rc://*/tn/help/jhn/03/16">j</a>',
                                links.Registry())
        self.assertTrue(r.html.startswith('g <a'))
        self.assertEqual(r.verse_links, [(vid(43, 3, 16), vid(43, 3, 16))])

    def test_merge_stats(self):
        a = links.resolve_links('<a href="about:blank">a</a>', reg()).stats
        b = links.resolve_links('<a href="about:blank">a</a>', reg()).stats
        links.merge_stats(a, b)
        self.assertEqual(a['other']['unresolved'], 2)


if __name__ == '__main__':
    unittest.main()


class RelativeRepairTest(unittest.TestCase):
    def test_space_book_first_and_missing_dot(self):
        from door43lib import links
        for h in ('.. /luk/23/49.md', 'luk/23/49.md', '../../luk/23/49md'):
            r = links.resolve_links('<a href="%s">x</a>' % h, links.Registry(), book=43, chapter=1)
            self.assertIn('scripture-link', r.html, h)
