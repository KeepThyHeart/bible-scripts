"""Tiny Markdown -> HTML-subset renderer for Door43 prose (design sec. 3).

Output tags: h3..h5, p, ul/ol/li, strong, em, blockquote, hr, a, br.  Raw HTML
is escaped (the single exception is a literal <br>, <br/> or <br />, which
becomes <br>).  Link hrefs are emitted verbatim (only ", < and > are
escaped) so links.py can post-process them.  Images are dropped, code spans
become plain text, tables are not supported (pipe lines are ordinary text).
The standard library only.
"""
import re

__all__ = ["render", "render_inline", "unescape_tsv"]

_LI = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_HR = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
_HEAD = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_BQ = re.compile(r"^\s{0,3}>\s?(.?.*)$")
_BR = re.compile(r"<br\s*/?>", re.I)
_PH = "%d"
_PH_RE = re.compile("(\\d+)")


def unescape_tsv(s):
    """Turn the literal two-character escape \\n of TSV cells into newlines."""
    return s.replace("\\n", "\n")


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_href(s):
    return s.replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


_LINK = re.compile(r"(!?)\[((?:[^\[\]]|\[[^\]]*\])*)\]\(([^)]*)\)")
_AUTO = re.compile(r"<(https?://[^>\s]+)>")
_BARE = re.compile(r"\[\[[^\]]*\]\]")
_RC = re.compile(r"rc:\s?//[^\s)\]]*")
_CODE = re.compile(r"(`+)(.+?)\1")


def render_inline(text, _held=None):
    """Inline Markdown -> HTML (no block structure)."""
    top = _held is None
    held = [] if top else _held

    def hold(html):
        held.append(html)
        return _PH % (len(held) - 1)

    # backslash escapes of punctuation
    text = re.sub(r"\\([\\`*_{}\[\]()#+\-.!>~|])", lambda m: hold(_esc(m.group(1))), text)
    text = _CODE.sub(lambda m: hold(_esc(m.group(2))), text)

    def link(m):
        if m.group(1):
            return ""  # image dropped (the app works offline)
        href = m.group(3).strip()
        href = re.sub(r'\s+"[^"]*"$', "", href)
        if href.startswith("<") and href.endswith(">"):
            href = href[1:-1]
        label = render_inline(m.group(2), held)
        return hold('<a href="%s">%s</a>' % (_esc_href(href), label))

    text = _LINK.sub(link, text)
    text = _AUTO.sub(lambda m: hold('<a href="%s">%s</a>' % (_esc_href(m.group(1)), _esc(m.group(1)))), text)
    text = _BARE.sub(lambda m: hold(_esc(m.group(0))), text)
    text = _RC.sub(lambda m: hold(_esc(m.group(0))), text)
    text = _BR.sub(lambda m: hold("<br>"), text)
    text = _esc(text)
    text = re.sub(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*", r"<strong><em>\1</em></strong>", text)
    text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(?=[^\s*])(.+?)(?<=[^\s*])\*", r"<em>\1</em>", text)
    text = re.sub(r"(?<!\w)_(?=[^\s_])(.+?)(?<=[^\s_])_(?!\w)", r"<em>\1</em>", text)
    if not top:
        return text  # the outermost call restores the shared placeholders
    # restore placeholders (they may nest: a link label holds other placeholders)
    for _ in range(4):
        new = _PH_RE.sub(lambda m: held[int(m.group(1))], text)
        if new == text:
            break
        text = new
    return text


def _para_text(lines):
    out = []
    for i, ln in enumerate(lines):
        hard = ln.endswith("  ") or ln.endswith("\\")
        ln = ln.strip()
        if ln.endswith("\\"):
            ln = ln[:-1]
        out.append(ln)
        if hard and i < len(lines) - 1:
            out.append("")
        elif i < len(lines) - 1:
            out.append(" ")
    s = render_inline("".join(out))
    return s.replace("", "<br>")


def _starts_block(ln):
    return bool(_HEAD.match(ln) or _HR.match(ln) or _LI.match(ln) or _BQ.match(ln))


def _blocks(lines):
    """Return a list of (kind, html) blocks; kind is 'p' for paragraphs."""
    out = []
    i, n = 0, len(lines)
    while i < n:
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue
        m = _HEAD.match(ln)
        if m:
            lvl = min(len(m.group(1)) + 2, 5)
            out.append(("h", "<h%d>%s</h%d>" % (lvl, render_inline(m.group(2)), lvl)))
            i += 1
            continue
        if _HR.match(ln):
            out.append(("hr", "<hr>"))
            i += 1
            continue
        if _BQ.match(ln):
            inner = []
            while i < n and _BQ.match(lines[i]):
                inner.append(_BQ.match(lines[i]).group(1))
                i += 1
            body = "".join(h for _, h in _blocks(inner))
            out.append(("bq", "<blockquote>%s</blockquote>" % body))
            continue
        m = _LI.match(ln)
        if m:
            html, i = _list(lines, i)
            out.append(("list", html))
            continue
        para = [ln]
        i += 1
        while i < n and lines[i].strip() and not _starts_block(lines[i]):
            para.append(lines[i])
            i += 1
        out.append(("p", "<p>%s</p>" % _para_text(para)))
    return out


def _list(lines, i):
    n = len(lines)
    m = _LI.match(lines[i])
    indent0 = len(m.group(1).expandtabs(4))
    ordered = m.group(2)[0].isdigit()
    items = []
    while i < n:
        m = _LI.match(lines[i])
        if not m:
            break
        ind = len(m.group(1).expandtabs(4))
        if ind != indent0:
            break
        if m.group(2)[0].isdigit() != ordered:
            break
        cind = len(m.group(1).expandtabs(4)) + len(m.group(2)) + 1
        item = [m.group(3)]
        i += 1
        while i < n:
            ln = lines[i]
            if not ln.strip():
                j = i
                while j < n and not lines[j].strip():
                    j += 1
                if j < n and len(lines[j]) - len(lines[j].lstrip()) >= cind:
                    item.append("")
                    i = j
                    continue
                break
            lead = len(ln.expandtabs(4)) - len(ln.expandtabs(4).lstrip())
            if lead >= cind:
                item.append(ln.expandtabs(4)[cind:])
            elif _starts_block(ln):
                break
            else:
                item.append(ln.strip())  # lazy continuation
            i += 1
        blocks = _blocks(item)
        if blocks and blocks[0][0] == "p" and (len(blocks) == 1 or "" not in item):
            blocks[0] = ("tight", blocks[0][1][3:-4])  # tight item: no <p>
        inner = "".join(h for _, h in blocks)
        items.append("<li>%s</li>" % inner)
        # skip blank lines before a following sibling
        j = i
        while j < n and not lines[j].strip():
            j += 1
        if j < n and _LI.match(lines[j]):
            m2 = _LI.match(lines[j])
            if len(m2.group(1).expandtabs(4)) == indent0:
                i = j
    tag = "ol" if ordered else "ul"
    return "<%s>%s</%s>" % (tag, "".join(items), tag), i


def render(md):
    """Render Markdown text to the HTML subset; blocks are newline-joined."""
    if not md:
        return ""
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(h for _, h in _blocks(lines))
