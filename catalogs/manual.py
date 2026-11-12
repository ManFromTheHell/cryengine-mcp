#!/usr/bin/env python3
"""The offline CRYENGINE V manual as functions an MCP tool can call (ENGINE_LEARNING_PLAN L5).

The manual is Crytek's own documentation: 1,725 pages, saved as markdown with their breadcrumb
and source URL (README.md in the docs folder says how it was fetched and which converter bug was
fixed). docs_search.py is the command-line front end and owns the index format; this module adds
what a tool needs on top of it:

    search(query, top, section)        BM25 over chunks, one hit per page, with a snippet
    read_page(page, heading, ...)      one page by id or title, paged, with its heading list
    toc(section, query)                the page tree from manifest.json, or the section counts
    identifiers(text)                  CVars, console commands and C++ names mentioned on a page

Nothing here writes, downloads or runs anything: it only reads the docs folder. The folder is
found through $CRYENGINE_DOCS, else the default below (the folder is not part of the project
because it is Crytek's content, "not for redistribution").
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import re
from collections import Counter

import docs_search

DOCS = os.environ.get("CRYENGINE_DOCS", docs_search.DEFAULT_DOCS)
PAGE_CHARS = 12000          # one read_page answer; the rest is reached with offset

_CHUNKS = {}                # docs folder -> loaded chunks (about 1 s to build, then free)
_MANIFEST = {}              # docs folder -> {id: entry}


class ManualError(Exception):
    """A request that cannot be answered; the message says what to do instead."""


def _index_path(docs):
    return os.path.join(docs, docs_search.INDEX_NAME)


def _chunks(docs):
    if docs not in _CHUNKS:
        path = _index_path(docs)
        if not os.path.exists(path):
            raise ManualError("no search index at %s: the offline manual is not part of this project; save the CRYENGINE V manual as markdown, set CRYENGINE_DOCS to its folder "
                              "and run  python catalogs/docs_search.py --build" % path)
        _CHUNKS[docs] = docs_search.load(path)
    return _CHUNKS[docs]


def _manifest(docs):
    if docs not in _MANIFEST:
        path = os.path.join(docs, "manifest.json")
        if not os.path.exists(path):
            raise ManualError("no manifest.json in %s: is this the manual's folder? (set CRYENGINE_DOCS to the folder holding the saved manual)" % docs)
        with open(path, encoding="utf-8") as f:
            pages = json.load(f)["pages"]
        _MANIFEST[docs] = {p["id"]: p for p in pages if p.get("status") == "ok"}
    return _MANIFEST[docs]


def search(query, top=5, section=None, docs=DOCS):
    """Best pages for a query: [{page_id, title, heading, section, breadcrumb, url, score, snippet}]."""
    if not query or not query.strip():
        raise ManualError("give a query, e.g. 'river tool' or 'AnimationGraph transitions'")
    chunks = _chunks(docs)
    if section and not any(c["section"] == section for c in chunks):
        raise ManualError("no section %r: pick one of %s" % (section, ", ".join(sections(docs))))
    hits = docs_search.search(chunks, query, top=max(1, min(int(top), 25)), section=section or None)
    # a page titled exactly like the query comes first: "River Tool" must not rank behind "River Tool How-To"
    hits.sort(key=lambda sc: sc[1]["title"].lower() != query.strip().lower())
    return [{"page_id": c["page_id"], "title": c["title"], "heading": c["heading"], "section": c["section"],
             "breadcrumb": c["path"], "url": c["url"], "score": round(s, 1),
             "snippet": docs_search.snippet(c["text"], query)} for s, c in hits]


def sections(docs=DOCS):
    return sorted({c["section"] for c in _chunks(docs)})


def _find_file(docs, page):
    """Resolve a page given as a numeric id, a file name, or a title (exact, then unique substring)."""
    page = str(page).strip()
    man = _manifest(docs)
    if page in man:
        return man[page]
    if not page:
        raise ManualError("give a page id (from search_manual) or a title")
    low = page.lower()
    exact = [p for p in man.values() if p["title"].lower() == low]
    if len(exact) == 1:
        return exact[0]
    cands = exact or [p for p in man.values() if low in p["title"].lower()]
    if len(cands) == 1:
        return cands[0]
    if not cands:
        raise ManualError("no page %r: use search_manual to find its id" % page)
    listing = "; ".join("%s = %s (%s)" % (p["id"], p["title"], " / ".join(p["path"][-2:])) for p in cands[:12])
    raise ManualError("%d pages match %r, pass the id: %s%s" % (len(cands), page, listing,
                                                              " ..." if len(cands) > 12 else ""))


def read_page(page, heading=None, offset=0, max_chars=PAGE_CHARS, docs=DOCS):
    """One page: its text (from offset, at most max_chars), its headings, and where it sits in the manual.

    heading: only the part under the first heading containing this text (case-insensitive).
    """
    entry = _find_file(docs, page)
    path = os.path.join(docs, entry["file"].replace("\\", os.sep))
    with open(path, encoding="utf-8") as f:
        text = docs_search.strip_footer(f.read())             # the site's footer (about 2,500 characters of links) is not the page
    headings = [m.group(2).strip() for m in (docs_search.HEADING.match(l) for l in text.splitlines()) if m]
    body = text
    if heading:
        start, level = None, 0
        out = []
        for line in text.splitlines():
            m = docs_search.HEADING.match(line)
            if start is None:
                if m and heading.lower() in m.group(2).lower():
                    start, level = True, len(m.group(1))
                    out.append(line)
            else:
                if m and len(m.group(1)) <= level:
                    break
                out.append(line)
        if start is None:
            raise ManualError("no heading containing %r on %r: headings are %s" % (heading, entry["title"], headings))
        body = "\n".join(out)
    offset = max(0, int(offset))
    max_chars = max(500, min(int(max_chars), 60000))
    chunk = body[offset:offset + max_chars]
    end = offset + len(chunk)
    return {"page_id": entry["id"], "title": entry["title"], "breadcrumb": " / ".join(entry["path"]),
            "url": entry["url"], "headings": headings, "chars_total": len(body), "offset": offset,
            "text": chunk, "truncated": end < len(body), "next_offset": end if end < len(body) else None}


def toc(section=None, query=None, docs=DOCS):
    """No argument: the sections with their page counts. A section (or a breadcrumb word): its pages in manual order."""
    man = _manifest(docs)
    if not section and not query:
        counts = Counter()
        for p in man.values():
            counts[p["file"].split("\\")[0]] += 1
        return {"sections": [{"section": k, "pages": v} for k, v in sorted(counts.items())], "pages": len(man)}
    rows = []
    for p in man.values():
        folder = p["file"].split("\\")[0]
        if section and folder != section:
            continue
        if query and query.lower() not in (p["title"] + " " + " ".join(p["path"])).lower():
            continue
        rows.append({"page_id": p["id"], "title": p["title"], "depth": p["depth"], "under": " / ".join(p["path"][-2:]),
                     "chars": p.get("chars", 0)})
    if not rows and section:
        raise ManualError("no section %r: call manual_toc() for the list" % section)
    return {"count": len(rows), "pages": rows[:300], "note": "first 300 shown" if len(rows) > 300 else ""}


# ---- identifiers: what a page talks about that exists in the engine's source ------------------

_CVAR = re.compile(r"\b((?:e|r|sys|ai|g|s|p|ca|aux|cl|sv|mfx|fg|sw|um|dbg|a|ed|es|ac|c|lua|ag|ui|pl|net|t|gp|log|pfx|sw)_[A-Za-z0-9_]{2,})\b")
_CLASS = re.compile(r"\b([ICSE][A-Z][A-Za-z0-9]{2,})\b")


def identifiers(text):
    """CVar-looking and class-looking names in a page: {'cvars': [...], 'classes': [...]}, most mentioned first."""
    cv = Counter(m.group(1) for m in _CVAR.finditer(text))
    cl = Counter(m.group(1) for m in _CLASS.finditer(text) if not m.group(1).isupper())
    return {"cvars": [k for k, _ in cv.most_common(40)], "classes": [k for k, _ in cl.most_common(40)]}


if __name__ == "__main__":      # a quick look from the shell: manual.py <query>
    import sys
    for h in search(" ".join(sys.argv[1:]) or "river tool"):
        print("[%5.1f] %s > %s  (%s, id %s)" % (h["score"], h["title"], h["heading"], h["section"], h["page_id"]))
