#!/usr/bin/env python3
"""Download the CRYENGINE V manual for offline reference.

The manual's pages are server-rendered, so a plain HTTP GET is enough -- no browser, no
login. (The in-page React viewer renders nothing for an anonymous visitor, which is what
made the site look unreadable; the HTML behind it is complete.)

Every page also embeds the whole 1725-entry navigation tree, so a page is about 1 MB of
which only a few kB is the article. This keeps the article and throws the rest away:

    <div class="typography--user-content"> ... the page ... </div>

Usage:
    python catalogs/docs_link_index.py "<a saved manual page>.html"      # build the link index
    python catalogs/scrape_cryengine_docs.py                             # download everything
    python catalogs/scrape_cryengine_docs.py --limit 5 --delay 0.5       # try a handful first
    python catalogs/scrape_cryengine_docs.py --only ids.txt              # refetch just these pages
    python catalogs/scrape_cryengine_docs.py --keep-html                 # also save the raw pages

Resumable: a page whose .md already exists is skipped, so it can be stopped and restarted.
Output (under the folder given by --out; default: env CRYENGINE_DOCS, else <repository>/cryengine_manual, the folder docs_search.py reads):
    <section>/<id>-<title>.md   one file per page, with a small YAML-ish header
    manifest.json               every page, its file, its status and the bytes kept
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import argparse
import datetime
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = _paths.ROOT
DEFAULT_INDEX = os.path.join(HERE, "cryengine_docs_links.json")
DEFAULT_OUT = os.environ.get("CRYENGINE_DOCS") or os.path.join(HERE, "cryengine_manual")
USER_AGENT = "Mozilla/5.0 (offline documentation copy for local reference)"

CONTENT_RE = re.compile(r'<div class="typography--user-content"[^>]*>(.*?)</div>\s*</div>\s*</section>', re.S)
CONTENT_FALLBACK_RE = re.compile(r'<div class="typography--user-content"[^>]*>(.*)', re.S)
TITLE_RE = re.compile(r'<h1[^>]*class="text--48[^"]*"[^>]*>(.*?)</h1>', re.S)


def slug(text, limit=60):
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:limit].strip("-") or "untitled"


def strip_tags(fragment):
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def strip_tags_raw(fragment):
    """Strip tags but leave the entities escaped.

    Unescaping early is what silently deleted every XML snippet in the first pass:
    `&lt;AgentType&gt;` turned into `<AgentType>` inside a handler, and the final
    tag-strip at the end of to_markdown then ate it as if it were markup. Nothing is
    unescaped before that last step now, and code blocks sit in a vault where none of
    the intermediate regexes reach them at all.
    """
    return re.sub(r"<[^>]+>", "", fragment).strip()


def to_markdown(fragment):
    """A small HTML to markdown pass -- enough to read and to search, not a converter."""
    s = fragment
    vault = []

    def stash(text):
        vault.append(text)
        return "@@CE_CODE_%d@@" % (len(vault) - 1)

    # things whose content is not text
    s = re.sub(r"(?s)<(script|style|noscript)\b.*?</\1>", "", s)

    # code first, and parked out of reach of every later regex
    def code_block(m):
        code = html.unescape(strip_tags_raw(m.group(1)))
        return stash("\n```\n" + code.strip("\n") + "\n```\n")
    s = re.sub(r"(?s)<pre[^>]*>(.*?)</pre>", code_block, s)
    s = re.sub(r"(?s)<code[^>]*>(.*?)</code>",
               lambda m: stash("`" + html.unescape(strip_tags_raw(m.group(1))) + "`"), s)

    # images and links keep their targets -- they point back at the site
    s = re.sub(r'<img[^>]*\balt="([^"]*)"[^>]*\bsrc="([^"]+)"[^>]*>', r"![\1](\2)", s)
    s = re.sub(r'<img[^>]*\bsrc="([^"]+)"[^>]*>', r"![](\1)", s)
    s = re.sub(r'(?s)<a[^>]*\bhref="([^"]+)"[^>]*>(.*?)</a>',
               lambda m: "[%s](%s)" % (strip_tags_raw(m.group(2)) or m.group(1), m.group(1)), s)

    for level in range(1, 7):
        s = re.sub(r"(?s)<h%d[^>]*>(.*?)</h%d>" % (level, level),
                   lambda m, l=level: "\n\n" + "#" * l + " " + strip_tags_raw(m.group(1)) + "\n", s)

    s = re.sub(r"(?s)<(strong|b)[^>]*>(.*?)</\1>", lambda m: "**" + strip_tags_raw(m.group(2)) + "**", s)
    s = re.sub(r"(?s)<(em|i)[^>]*>(.*?)</\1>", lambda m: "*" + strip_tags_raw(m.group(2)) + "*", s)

    s = re.sub(r"(?s)<li[^>]*>(.*?)</li>", lambda m: "\n- " + strip_tags_raw(m.group(1)), s)
    s = re.sub(r"(?s)<t[dh][^>]*>(.*?)</t[dh]>", lambda m: " | " + strip_tags_raw(m.group(1)), s)
    s = re.sub(r"</tr>", "\n", s)
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"</p>|</div>|</ul>|</ol>|</table>", "\n\n", s)

    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"@@CE_CODE_(\d+)@@", lambda m: vault[int(m.group(1))], s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def fetch(url, timeout=45, attempts=3):
    last = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise last


def extract(page_html):
    match = CONTENT_RE.search(page_html) or CONTENT_FALLBACK_RE.search(page_html)
    body = match.group(1) if match else ""
    title = TITLE_RE.search(page_html)
    return (strip_tags(title.group(1)) if title else ""), to_markdown(body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default=DEFAULT_INDEX)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--delay", type=float, default=0.8, help="seconds between requests")
    ap.add_argument("--limit", type=int, default=0, help="stop after N downloads (0 = all)")
    ap.add_argument("--keep-html", action="store_true", help="also save the raw page (about 1 MB each)")
    ap.add_argument("--refetch", action="store_true", help="download pages that are already saved")
    ap.add_argument("--only", help="file of page ids, one per line: fetch only those (implies --refetch)")
    args = ap.parse_args()

    with open(args.index, encoding="utf-8") as f:
        index = json.load(f)
    pages = index["pages"]

    if args.only:
        with open(args.only, encoding="utf-8") as f:
            wanted = {line.strip() for line in f if line.strip()}
        pages = [p for p in pages if str(p["id"]) in wanted]
        args.refetch = True
        print("only: %d of %d pages" % (len(pages), len(index["pages"])))

    manifest_path = os.path.join(args.out, "manifest.json")
    manifest = {}
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            manifest = {entry["url"]: entry for entry in json.load(f).get("pages", [])}

    os.makedirs(args.out, exist_ok=True)
    downloaded = skipped = failed = 0
    started = time.time()

    for i, page in enumerate(pages, 1):
        section = slug(page["path"][1] if len(page["path"]) > 1 else (page["path"][0] if page["path"] else "manual"), 40)
        folder = os.path.join(args.out, section)
        name = "%s-%s.md" % (page["id"], slug(page["title"]))
        path = os.path.join(folder, name)

        if os.path.exists(path) and not args.refetch:
            skipped += 1
            continue

        try:
            page_html = fetch(page["url"])
            title, markdown = extract(page_html)
        except Exception as exc:                                   # noqa: BLE001 - reported, not raised
            failed += 1
            manifest[page["url"]] = dict(page, file=None, status="error: %s" % exc, chars=0)
            print("FAIL %s %s (%s)" % (page["id"], page["title"][:50], exc))
            continue

        os.makedirs(folder, exist_ok=True)
        header = [
            "# %s" % (title or page["title"]),
            "",
            "> %s" % " / ".join(page["path"]) if page["path"] else "",
            "> source: %s" % page["url"],
            "",
        ]
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(h for h in header if h is not None) + "\n" + markdown + "\n")

        if args.keep_html:
            raw_folder = os.path.join(args.out, "_html", section)
            os.makedirs(raw_folder, exist_ok=True)
            with open(os.path.join(raw_folder, "%s.html" % page["id"]), "w", encoding="utf-8") as f:
                f.write(page_html)

        manifest[page["url"]] = dict(page, file=os.path.relpath(path, args.out), status="ok", chars=len(markdown))
        downloaded += 1

        if downloaded % 25 == 0:
            elapsed = time.time() - started
            print("%4d/%d  %5.1f min  last: %s (%d chars)" % (i, len(pages), elapsed / 60, page["title"][:45], len(markdown)))
            with open(manifest_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump({"generated": datetime.datetime.now().isoformat(timespec="seconds"),
                           "pages": list(manifest.values())}, f, indent=1, ensure_ascii=False)

        if args.limit and downloaded >= args.limit:
            break
        time.sleep(args.delay)

    with open(manifest_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"generated": datetime.datetime.now().isoformat(timespec="seconds"),
                   "pages": list(manifest.values())}, f, indent=1, ensure_ascii=False)

    print("done: %d downloaded, %d already there, %d failed, %.1f min" %
          (downloaded, skipped, failed, (time.time() - started) / 60))


if __name__ == "__main__":
    sys.exit(main())
