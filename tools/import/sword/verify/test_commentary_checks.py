"""Self-test for commentary_checks.py. Run: python3 -I test_commentary_checks.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from commentary_checks import commentary_flags, scan_commentary

CASES = [
    # (html, expected flags)
    ('<p>Ver. 1,2. <strong>Hezekiah began to reign</strong>,.... Of these two verses,</p>\n<p>:-,</p>\n<p>:-.</p>', ['stub']),
    ('<p>:-</p>', ['stub', 'whole']),
    ('<p>:-.</p>', ['stub', 'whole']),
    ('<p>Ver. 27. <em>See Gill on "2Ch 28:26"</em>}</p>', []),
    ('<p>Of the name of this tree, <em>see Gill on "Ge 2:9"</em></p>', []),
    ('<p>end. 2 Chronicles2Ch 272 Chronicles2Ch2 Chronicles2Ch 29</p>', ['glued']),
    ('<p>RuthRut 3RuthRut1 Samuel1Sa 1</p>', ['glued']),
    ('<p>as Ps 69:4 and Zec 8:16 in prose :- genuine colon-dash mid text</p>', []),
    ('<p>as</p><p>Ps 69:4</p>', []),
    ('<p>asPs 69:4 glued</p>', ['glued']),
    ('<p>Ends well.\x1a</p>', ['junk_end']),
    ('<p>Ends well.\u00b9</p>', []),
]

failed = 0
for html, want in CASES:
    got = commentary_flags(html)
    if got != want:
        failed += 1
        print('FAIL', repr(html), 'got', got, 'want', want)
r = scan_commentary([(1, CASES[0][0]), (2, CASES[3][0]), (3, CASES[1][0])])
assert r['counts'] == {'stub': 2, 'whole': 1, 'glued': 0, 'junk_end': 0}, r
if failed:
    sys.exit(1)
print('commentary_checks: %d cases ok' % len(CASES))
