#!/usr/bin/env python3
"""Fill the Hebrew lemma (Hebrew script) into a converted StrongsHebrew module.

The SWORD StrongsHebrew module (public domain, Strong 1890) carries only the
transliteration and pronunciation, so `dictionary_entry.word` ends up equal to
the entry key ("00430") and the app can show no Hebrew script (B-179, B-190).
The Open Scriptures Hebrew Lexicon's HebrewStrong.xml has the lemma for every
Strong's number H1..H8674. This script sets `word` to that lemma and records
the attribution CC BY 4.0 requires in module_info.

    add-hebrew-lemmas.py --db dictionary_strongshebrew.db --xml HebrewStrong.xml

Idempotent. Only `word` and the provenance fields change: the definition text
(which the app parses for transliteration/pronunciation) is left untouched.
The XML is treated as data and parsed with the standard library only.

Source:  https://github.com/openscriptures/HebrewLexicon (HebrewStrong.xml)
Licence: Creative Commons Attribution 4.0 International; "For attribution
         purposes, credit the Open Scriptures Hebrew Bible Project." The text of
         Strong's dictionary itself remains in the public domain.
"""
import argparse
import json
import sqlite3
import sys
import xml.etree.ElementTree as ET

NS = "{http://openscriptures.github.com/morphhb/namespace}"
NOTICE = ("Hebrew lemmas (the word column): Open Scriptures Hebrew Bible Project, "
          "Hebrew Lexicon HebrewStrong.xml, CC BY 4.0 "
          "(https://creativecommons.org/licenses/by/4.0/), "
          "https://github.com/openscriptures/HebrewLexicon. "
          "Strong's dictionary text: public domain.")


def load_lemmas(path):
    out = {}
    for e in ET.parse(path).getroot().findall(NS + "entry"):
        eid, w = e.get("id", ""), e.find(NS + "w")
        if eid[:1] == "H" and eid[1:].isdigit() and w is not None and (w.text or "").strip():
            out[int(eid[1:])] = w.text.strip()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--xml", required=True)
    a = ap.parse_args()
    lemmas = load_lemmas(a.xml)
    if len(lemmas) < 8000:
        sys.exit(f"only {len(lemmas)} lemmas in {a.xml}; refusing")
    db = sqlite3.connect(a.db)
    done = 0
    with db:
        for eid, key in db.execute("select entry_id, entry_key from dictionary_entry").fetchall():
            k = key.strip().lstrip("Hh")
            if k.isdigit() and int(k) in lemmas:
                db.execute("update dictionary_entry set word=? where entry_id=?", (lemmas[int(k)], eid))
                done += 1
        row = db.execute("select copyright, metadata from module_info").fetchone()
        copyright_, meta = row
        if NOTICE not in (copyright_ or ""):
            copyright_ = ((copyright_ + " | ") if copyright_ else "") + NOTICE
        m = json.loads(meta) if meta else {}
        m["lemma_source"] = {"name": "Open Scriptures Hebrew Lexicon (HebrewStrong.xml)",
                             "license": "CC-BY-4.0",
                             "url": "https://github.com/openscriptures/HebrewLexicon"}
        db.execute("update module_info set copyright=?, metadata=?", (copyright_, json.dumps(m, ensure_ascii=False)))
    print(f"lemmas loaded: {len(lemmas)}; entries updated: {done}")


if __name__ == "__main__":
    main()
