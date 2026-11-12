#!/usr/bin/env python3
"""One command for the knowledge corpus.

    python rag/build_corpus.py --check      # validate + lint; exit 1 on any error. With CRYENGINE_CORPUS_MIRROR set it also compares the mirror copy
    python rag/build_corpus.py --sync       # re-normalise the corpus and write it atomically (and the mirror, if set; backup kept in rag/backups/)
    python rag/build_corpus.py --status     # regenerate the STATUS chunk (status-current) from the code and tests, linked from playbook-start-here
    python rag/build_corpus.py --readme     # regenerate rag/README.md from the corpus
    python rag/build_corpus.py --all        # --status, --readme, --check

CRYENGINE_CORPUS_MIRROR (optional, empty by default) names a second copy of the corpus file that is then kept byte-identical.
Chunks are added with corpus_lib.upsert(); never edit the .jsonl files by hand.
"""
import argparse
import collections
import datetime
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import corpus_lib as L  # noqa: E402
import rag_query as R  # noqa: E402

MCP_DIR = os.path.dirname(HERE)
STATUS_ID = "status-current"
START_ID = "playbook-start-here"
START_MARK = "CURRENT COUNTS AND WHAT HAS NEVER BEEN SEEN WORKING: " + STATUS_ID


def count_tools():
    """Tool registrations in server.py: the number of '@mcp.tool' decorators."""
    path = os.path.join(MCP_DIR, "server.py")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return sum(1 for line in f if line.startswith("@mcp.tool"))
    except OSError:
        return None


_CHECK_CALL = re.compile(r"^\s*(?:check|ok|expect|ensure|req)\(|^\s*assert\s|^\s*def test_", re.M)


def count_tests():
    """-> (files, static check call sites, top list). A STATIC count of check()/assert/def test_ lines: not the number of checks a run performs."""
    tests_dir = os.path.join(MCP_DIR, "tests")
    files = sorted(f for f in os.listdir(tests_dir) if re.match(r"test_.*\.py$", f)) if os.path.isdir(tests_dir) else []
    per = {}
    for f in files:
        try:
            with open(os.path.join(tests_dir, f), encoding="utf-8", errors="replace") as fh:
                per[f] = len(_CHECK_CALL.findall(fh.read()))
        except OSError:
            per[f] = 0
    return len(files), sum(per.values()), sorted(per.items(), key=lambda kv: -kv[1])[:6]


def build_status_chunk(rows, today=None):
    today = today or datetime.date.today().isoformat()
    tools = count_tools()
    nfiles, ncalls, top = count_tests()
    live = [r for r in rows if r.get("status") != "superseded" and r["id"] != STATUS_ID]
    by_status = collections.Counter(r.get("status", "unclassified") for r in rows if r["id"] != STATUS_ID)
    by_ver = collections.Counter(r.get("verified_in", "unclassified") for r in rows if r["id"] != STATUS_ID)
    none = sorted(r["id"] for r in live if r.get("verified_in") == "none")
    partial = sorted(r["id"] for r in live if r.get("verified_in") == "partial")
    sup = sorted((r["id"], r.get("superseded_by", "")) for r in rows if r.get("status") == "superseded")
    cats = collections.Counter(r["category"] for r in live)
    lines = [
        "GENERATED %s by build_corpus.py --status from server.py, the test files and this corpus. Do not edit by hand; rerun it (build_corpus.py --all) after tools, tests or chunks change. A number here that disagrees with a hand-written one elsewhere is the newer one." % today,
        "TOOLS: %s MCP tools are registered in server.py (count of @mcp.tool); tool_guide() maps them by family." % (tools if tools is not None else "unknown (server.py not found)"),
        "TESTS: %d test_*.py files in tests/ with %d check()/assert/def test_ call sites (a STATIC count of lines, not the number of checks one run makes; the largest: %s). start_job('editor_suite') runs the editor-only tests; test_knowledge_eval.py grades this knowledge base." % (
            nfiles, ncalls, ", ".join("%s %d" % kv for kv in top)),
        "KNOWLEDGE BASE: %d chunks (%d live, %d superseded). Live by category: %s. Status of all: %s. verified_in of all: %s." % (
            len(rows) - (1 if any(r["id"] == STATUS_ID for r in rows) else 0), len(live), len(sup), ", ".join("%s %d" % kv for kv in sorted(cats.items())),
            ", ".join("%s %d" % kv for kv in sorted(by_status.items())), ", ".join("%s %d" % kv for kv in sorted(by_ver.items()))),
        "EVALUATION: hold-out questions graded by chunk id are in knowledge_holdout.json (the recorded before/after numbers sit in its 'recorded' block); knowledge_dev.json is the set synonyms are tuned on; the old 76 self-graded title-substring questions in tests/test_knowledge_eval.py are labelled 'dev, self-graded'. Never quote a self-graded rate as accuracy.",
        "NEVER SEEN WORKING (verified_in none; the claim was read, planned or run only in a tool, not seen in the game): %s." % (", ".join(none) or "none"),
        "PARTLY SEEN (verified_in partial; see each chunk's verified_note for what was and was not seen): %s." % (", ".join(partial) or "none"),
        "REPLACED CHUNKS (excluded from search unless include_superseded; the old text is in their history field): %s." % (", ".join("%s -> %s" % p for p in sup) or "none"),
        "SEARCH FIELDS: query_knowledge results carry status (measured / read-from-source / hypothesis / superseded / unclassified), verified_in (none / editor / packaged / partial / unclassified), date, superseded_by. 'unclassified' means nobody has recorded the evidence, not that the claim is wrong.",
    ]
    return dict(
        id=STATUS_ID, title="STATUS (generated): tool, test and chunk counts, and the chunks whose claims were never seen working",
        category="project", tags=["status", "current state", "how many tools", "how many tests", "how many chunks", "never seen", "unverified", "not verified", "what is verified", "counts", "generated", "where are we", "overview"],
        source="Generated by rag/build_corpus.py --status on %s" % today,
        content="\n".join(lines), status="measured", verified_in="unclassified", date=today, superseded_by="")


