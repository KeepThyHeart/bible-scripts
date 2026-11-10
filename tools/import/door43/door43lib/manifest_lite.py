"""Manifest reader: PyYAML when importable, else a small YAML-subset parser.

The subset covers what Door43 ``manifest.yaml`` files use: nested mappings, block
sequences (indented or not), plain / single- / double-quoted scalars, folded and
literal block scalars (``>``, ``>-``, ``|``, ``|-``), ``[a, b]`` flow lists and
comments. Differences from PyYAML: unquoted dates stay strings, and plain
scalars that continue on following indented lines are joined with a space.
"""
import re

try:  # pragma: no cover - environment dependent
    import yaml as _yaml
except ImportError:  # pragma: no cover
    _yaml = None


def load_manifest(text, prefer_yaml=True):
    if prefer_yaml and _yaml is not None:
        return _yaml.safe_load(text)
    return parse_lite(text)


_KEY_RE = re.compile(r'^((?:"[^"]*"|\'[^\']*\'|[^\s\'"#:\[\]{},&*!|>%@`-][^:#]*?|-[^\s:][^:#]*?))\s*:(?:\s+(.*))?$')


def _indent(line):
    return len(line) - len(line.lstrip(' '))


def _strip_comment(s):
    out, q = [], None
    for i, c in enumerate(s):
        if q:
            if c == q:
                q = None
        elif c in '"\'' and (i == 0 or s[i - 1] in ' [,{:'):
            q = c
        elif c == '#' and (i == 0 or s[i - 1] == ' '):
            break
        out.append(c)
    return ''.join(out).rstrip()


def _scalar(s):
    s = s.strip()
    if s == '' or s in ('~', 'null', 'Null', 'NULL'):
        return None
    if s[0] == '"' and s[-1] == '"' and len(s) > 1:
        body = s[1:-1]
        return re.sub(r'\\(u[0-9a-fA-F]{4}|.)', _esc, body)
    if s[0] == "'" and s[-1] == "'" and len(s) > 1:
        return s[1:-1].replace("''", "'")
    if s[0] == '[' and s[-1] == ']':
        inner = s[1:-1].strip()
        return [] if not inner else [_scalar(p) for p in _split_flow(inner)]
    if s in ('true', 'True', 'TRUE'):
        return True
    if s in ('false', 'False', 'FALSE'):
        return False
    if re.fullmatch(r'-?\d+', s):
        return int(s)
    if re.fullmatch(r'-?\d+\.\d+', s):
        return float(s)
    return s


def _esc(m):
    t = m.group(1)
    if t[0] == 'u' and len(t) == 5:
        return chr(int(t[1:], 16))
    return {'n': '\n', 't': '\t', '"': '"', '\\': '\\', '/': '/'}.get(t, '\\' + t)


def _split_flow(s):
    parts, cur, q = [], [], None
    for c in s:
        if q:
            cur.append(c)
            if c == q:
                q = None
        elif c in '"\'':
            q = c
            cur.append(c)
        elif c == ',':
            parts.append(''.join(cur))
            cur = []
        else:
            cur.append(c)
    parts.append(''.join(cur))
    return parts


class _Parser:
    def __init__(self, text):
        self.lines = text.replace('\r\n', '\n').replace('\t', '    ').split('\n')
        self.pos = 0

    def _skip(self):
        while self.pos < len(self.lines):
            s = self.lines[self.pos].strip()
            if s == '' or s.startswith('#') or s in ('---', '...'):
                self.pos += 1
            else:
                return
        return

    def _peek(self):
        self._skip()
        if self.pos >= len(self.lines):
            return None
        return self.lines[self.pos]

    def parse(self):
        line = self._peek()
        if line is None:
            return None
        return self.node(_indent(line))

    def node(self, ind):
        line = self._peek()
        s = line.strip()
        if s == '-' or s.startswith('- '):
            return self.seq(_indent(line))
        if _KEY_RE.match(_strip_comment(s)):
            return self.map(_indent(line))
        self.pos += 1
        return _scalar(_strip_comment(s))

    def map(self, ind):
        result = {}
        while True:
            line = self._peek()
            if line is None or _indent(line) != ind:
                break
            s = _strip_comment(line.strip())
            if s.startswith('- ') or s == '-':
                break
            m = _KEY_RE.match(s)
            if not m:
                break
            key = _scalar(m.group(1))
            val = m.group(2)
            self.pos += 1
            result[key] = self.value(val, ind)
        return result

    def value(self, val, ind):
        val = (val or '').strip()
        if val and val[0] in '>|':
            return self.block(val, ind)
        if val == '':
            nxt = self._peek()
            if nxt is None:
                return None
            ni = _indent(nxt)
            ns = nxt.strip()
            if ni > ind or (ni == ind and (ns.startswith('- ') or ns == '-')):
                return self.node(ni)
            return None
        # plain/quoted scalar with possible continuation lines
        parts = [val]
        while self.pos < len(self.lines):
            raw = self.lines[self.pos]
            if raw.strip() == '' or _indent(raw) <= ind or raw.strip().startswith('#'):
                break
            parts.append(raw.strip())
            self.pos += 1
        return _scalar(_strip_comment(' '.join(parts)) if len(parts) > 1 else _strip_comment(val))

    def block(self, header, ind):
        style = header[0]
        chomp = '-' if '-' in header[1:] else ('+' if '+' in header[1:] else '')
        raw = []
        block_ind = None
        while self.pos < len(self.lines):
            line = self.lines[self.pos]
            if line.strip() == '':
                raw.append('')
                self.pos += 1
                continue
            li = _indent(line)
            if li <= ind:
                break
            if block_ind is None:
                block_ind = li
            raw.append(line[block_ind:] if li >= block_ind else line.strip())
            self.pos += 1
        while raw and raw[-1] == '':
            raw.pop()
        if style == '|':
            text = '\n'.join(raw)
        else:
            text, para = [], []
            for l in raw:
                if l == '':
                    text.append(' '.join(para))
                    para = []
                else:
                    para.append(l.strip())
            text.append(' '.join(para))
            # blank line = newline between paragraphs
            text = '\n'.join(text)
        if chomp != '-':
            text += '\n'
        return text

    def seq(self, ind):
        items = []
        while True:
            line = self._peek()
            if line is None or _indent(line) != ind:
                break
            s = line.strip()
            if not (s == '-' or s.startswith('- ')):
                break
            rest = line[ind + 1:]
            extra = len(rest) - len(rest.lstrip(' '))
            rest = rest.strip()
            if rest == '':
                self.pos += 1
                nxt = self._peek()
                if nxt is not None and _indent(nxt) > ind:
                    items.append(self.node(_indent(nxt)))
                else:
                    items.append(None)
                continue
            if _KEY_RE.match(_strip_comment(rest)) and rest[0] not in '"\'[':
                self.lines[self.pos] = ' ' * (ind + 1 + extra) + rest
                items.append(self.map(ind + 1 + extra))
            else:
                self.pos += 1
                items.append(_scalar(_strip_comment(rest)))
        return items


def parse_lite(text):
    return _Parser(text).parse()
