#!/usr/bin/env python3
"""Grade query_knowledge on a file of questions with EXPECTED CHUNK IDS (not title substrings).

    python catalogs/knowledge_eval.py                       # hold-out + dev against the live corpus and rag_query
    python catalogs/knowledge_eval.py --file knowledge_holdout.json --verbose
    python catalogs/knowledge_eval.py --rag path/to/rag_query.py --corpus path/to/corpus.jsonl   # an older version, for a before/after

A question's 'expected' is a list of acceptable chunk ids; null means the corpus has no answer (a GAP: reported, not graded).
A hit counts when its id, or the id of the chunk it was split from ('parent'), is in the list.
Metrics: rank-1 rate, top-3 rate, top-5 rate, MRR (reciprocal rank over the top 10) over the graded questions.
Editor-free, standard library only.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import argparse
import importlib.util
import json
import os
import sys

HERE = _paths.ROOT
DEFAULT_RAG = os.path.join(HERE, "rag", "rag_query.py")
DEFAULT_CORPUS = os.path.join(HERE, "rag", "cryengine_project_rag.jsonl")
FILES = ["knowledge_holdout.json", "knowledge_dev.json"]


def load_module(path, name="rag_query_under_test"):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ranked_ids(R, chunks, q, depth=10):
    """The ids of the top results, each paired with its parent id (or None)."""
    hits = R.search(chunks, q, depth)
    return [(c["id"], c.get("parent")) for _s, c in hits], hits


def evaluate(R, chunks, questions, depth=10):
    """Returns {n, gaps, rank1, top3, top5, mrr, rows, gap_rows}."""
    rows, gap_rows = [], []
    for item in questions:
        ids, hits = ranked_ids(R, chunks, item["q"], depth)
        exp = item.get("expected")
        if exp is None:
            gap_rows.append({"id": item["id"], "q": item["q"], "top1": ids[0][0] if ids else None,
                             "score": round(hits[0][0], 2) if hits else 0.0, "n_results": len(ids), "note": item.get("note", "")})
            continue
        want = set(exp)
        rank = 0
        for i, (cid, parent) in enumerate(ids, 1):
            if cid in want or (parent and parent in want):
                rank = i
                break
        rows.append({"id": item["id"], "q": item["q"], "rank": rank, "top": [i for i, _p in ids[:3]], "expected": exp[:3], "note": item.get("note", "")})
    n = len(rows)
    out = {"n": n, "gaps": len(gap_rows), "rows": rows, "gap_rows": gap_rows}
    if n:
        out["rank1"] = sum(1 for r in rows if r["rank"] == 1) / float(n)
        out["top3"] = sum(1 for r in rows if 1 <= r["rank"] <= 3) / float(n)
        out["top5"] = sum(1 for r in rows if 1 <= r["rank"] <= 5) / float(n)
        out["mrr"] = sum((1.0 / r["rank"]) for r in rows if r["rank"]) / float(n)
    else:
        out["rank1"] = out["top3"] = out["top5"] = out["mrr"] = 0.0
    return out


def load_questions(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data, data["questions"]


def summary_line(label, res):
    return "%-34s n=%-3d gaps=%d  rank-1 %5.1f%%  top-3 %5.1f%%  top-5 %5.1f%%  MRR %.3f" % (
        label, res["n"], res["gaps"], 100 * res["rank1"], 100 * res["top3"], 100 * res["top5"], res["mrr"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rag", default=DEFAULT_RAG)
    ap.add_argument("--corpus", default=DEFAULT_CORPUS)
    ap.add_argument("--file", action="append", help="question file(s); default hold-out and dev")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--json", action="store_true", help="print the metrics as JSON")
    a = ap.parse_args()
    R = load_module(a.rag)
    chunks = R.load(a.corpus)
    results = {}
    for fn in (a.file or [os.path.join(HERE, f) for f in FILES]):
        if not os.path.exists(fn):
            print("missing", fn)
            continue
        _data, qs = load_questions(fn)
        res = evaluate(R, chunks, qs)
        results[os.path.basename(fn)] = {k: v for k, v in res.items() if k not in ("rows", "gap_rows")}
        if not a.json:
            print(summary_line(os.path.basename(fn), res))
            if a.verbose:
                for r in res["rows"]:
                    print("   %s rank=%s  %r -> %s  (want %s)" % (r["id"], r["rank"] or "-", r["q"], r["top"], r["expected"]))
                for g in res["gap_rows"]:
                    print("   %s GAP %r -> top1 %s score %s (%d results)" % (g["id"], g["q"], g["top1"], g["score"], g["n_results"]))
    if a.json:
        print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