def do_status():
    rows = L.read_rows()
    chunk = build_status_chunk(rows)
    start = L.get(START_ID)
    upd = [chunk]
    if start and START_MARK not in start["content"]:
        start = L.normalize(start)
        start["content"] = start["content"].rstrip() + "\n" + START_MARK + " (generated: tool, test and chunk counts, the chunks whose claims were never seen working)."
        upd.append(start)
    L.upsert_many(upd)
    print("status chunk written (%d chars); start-here %s" % (len(chunk["content"]), "linked" if len(upd) == 2 else "already linked"))


def do_readme():
    rows = L.read_rows()
    live = [r for r in rows if r.get("status") != "superseded"]
    cats = collections.Counter(r["category"] for r in rows)
    st = collections.Counter(r.get("status") for r in rows)
    vf = collections.Counter(r.get("verified_in") for r in rows)
    sup = [r for r in rows if r.get("status") == "superseded"]
    parents = collections.Counter(r["parent"] for r in rows if r.get("parent"))
    out = []
    out.append("# Knowledge base (RAG corpus)\n")
    out.append("This file is generated by `python rag/build_corpus.py --readme`. To change its wording edit `do_readme()` in `rag/build_corpus.py` and regenerate; "
               "an edit made here is lost on the next run.\n")
    out.append("A searchable record of what was measured, broken and fixed while building this server and the Ashvale example "
               "(traps, silent engine failures, playbooks, tool descriptions). Many chunks are field notes: they name the example's levels and assets, "
               "may contain Windows paths from the author's machine and are partly written in the first person. "
               "Some chunks paraphrase or quote Crytek documentation or engine source; the licence of those sources was not checked. "
               "`query_knowledge(\"<question>\")` (MCP) or `python rag/rag_query.py \"<question>\"` search it: BM25 over the title (x%d), tags (x%d) and body, "
               "filler words dropped, synonyms (x%s) widening the query. The CRYENGINE manual is a separate index (`catalogs/docs_search.py`); where the two disagree, a measurement wins over the manual.\n" % (R.TITLE_W, R.TAG_W, R.SYN_W))
    out.append("**%d chunks** (%d live, %d superseded, %d of them split children).\n" % (len(rows), len(live), len(sup), sum(parents.values())))
    out.append("## Files\n")
    out.append("| file | what |\n|---|---|")
    out.append("| `cryengine_project_rag.jsonl` | the corpus, one JSON chunk per line; `query_knowledge` reads this file. It can optionally be mirrored to a second copy named by the environment variable `CRYENGINE_CORPUS_MIRROR` (empty by default = no mirror) |")
    out.append("| `rag_query.py` | the query program |")
    out.append("| `corpus_lib.py` | the only way to change the corpus: `upsert`, `validate`, `sync`, `remove` |")
    out.append("| `build_corpus.py` | `--check`, `--sync`, `--status`, `--readme`, `--all` |")
    out.append("| `backups/` | created on the first write (not in the repository): the newest 25 pre-write copies of the corpus, made by `sync` |")
    out.append("| `../knowledge_holdout.json`, `../knowledge_dev.json`, `../catalogs/knowledge_eval.py`, `../tests/test_knowledge_eval.py` | the evaluation (hold-out graded by chunk id; dev for tuning) |\n")
    out.append("## Categories\n")
    out.append("| category | chunks | contains |\n|---|---|---|")
    for k in R.CATEGORIES:
        out.append("| `%s` | %d | %s |" % (k, cats.get(k, 0), R.CATEGORY_HELP[k]))
    out.append("\nThe filter understands case, plurals and the old category names (`traps`, `how-to`, `gamesdk-ai`, `open-problem` ...); the old name is kept on each chunk as the tag `cat:<old>`.\n")
    out.append("## Fields of a chunk\n")
    out.append("`id` (lowercase, hyphens), `title` (<= %d chars), `category`, `tags` (the words a newcomer types), `source`, `content`, and" % L.MAX_TITLE)
    out.append("- `status`: `measured` / `read-from-source` / `hypothesis` / `superseded` / `unclassified` (nobody recorded the evidence; NOT 'wrong'). Now: " + ", ".join("%s %d" % kv for kv in sorted(st.items())) + ".")
    out.append("- `verified_in`: `none` (never run or seen), `editor`, `packaged`, `partial` (see `verified_note`), `unclassified`. Now: " + ", ".join("%s %d" % kv for kv in sorted(vf.items())) + ".")
    out.append("- `date` (YYYY-MM-DD it was written, empty when unknown), `superseded_by` (required when superseded), optional `history` (old text kept for the record), `parent` (the chunk it was split from), `verified_note`.\n")
    out.append("A chunk with `status: superseded` is NOT returned unless `include_superseded=True` / `--include-superseded`.\n")
    out.append("## Use\n")
    out.append("Run from the repository root:\n")
    out.append("```\npython rag/rag_query.py \"material renders black\" --top 3\npython rag/rag_query.py --cat trap --list\npython rag/rag_query.py --verified-in none --list   # claims never seen working\npython rag/rag_query.py --id npc-open-problem        # a replaced chunk, with its history\n```\n")
    out.append("## Change it\n")
    out.append("```python\nimport sys; sys.path.insert(0, \"rag\")\nimport corpus_lib as L\nL.upsert(dict(id=\"trap-x\", title=\"Symptom as the title\", category=\"trap\", tags=[\"words\", \"a newcomer\", \"types\"],\n              source=\"where it was seen\", content=\"...\", status=\"measured\", verified_in=\"editor\", date=\"2026-01-31\"))\n```\n"
               "`upsert` validates (required fields, tag types, title length, category list, duplicate ids and titles, dangling `superseded_by` / `parent`) and writes the corpus atomically (and the mirror copy, if `CRYENGINE_CORPUS_MIRROR` names one; "
               "that file must already exist). Nothing is written when the result has an error. Then `python rag/build_corpus.py --all`. "
               "Lint warnings: an open problem or hypothesis older than %d days or undated, text naming a chunk id that does not exist, a chunk over %d characters (split it into one-symptom chunks)." % (L.OPEN_PROBLEM_MAX_AGE_DAYS, L.SOFT_MAX_CONTENT))
    out.append("\n## Superseded chunks\n")
    for r in sorted(sup, key=lambda x: x["id"]):
        out.append("- `%s` -> `%s`" % (r["id"], r.get("superseded_by")))
    out.append("")
    text = "\n".join(out) + "\n"
    path = os.path.join(HERE, "README.md")
    L._atomic_write(path, text)
    print("README.md written (%d chars)" % len(text))


def do_check():
    rep, problems = L.check()
    print(rep.text())
    for p in problems:
        print("PROBLEM:", p)
    rows = L.read_rows()
    if not any(r["id"] == STATUS_ID for r in rows):
        print("note: no status chunk yet (run --status)")
    return 0 if rep.ok and not problems else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for f in ("check", "sync", "status", "readme", "all"):
        ap.add_argument("--" + f, action="store_true")
    a = ap.parse_args()
    if not any(vars(a).values()):
        ap.print_help()
        return 0
    code = 0
    try:
        if a.sync:
            print("synced %d chunks; identical: %s" % (L.sync(), L.copies_identical()))
        if a.status or a.all:
            do_status()
        if a.readme or a.all:
            do_readme()
        if a.check or a.all:
            code = do_check()
    except L.CorpusError as e:
        print("CORPUS ERROR:", e)
        return 1
    return code


if __name__ == "__main__":
    sys.exit(main())
