#!/usr/bin/env python3
"""Self-test of manual.py (the offline manual as tools). Reads the docs folder only; it takes about 3 s.

    test_manual.py       # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import manual as M

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-72s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:80]))


def raises(fn, *a, **k):
    try:
        fn(*a, **k)
    except M.ManualError as exc:
        return str(exc)
    return None


def main():
    # the instrument first: does the docs folder hold what the manifest says?
    if not os.path.isdir(M.DOCS):
        print("SKIPPED: docs folder %s not found (set CRYENGINE_DOCS to the offline manual folder); no checks were run" % M.DOCS)
        return 0
    man = M._manifest(M.DOCS)
    on_disk = sum(1 for _r, _d, fs in os.walk(M.DOCS) for f in fs if f.endswith(".md") and f != "README.md")
    check("every page in the manifest has its file (%d pages, %d .md files on disk)" % (len(man), on_disk),
          all(os.path.isfile(os.path.join(M.DOCS, p["file"].replace("\\", os.sep))) for p in man.values()), "")

    hits = M.search("river tool", 3)
    check("search finds the River Tool page first (known answer, id 36867737)", hits and hits[0]["page_id"] == "36867737", [h["title"] for h in hits])
    check("a hit carries section, breadcrumb, url and a snippet that mentions the query", all(hits[0][k] for k in ("section", "breadcrumb", "url", "snippet")) and "river" in hits[0]["snippet"].lower(), hits[0]["section"])
    check("control: a nonsense query finds nothing", M.search("qzxvbnmq wlkjhg", 3) == [], "")
    check("a section filter only returns that section", all(h["section"] == "animation" for h in M.search("blend", 8, "animation")), "")
    check("a section that does not exist is refused, naming the real ones", "animation" in (raises(M.search, "x", 3, "nosuch") or ""), "")
    check("an empty query is refused", raises(M.search, "  ") is not None, "")

    raw = open(os.path.join(M.DOCS, M._find_file(M.DOCS, "36867737")["file"].replace(chr(92), os.sep)), encoding="utf-8").read()
    page = M.read_page("36867737", max_chars=60000)["text"]
    check("the site footer (social links, site map, cookie text) is not part of a page: raw file %d chars, page %d" % (len(raw), len(page)),
          "twitter.com/cryengine" in raw and "Stay up to date" in raw and "twitter.com/cryengine" not in page and "Stay up to date" not in page and len(raw) - len(page) > 2000, len(raw) - len(page))
    check("the footer is not in the search index either (no chunk holds the site map; one release note mentions Twitter in its own text)",
          not any("Stay up to date" in c["text"] for c in M._chunks(M.DOCS)) and sum("twitter.com/cryengine" in c["text"] for c in M._chunks(M.DOCS)) <= 1, "")
    by_id, by_title = M.read_page("36867737"), M.read_page("River Tool")
    check("a page reads the same by id and by title", by_id["text"] == by_title["text"] and by_id["title"] == "River Tool", by_id["page_id"])
    # paging must lose nothing: stitching 500-char parts gives the whole page
    parts, off = [], 0
    while off is not None:
        p = M.read_page("36867737", offset=off, max_chars=500)
        parts.append(p["text"])
        off = p["next_offset"]
    check("following next_offset in 500-char parts rebuilds the whole page (%d parts)" % len(parts), "".join(parts) == by_id["text"] and len(parts) > 1, len("".join(parts)))
    h = by_id["headings"][2]
    sl = M.read_page("36867737", heading=h)
    check("a heading returns only that part (shorter, starts at the heading)", 0 < len(sl["text"]) < len(by_id["text"]) and sl["text"].lstrip("# ").startswith(h), h)
    check("a heading that is not on the page is refused with the real headings", h in (raises(M.read_page, "36867737", heading="no such heading") or ""), "")
    amb = raises(M.read_page, "Water")
    check("an ambiguous title is refused with ids to choose from", amb and "36869912" in amb, (amb or "")[:60])
    check("an unknown page is refused", "no page" in (raises(M.read_page, "zzzqq") or ""), "")

    t = M.toc()
    check("the section page counts add up to the manifest (%d)" % t["pages"], sum(s["pages"] for s in t["sections"]) == t["pages"] == len(man) >= 1000, t["pages"])        # the manual is ~1700 pages; not pinned to an exact count
    folder = "editor-tools"
    disk = len([f for f in os.listdir(os.path.join(M.DOCS, folder)) if f.endswith(".md")])
    ts = M.toc(folder)
    check("toc(section) lists as many pages as the folder holds (%d)" % disk, ts["count"] == disk, ts["count"])
    check("toc(query) filters by title or breadcrumb", 0 < M.toc(query="particle")["count"] < len(man), M.toc(query="particle")["count"])

    ids = M.identifiers("Set r_DisplayInfo 1 and e_Clouds 0, then call ICVar::Set on the CCryAction instance (e_Clouds again). CRYENGINE ERROR")
    check("identifiers finds CVars and classes, most mentioned first, ignoring shouting words", ids["cvars"][0] == "e_Clouds" and "r_DisplayInfo" in ids["cvars"] and "ICVar" in ids["classes"] and "CRYENGINE" not in ids["classes"] and "ERROR" not in ids["classes"], ids)

    bad = results.count(False)
    print("manual test: %s (%d checks)" % ("PASS" if not bad else "%d FAILED" % bad, len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
