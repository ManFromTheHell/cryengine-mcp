#!/usr/bin/env python3
"""Search the offline copy of the CRYENGINE V manual.

Deliberately SEPARATE from rag/rag_query.py. That corpus is 163 hand-written entries,
each one something measured in this project; the manual is 1725 pages of somebody else's
prose. Mixing them would bury the measurements under the documentation and make it
impossible to tell "we tested this" from "Crytek says this". Two indexes, two commands,
same query language.

    python catalogs/docs_search.py --build                     (re)build the index
    python catalogs/docs_search.py "occlusion mesh"            search
    python catalogs/docs_search.py "flow graph" --section editor-tools --top 8
    python catalogs/docs_search.py --page 25534433             print one page
    python catalogs/docs_search.py --sections                  list the sections

Pages are split into chunks at their headings, so a hit points at the part of a long page
that actually matched. Standard library only.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import argparse
import json
import math
import os
import re
import sys
from collections import Counter

HERE = _paths.ROOT
# Folder holding the saved CRYENGINE manual as markdown: env CRYENGINE_DOCS, else <this folder>/cryengine_manual (not shipped; save the manual there, see scrape_cryengine_docs.py).
DEFAULT_DOCS = os.environ.get("CRYENGINE_DOCS") or os.path.join(HERE, "cryengine_manual")
INDEX_NAME = "search_index.jsonl"

STOP = set("""a an the and or of to in on for with is are was were be been it its this that
these those as at by from if then than so such not no do does did how what when where which
you your i we our my me can could should would will shall may might must have has had using
use used it's dont don't there their they them he she his her""".split())

WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*|\d+")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
MAX_CHUNK = 2200
# every saved page ends with the site's own footer (social links, a site map, cookie text): ~2,500 characters of nothing about the engine
FOOTER = re.compile(r"\n[ \t]*\[https://twitter\.com/cryengine\]")


def strip_footer(text):
    m = FOOTER.search(text)
    return text[:m.start()].rstrip() + "\n" if m else text


def tokenize(text):
    out = []
    for m in WORD.finditer(text.lower()):
        t = m.group(0)
        if t in STOP or len(t) < 2:
            continue
        out.append(t)
        for part in re.split(r"[._]", t):
            if len(part) >= 3 and part != t and part not in STOP:
                out.append(part)
    return out


def split_page(text):
    """Split a page into (heading, body) chunks, breaking long ones on blank lines."""
    chunks, heading, buf = [], "", []

    def flush():
        body = "\n".join(buf).strip()
        if body:
            chunks.append((heading, body))

    for line in text.splitlines():
        m = HEADING.match(line)
        if m:
            flush()
            buf = []
            heading = m.group(2).strip()
        else:
            buf.append(line)
            if sum(len(b) for b in buf) > MAX_CHUNK and not line.strip():
                flush()
                buf = []
    flush()
    return chunks or [("", text.strip())]


def build(docs_dir):
    index_path = os.path.join(docs_dir, INDEX_NAME)
    if not os.path.isdir(docs_dir):
        raise SystemExit("no manual folder at %s: save the CRYENGINE manual there as markdown (scrape_cryengine_docs.py) or set CRYENGINE_DOCS" % docs_dir)
    written = pages = 0
    with open(index_path, "w", encoding="utf-8", newline="\n") as out:
        for root, _dirs, files in os.walk(docs_dir):
            for name in sorted(files):
                if not name.endswith(".md"):
                    continue
                path = os.path.join(root, name)
                with open(path, encoding="utf-8") as f:
                    text = strip_footer(f.read())

                title = ""
                url = ""
                breadcrumb = ""
                for line in text.splitlines()[:6]:
                    if line.startswith("# ") and not title:
                        title = line[2:].strip()
                    elif line.startswith("> source: "):
                        url = line[len("> source: "):].strip()
                    elif line.startswith("> ") and not breadcrumb:
                        breadcrumb = line[2:].strip()

                section = os.path.relpath(root, docs_dir).split(os.sep)[0]
                page_id = name.split("-")[0]
                pages += 1
                for n, (heading, body) in enumerate(split_page(text)):
                    if len(body) < 40:
                        continue
                    out.write(json.dumps({
                        "page_id": page_id,
                        "chunk": n,
                        "title": title or name,
                        "heading": heading,
                        "section": section,
                        "path": breadcrumb,
                        "url": url,
                        "file": os.path.relpath(path, docs_dir),
                        "text": body[:6000],
                    }, ensure_ascii=False) + "\n")
                    written += 1
    print("indexed %d chunks from %d pages -> %s" % (written, pages, index_path))
    return index_path


def load(index_path):
    chunks = []
    with open(index_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    for c in chunks:
        boost = tokenize(c["title"]) + tokenize(c["heading"])
        c["_tf"] = Counter(tokenize(c["text"]) + boost * 3)
        c["_len"] = sum(c["_tf"].values())
    return chunks


def search(chunks, query, top=5, section=None):
    pool = [c for c in chunks if not section or c["section"] == section]
    if not pool:
        return []
    qt = tokenize(query)
    if not qt:
        return []
    N = len(pool)
    avg = sum(c["_len"] for c in pool) / N
    df = Counter()
    for c in pool:
        for t in set(c["_tf"]):
            df[t] += 1
    k1, b = 1.5, 0.6
    ql = query.lower()
    scored = []
    for c in pool:
        s = 0.0
        for t in qt:
            tf = c["_tf"].get(t, 0)
            if not tf:
                continue
            idf = math.log(1 + (N - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * c["_len"] / avg))
        if len(qt) > 1 and ql in (c["title"] + " " + c["heading"] + " " + c["text"]).lower():
            s *= 1.6
        if s > 0:
            scored.append((s, c))
    scored.sort(key=lambda p: -p[0])

    # one hit per page: a long page should not fill the whole result list
    seen, out = set(), []
    for s, c in scored:
        if c["page_id"] in seen:
            continue
        seen.add(c["page_id"])
        out.append((s, c))
        if len(out) >= top:
            break
    return out


def snippet(text, query, width=260):
    words = [w for w in tokenize(query) if len(w) > 2]
    low = text.lower()
    best, pos = 0, 0
    for i in range(0, max(1, len(low) - width), 60):
        window = low[i:i + width]
        hits = sum(window.count(w) for w in words)
        if hits > best:
            best, pos = hits, i
    return re.sub(r"\s+", " ", text[pos:pos + width]).strip()


def main():
    ap = argparse.ArgumentParser(description="Search the offline CRYENGINE manual.")
    ap.add_argument("query", nargs="*")
    ap.add_argument("--docs", default=DEFAULT_DOCS)
    ap.add_argument("--build", action="store_true", help="(re)build the index")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--section", help="restrict to one section folder")
    ap.add_argument("--sections", action="store_true", help="list sections and page counts")
    ap.add_argument("--page", help="print a page by its numeric id")
    ap.add_argument("--full", action="store_true", help="print whole chunks, not snippets")
    a = ap.parse_args()

    index_path = os.path.join(a.docs, INDEX_NAME)
    if a.build or not os.path.exists(index_path):
        build(a.docs)
        if a.build and not a.query:
            return

    if a.sections:
        counts = Counter()
        for root, _dirs, files in os.walk(a.docs):
            section = os.path.relpath(root, a.docs).split(os.sep)[0]
            counts[section] += sum(1 for f in files if f.endswith(".md"))
        for k, v in sorted(counts.items()):
            if v:
                print("%-34s %4d" % (k, v))
        return

    if a.page:
        for root, _dirs, files in os.walk(a.docs):
            for f in files:
                if f.startswith(a.page + "-") and f.endswith(".md"):
                    with open(os.path.join(root, f), encoding="utf-8") as fh:
                        print(fh.read())
                    return
        sys.exit("no page with id " + a.page)

    if not a.query:
        ap.print_help()
        return

    chunks = load(index_path)
    query = " ".join(a.query)
    hits = search(chunks, query, top=a.top, section=a.section)
    if not hits:
        print("nothing found for %r" % query)
        return
    for score, c in hits:
        where = c["heading"] and (" > " + c["heading"]) or ""
        print("\n[%5.1f] %s%s" % (score, c["title"], where))
        print("        %s  (%s)" % (c["file"], c["url"]))
        print("        " + (c["text"][:1500] if a.full else snippet(c["text"], query)))


if __name__ == "__main__":
    main()
