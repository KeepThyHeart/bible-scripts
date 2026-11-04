#!/usr/bin/env python3
"""Verify the interlinear article/noun split (B-059) in a converted Bible module.

Usage: verify-interlinear-tags.py NEW.db [--old OLD.db] [--verse 43001001 ...]

Checks on NEW.db:
  1. no row still has a multi-code morphology that starts with an article
     ("T-NSM N-NSM"), i.e. the article and noun are no longer fused in one row;
  2. (informational) glossed article rows that share a range with a gloss-less
     content row: legitimate where the article IS the English word (Matt 13:23
     "some" = ὁ + μέν), so it is reported, not failed;
  3. every gloss still equals the verse words it spans (gloss invariant).
With --old (a module built before the fix) also checks that every Strong's tag
the old rows listed (primary or metadata.strongs) now exists as the primary
strongs_number of a row over the same verse and word range: none lost. How the
split is represented (gloss-less article row, or both rows glossed) is not
checked, so the script is valid for any such design.
Exit status 1 on any failure. Test data is KJV, kept literal: only the row
structure changes, never the English text or the tags.
"""
import argparse
import json
import sqlite3
import sys


def tags_of(strongs, meta):
    if meta:
        try:
            s = json.loads(meta).get("strongs")
            if s:
                return s.split()
        except ValueError:
            pass
    return [strongs] if strongs else []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("new")
    ap.add_argument("--old")
    ap.add_argument("--verse", type=int, nargs="*", default=[])
    a = ap.parse_args()
    new = sqlite3.connect(a.new)
    bad = 0

    n = new.execute("select count(*) from interlinear_word where morphology like 'T-% %'").fetchone()[0]
    print(f"rows with article-first multi-code morphology: {n}")
    bad += n > 0

    n = new.execute("""select count(*) from interlinear_word a where a.strongs_number='G3588' and a.gloss is not null
        and exists (select 1 from interlinear_word b where b.verse_id=a.verse_id and b.interlinear_id<>a.interlinear_id
          and b.word_position_start=a.word_position_start and b.word_position_end=a.word_position_end
          and b.strongs_number<>'G3588')""").fetchone()[0]
    print(f"glossed article over a gloss-less content row (informational): {n}")

    bad_gloss = 0
    rows = new.execute("""select i.verse_id, i.word_position_start, i.word_position_end, i.gloss, v.text
        from interlinear_word i join bible_verse v using(verse_id) where i.gloss is not null""")
    for vid, s, e, g, text in rows:
        if " ".join(text.split()[s:e + 1]) != g:
            bad_gloss += 1
    print(f"gloss invariant violations: {bad_gloss}")
    bad += bad_gloss > 0

    if a.old:
        old = sqlite3.connect(a.old)
        have = set(new.execute(
            "select verse_id, word_position_start, word_position_end, strongs_number from interlinear_word"))
        lost = 0
        for vid, st, en, sn, meta in old.execute(
                "select verse_id, word_position_start, word_position_end, strongs_number, metadata from interlinear_word"):
            for tag in tags_of(sn, meta):
                if (vid, st, en, tag) not in have:
                    lost += 1
        print(f"old Strong's tags with no primary row over the same range in new: {lost}")
        bad += lost > 0

    for vid in a.verse:
        print(f"-- {vid}")
        for r in new.execute("select word_position_start,word_position_end,original_word,strongs_number,morphology,gloss from interlinear_word where verse_id=? order by interlinear_id", (vid,)):
            print("  ", r)
    print("FAIL" if bad else "OK")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
