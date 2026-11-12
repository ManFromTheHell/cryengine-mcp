#!/usr/bin/env python3
"""Build a link index of the CRYENGINE V manual from a saved copy of any manual page.

Every page of the manual embeds the WHOLE navigation tree (1725 entries, which is why each
page is about 1 MB), so one saved page is enough to enumerate the entire manual -- no
crawling needed to discover it.

    python catalogs/docs_link_index.py "<saved page>.html" [-o cryengine_docs_links.json]

Output: {"generated", "source", "count", "pages": [{id, title, url, depth, path}]}
"path" is the breadcrumb of section titles above the page, taken from the tree nesting.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import argparse
import datetime
import json
import os
import re
from html.parser import HTMLParser

DOC_URL = re.compile(r"^https://www\.cryengine\.com/docs/static/engines/cryengine-5/categories/(\d+)(?:/pages/(\d+))?$")


class NavTreeParser(HTMLParser):
    """Walks the nav tree, keeping a stack of the section titles above the current link.

    The tree is built from nested DIVs, not lists:
        <div class="nav-tree__item">
          <div class="nav-tree__item--title"> <a href=...>Title</a> </div>
          <div class="nav-tree__item--list">  ... children ... </div>
        </div>
    so the section stack is pushed when a "--list" div opens and popped when the div
    nesting returns to the level it opened at.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pages = []
        self.seen = set()
        self.section_stack = []      # titles of the enclosing lists
        self.open_at = []            # div nesting depth each of those lists opened at
        self.div_depth = 0
        self.pending = None          # link being read
        self.last_title = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        cls = attrs.get("class", "")

        if tag == "div":
            if "nav-tree__item--list" in cls:
                # everything inside belongs under the link read just before it
                self.section_stack.append(self.last_title or "")
                self.open_at.append(self.div_depth)
            self.div_depth += 1

        elif tag == "a":
            href = attrs.get("href", "")
            match = DOC_URL.match(href)
            if match:
                self.pending = {"url": href, "category": match.group(1), "id": match.group(2), "text": []}

    def handle_endtag(self, tag):
        if tag == "div":
            self.div_depth -= 1
            while self.open_at and self.open_at[-1] >= self.div_depth:
                self.open_at.pop()
                self.section_stack.pop()

        elif tag == "a" and self.pending is not None:
            title = re.sub(r"\s+", " ", "".join(self.pending["text"])).strip()
            url = self.pending["url"]
            self.last_title = title or self.last_title
            if url not in self.seen:
                self.seen.add(url)
                self.pages.append({
                    "id": self.pending["id"] or self.pending["category"],
                    "title": title,
                    "url": url,
                    "depth": len(self.section_stack),
                    "path": [p for p in self.section_stack if p],
                    "is_category": self.pending["id"] is None,
                })
            self.pending = None

    def handle_data(self, data):
        if self.pending is not None:
            self.pending["text"].append(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="a saved .html page of the manual")
    ap.add_argument("-o", "--out", default=os.path.join(_paths.ROOT, "cryengine_docs_links.json"))
    args = ap.parse_args()

    with open(args.source, encoding="utf-8", errors="replace") as f:
        html = f.read()

    parser = NavTreeParser()
    parser.feed(html)

    index = {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "source": os.path.basename(args.source),
        "count": len(parser.pages),
        "pages": parser.pages,
    }
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(index, f, indent=1, ensure_ascii=False)

    depths = {}
    for p in parser.pages:
        depths[p["depth"]] = depths.get(p["depth"], 0) + 1
    print("%d pages -> %s" % (len(parser.pages), args.out))
    print("depth histogram:", dict(sorted(depths.items())))


if __name__ == "__main__":
    main()
