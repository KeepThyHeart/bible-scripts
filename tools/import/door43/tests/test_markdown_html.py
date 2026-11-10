import unittest

from door43lib.markdown_html import render, render_inline, unescape_tsv


class MarkdownTests(unittest.TestCase):
    def test_headings(self):
        self.assertEqual(render("# A\n## B\n### C\n#### D"),
                         "<h3>A</h3>\n<h4>B</h4>\n<h5>C</h5>\n<h5>D</h5>")

    def test_paragraphs_and_breaks(self):
        self.assertEqual(render("one\ntwo\n\nthree  \nfour"), "<p>one two</p>\n<p>three<br>four</p>")

    def test_inline(self):
        self.assertEqual(render_inline("**b** *i* _j_ ***x***"),
                         "<strong>b</strong> <em>i</em> <em>j</em> <strong><em>x</em></strong>")
        self.assertEqual(render_inline("snake_case_word"), "snake_case_word")

    def test_escaping(self):
        self.assertEqual(render("a <b>x</b> & c"), "<p>a &lt;b&gt;x&lt;/b&gt; &amp; c</p>")
        self.assertEqual(render("<script>alert(1)</script>"), "<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>")

    def test_br_literal_allowed(self):
        self.assertEqual(render("a<br>b <br/> c"), "<p>a<br>b <br> c</p>")

    def test_links_hrefs_untouched(self):
        self.assertEqual(render("[God](../kt/god.md)"), '<p><a href="../kt/god.md">God</a></p>')
        self.assertEqual(render("[v](rc://*/tn/help/1jn/01/05) x"),
                         '<p><a href="rc://*/tn/help/1jn/01/05">v</a> x</p>')
        self.assertEqual(render("[v](rc: //*/tn/help/1jn/01/05)"),
                         '<p><a href="rc: //*/tn/help/1jn/01/05">v</a></p>')
        self.assertEqual(render("[x](https://a.b/?q=1&r=2)"),
                         '<p><a href="https://a.b/?q=1&r=2">x</a></p>')
        self.assertEqual(render("[x](about:blank)"), '<p><a href="about:blank">x</a></p>')

    def test_bare_rc_link_literal(self):
        self.assertEqual(render("see [[rc://*/tw/dict/bible/kt/god]] now"),
                         "<p>see [[rc://*/tw/dict/bible/kt/god]] now</p>")

    def test_emphasis_inside_link_label(self):
        self.assertEqual(render("[**a** b](x.md)"), '<p><a href="x.md"><strong>a</strong> b</a></p>')

    def test_image_dropped(self):
        self.assertNotIn("img", render("![a](b.jpg)"))
        self.assertNotIn("b.jpg", render("![a](b.jpg)"))

    def test_lists(self):
        self.assertEqual(render("* one\n* two\n  - sub *a*\n  - sub b\n* three"),
                         "<ul><li>one</li><li>two<ul><li>sub <em>a</em></li><li>sub b</li></ul></li>"
                         "<li>three</li></ul>")
        self.assertEqual(render("1. a\n2. b"), "<ol><li>a</li><li>b</li></ol>")
        self.assertEqual(render("- a\n\n- b"), "<ul><li>a</li><li>b</li></ul>")

    def test_blockquote_and_hr(self):
        self.assertEqual(render("> q **x**\n> more\n\n---"),
                         "<blockquote><p>q <strong>x</strong> more</p></blockquote>\n<hr>")

    def test_bold_line_is_not_list(self):
        self.assertEqual(render("**Bold** start"), "<p><strong>Bold</strong> start</p>")

    def test_empty(self):
        self.assertEqual(render(""), "")

    def test_unescape_tsv(self):
        self.assertEqual(unescape_tsv("a\\nb\\n\\nc"), "a\nb\n\nc")
        self.assertEqual(render(unescape_tsv("a\\n\\nb")), "<p>a</p>\n<p>b</p>")


if __name__ == "__main__":
    unittest.main()


class NestedPlaceholderTest(unittest.TestCase):
    def test_link_label_with_escape_and_code(self):
        from door43lib import markdown_html
        out = markdown_html.render_inline(r"see [a \* `b` c](http://x.org/y) now \*done")
        self.assertIn('<a href="http://x.org/y">a * b c</a>', out)
        self.assertIn("now *done", out)
