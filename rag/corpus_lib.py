#!/usr/bin/env python3
"""The ONE way to change the knowledge corpus.

The corpus is one file:
    PRIMARY  rag/cryengine_project_rag.jsonl      (what query_knowledge reads)
and, optionally, a byte-identical second copy:
    MIRROR   the file named by the environment variable CRYENGINE_CORPUS_MIRROR (empty by default = no mirror)
Never edit either by hand: use this module. When a mirror is configured it is always written together with PRIMARY.

    import corpus_lib as L
    L.upsert(dict(id="trap-x", title="...", category="trap", tags=[...], source="...", content="...",
                  status="measured", verified_in="editor", date="2026-10-23"))     # validates, writes atomically (PRIMARY, plus the mirror if set)
    L.upsert_many([...])
    L.validate()                 # -> Report(errors, warnings); L.validate().ok
    L.sync()                     # rewrite PRIMARY (and the mirror, if one is configured; fails loudly if the mirror or its folder is missing)
    L.remove(id)                 # refuses while another chunk points at it (parent / superseded_by)

A write is refused (nothing is touched) when validate() finds an ERROR in the result. Warnings (lint) are returned, never block.
Before every write the previous PRIMARY is copied to rag/backups/ (the newest 25 are kept).
Standard library only; imports rag_query for the taxonomy so there is a single definition of it.
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rag_query as R  # noqa: E402

PRIMARY = os.path.join(HERE, "cryengine_project_rag.jsonl")
MIRROR = os.environ.get("CRYENGINE_CORPUS_MIRROR", "")   # optional second copy of the corpus (a docs folder); empty = no mirror
MIRROR_RAG_QUERY = os.path.join(os.path.dirname(MIRROR), "rag_query.py") if MIRROR else ""
BACKUP_DIR = os.path.join(HERE, "backups")
LOCK = os.path.join(HERE, ".corpus.lock")

REQUIRED = ["id", "title", "category", "tags", "source", "content", "status", "verified_in", "date", "superseded_by"]
OPTIONAL = ["verified_note", "history", "parent"]
KEY_ORDER = REQUIRED + OPTIONAL
MAX_TITLE = 140
SOFT_MAX_CONTENT = 3000      # lint: a longer chunk should be split into one-symptom chunks
OPEN_PROBLEM_MAX_AGE_DAYS = 14
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class CorpusError(Exception):
    pass


class Report:
    def __init__(self):
        self.errors, self.warnings = [], []

    @property
    def ok(self):
        return not self.errors

    def err(self, cid, msg):
        self.errors.append((cid, msg))

    def warn(self, cid, msg):
        self.warnings.append((cid, msg))

    def text(self, warnings=True, limit=40):
        out = ["%d error(s), %d warning(s)" % (len(self.errors), len(self.warnings))]
        for cid, m in self.errors[:limit]:
            out.append("  ERROR   %s: %s" % (cid, m))
        if len(self.errors) > limit:
            out.append("  ... %d more errors" % (len(self.errors) - limit))
        if warnings:
            for cid, m in self.warnings[:limit]:
                out.append("  warn    %s: %s" % (cid, m))
            if len(self.warnings) > limit:
                out.append("  ... %d more warnings" % (len(self.warnings) - limit))
        return "\n".join(out)


# ---------------------------------------------------------------------------- reading / writing
def read_rows(path=PRIMARY):
    rows = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as e:
                raise CorpusError("%s line %d does not parse: %s" % (path, n, e))
    return rows


def serialize(rows):
    return "".join(json.dumps(order_keys(r), ensure_ascii=False) + "\n" for r in rows)


def order_keys(r):
    out = {k: r[k] for k in KEY_ORDER if k in r}
    for k in r:
        if k not in out and not k.startswith("_"):
            out[k] = r[k]
    return out


def _atomic_write(path, text):
    d = os.path.dirname(path)
    tmp = os.path.join(d, ".%s.%d.tmp" % (os.path.basename(path), os.getpid()))
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    last = None
    for _ in range(20):                 # Windows: a reader (the MCP server) can hold the file for a moment
        try:
            os.replace(tmp, path)
            return
        except PermissionError as e:
            last = e
            time.sleep(0.25)
    try:
        os.remove(tmp)
    except OSError:
        pass
    raise CorpusError("could not replace %s: %s" % (path, last))


class _Lock:
    def __enter__(self):
        t0 = time.time()
        while True:
            try:
                self.fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(LOCK) > 120:      # a dead writer
                        os.remove(LOCK)
                        continue
                except OSError:
                    pass
                if time.time() - t0 > 60:
                    raise CorpusError("corpus lock %s held for over 60 s" % LOCK)
                time.sleep(0.2)

    def __exit__(self, *a):
        try:
            os.close(self.fd)
            os.remove(LOCK)
        except OSError:
            pass


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def copies_identical():
    """True when there is no mirror configured, else whether PRIMARY and the mirror are byte-identical."""
    if not MIRROR:
        return True
    return os.path.exists(PRIMARY) and os.path.exists(MIRROR) and sha(PRIMARY) == sha(MIRROR)


def _backup_primary():
    if not os.path.exists(PRIMARY):
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(BACKUP_DIR, "cryengine_project_rag_%s.jsonl" % stamp)
    shutil.copyfile(PRIMARY, dst)
    old = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith("cryengine_project_rag_") and f.endswith(".jsonl"))
    for f in old[:-25]:
        try:
            os.remove(os.path.join(BACKUP_DIR, f))
        except OSError:
            pass
    return dst


def sync(rows=None, allow_new_mirror=False):
    """Write rows (default: re-read and normalise PRIMARY) to PRIMARY and, when CRYENGINE_CORPUS_MIRROR is set, to the mirror too,
    atomically, then prove the two are byte-identical. With no mirror configured only PRIMARY is written.
    Raises CorpusError - loudly - when a configured mirror folder or file is missing (nothing is written then)."""
    if MIRROR and not os.path.isdir(os.path.dirname(MIRROR)):
        raise CorpusError("mirror folder is missing: %s (check CRYENGINE_CORPUS_MIRROR)" % os.path.dirname(MIRROR))
    if MIRROR and not os.path.exists(MIRROR) and not allow_new_mirror:
        raise CorpusError("mirror copy is missing: %s (refusing to invent it; pass allow_new_mirror=True)" % MIRROR)
    if not os.path.exists(PRIMARY):
        raise CorpusError("primary corpus is missing: %s" % PRIMARY)
    if rows is None:
        rows = [normalize(r) for r in read_rows(PRIMARY)]
    text = serialize(rows)
    _backup_primary()
    _atomic_write(PRIMARY, text)
    if MIRROR:
        _atomic_write(MIRROR, text)
    if not copies_identical():
        raise CorpusError("the two copies differ after sync - investigate before anything else")
    return len(rows)


# ---------------------------------------------------------------------------- normalising
def normalize(c):
    """A chunk with every required field, a canonical category (the old one kept as the tag 'cat:<old>'), clean tags."""
    c = dict(c)
    c.setdefault("source", "")
    c.setdefault("tags", [])
    c["tags"] = [t for t in (str(x).strip() for x in c["tags"]) if t]
    c.setdefault("status", "unclassified")
    c.setdefault("verified_in", "unclassified")
    c.setdefault("date", "")
    c.setdefault("superseded_by", "")
    cat = c.get("category", "")
    if cat not in R.CATEGORIES:
        n = R.norm_name(cat)
        canon = R.LEGACY_CATEGORY.get(n) or R.canonical_category(n)
        if canon:
            if (R.OLD_CAT_PREFIX + n) not in c["tags"] and n != canon:
                c["tags"].append(R.OLD_CAT_PREFIX + n)
            c["category"] = canon
    return c


# ---------------------------------------------------------------------------- validation + lint
def _id_candidates(rows):
    ids = {r.get("id") for r in rows}
    heads = {}
    for i in ids:
        if isinstance(i, str):
            heads[i.split("-")[0]] = heads.get(i.split("-")[0], 0) + 1
    return ids, {h for h, n in heads.items() if n >= 3}


_ID_TOKEN = re.compile(r"(?<![\w/\\.])([a-z][a-z0-9]*(?:-[a-z0-9]+)+)(?![\w/\\])")
# hyphenated English words that happen to start like an id family; not references
_NOT_IDS = {"engine-computed", "engine-5", "engine-log", "engine-side", "engine-made", "engine-native", "engine-owned", "engine-assets",
            "nav-points", "nav-link", "player-opened", "content-fed", "process-wide", "homebound-project-ggj19", "fact-checked"}


def validate(rows=None, today=None):
    """Errors block a write; warnings are lint. Returns Report."""
    rep = Report()
    if rows is None:
        try:
            rows = read_rows(PRIMARY)
        except (CorpusError, OSError) as e:
            rep.err("-", str(e))
            return rep
    today = today or datetime.date.today()
    ids = {}
    titles = {}
    for r in rows:
        cid = r.get("id", "?")
        for k in REQUIRED:
            if k not in r:
                rep.err(cid, "missing field %r" % k)
        if not isinstance(r.get("id"), str) or not ID_RE.match(r.get("id", "")):
            rep.err(cid, "id must be lowercase letters, digits and hyphens")
        if cid in ids:
            rep.err(cid, "duplicate id")
        ids[cid] = r
        t = r.get("title")
        if not isinstance(t, str) or not t.strip():
            rep.err(cid, "title missing or empty")
        else:
            if len(t) > MAX_TITLE:
                rep.err(cid, "title is %d chars (max %d)" % (len(t), MAX_TITLE))
            key = t.strip().lower()
            if key in titles:
                rep.err(cid, "duplicate title with %s" % titles[key])
            titles[key] = cid
        tags = r.get("tags")
        if not isinstance(tags, list) or not all(isinstance(x, str) and x.strip() for x in tags):
            rep.err(cid, "tags must be a list of non-empty strings")
        elif len(tags) == 0:
            rep.warn(cid, "no tags (tags carry the words a newcomer types)")
        if not isinstance(r.get("content"), str) or not r.get("content", "").strip():
            rep.err(cid, "content missing or empty")
        if not isinstance(r.get("source", ""), str):
            rep.err(cid, "source must be a string")
        if r.get("category") not in R.CATEGORIES:
            rep.err(cid, "category %r is not one of %s" % (r.get("category"), ", ".join(R.CATEGORIES)))
        if r.get("status") not in R.STATUSES:
            rep.err(cid, "status %r is not one of %s" % (r.get("status"), ", ".join(R.STATUSES)))
        if r.get("verified_in") not in R.VERIFIED_IN:
            rep.err(cid, "verified_in %r is not one of %s" % (r.get("verified_in"), ", ".join(R.VERIFIED_IN)))
        d = r.get("date", "")
        if d != "" and not (isinstance(d, str) and DATE_RE.match(d)):
            rep.err(cid, "date %r is not YYYY-MM-DD" % (d,))
        else:
            if d:
                try:
                    datetime.date.fromisoformat(d)
                except ValueError:
                    rep.err(cid, "date %r is not a real date" % d)
    # references that must resolve
    for cid, r in ids.items():
        sb = r.get("superseded_by", "")
        if r.get("status") == "superseded":
            if not sb:
                rep.err(cid, "status superseded without superseded_by")
            elif sb not in ids:
                rep.err(cid, "superseded_by %r does not exist" % sb)
            elif sb == cid:
                rep.err(cid, "superseded_by points at itself")
        elif sb:
            rep.err(cid, "superseded_by is set but status is %r (set status superseded or clear it)" % r.get("status"))
        p = r.get("parent")
        if p is not None and p not in ids:
            rep.err(cid, "parent %r does not exist" % p)
    # superseded chains must end at a live chunk, no cycles
    for cid, r in ids.items():
        seen, cur = {cid}, r
        while cur.get("status") == "superseded" and cur.get("superseded_by") in ids:
            nxt = cur["superseded_by"]
            if nxt in seen:
                rep.err(cid, "superseded_by chain loops through %s" % nxt)
                break
            seen.add(nxt)
            cur = ids[nxt]
        else:
            if cur.get("status") == "superseded" and cur.get("superseded_by") and cur["superseded_by"] not in ids:
                pass
    # ---- lint (warnings)
    known, heads = _id_candidates(rows)
    for cid, r in ids.items():
        text = r.get("content", "")
        if len(text) > SOFT_MAX_CONTENT and "generated" not in r.get("tags", []):
            rep.warn(cid, "content is %d chars (> %d): split into one-symptom chunks" % (len(text), SOFT_MAX_CONTENT))
        dangling = set()
        for m in _ID_TOKEN.finditer(text):
            tok = m.group(1)
            if tok in known or tok in _NOT_IDS or tok == cid or tok in R.CATEGORIES:
                continue
            if tok.split("-")[0] in heads:
                dangling.add(tok)
        if dangling:
            rep.warn(cid, "text names chunk id(s) that do not exist: %s" % ", ".join(sorted(dangling)))
        # an open problem / hypothesis must be dated and recent, or it silently rots
        is_open = (r.get("status") == "hypothesis"
                   or bool(re.match(r"(?i)^(open problem|leading hypothesis)", r.get("title", ""))))
        if is_open and r.get("status") != "superseded":
            d = r.get("date", "")
            try:
                age = (today - datetime.date.fromisoformat(d)).days if d else None
            except ValueError:
                age = None
            if age is None:
                rep.warn(cid, "open problem / hypothesis without a date")
            elif age > OPEN_PROBLEM_MAX_AGE_DAYS:
                rep.warn(cid, "open problem / hypothesis is %d days old (> %d): resolve, re-date or mark superseded" % (age, OPEN_PROBLEM_MAX_AGE_DAYS))
        if r.get("status") == "measured" and r.get("verified_in") == "none" and not r.get("verified_note"):
            rep.warn(cid, "status measured but verified_in none: say what was measured and where it was NOT seen (verified_note)")
    return rep


# ---------------------------------------------------------------------------- the editing API
def upsert_many(chunks, sync_now=True):
    """Insert or replace chunks by id (replacement keeps the chunk's place). All-or-nothing: validation errors write nothing."""
    with _Lock():
        rows = [normalize(r) for r in read_rows(PRIMARY)]
        index = {r["id"]: i for i, r in enumerate(rows)}
        for c in chunks:
            c = normalize(c)
            if c["id"] in index:
                rows[index[c["id"]]] = c
            else:
                index[c["id"]] = len(rows)
                rows.append(c)
        rep = validate(rows)
        # a legacy lint backlog must not block a good write: refuse only on errors that name a chunk being written
        touched = {c["id"] for c in chunks}
        blocking = [(cid, m) for cid, m in rep.errors if cid in touched or cid == "-"]
        if blocking:
            raise CorpusError("refused, nothing written:\n" + "\n".join("  %s: %s" % e for e in blocking))
        other = [(cid, m) for cid, m in rep.errors if cid not in touched and cid != "-"]
        if other:
            raise CorpusError("refused, nothing written: the corpus already has errors elsewhere:\n" + "\n".join("  %s: %s" % e for e in other[:15]))
        if sync_now:
            sync(rows)
    return rep


def upsert(chunk, sync_now=True):
    return upsert_many([chunk], sync_now)


def remove(cid):
    with _Lock():
        rows = [normalize(r) for r in read_rows(PRIMARY)]
        if not any(r["id"] == cid for r in rows):
            raise CorpusError("no such id: " + cid)
        users = [r["id"] for r in rows if r.get("parent") == cid or r.get("superseded_by") == cid]
        if users:
            raise CorpusError("refused: %s is the parent / replacement of %s" % (cid, ", ".join(users)))
        rows = [r for r in rows if r["id"] != cid]
        rep = validate(rows)
        if not rep.ok:
            raise CorpusError("refused, nothing written:\n" + rep.text(warnings=False))
        sync(rows)


def get(cid, path=PRIMARY):
    for r in read_rows(path):
        if r.get("id") == cid:
            return r
    return None


def children_of(cid, rows=None):
    return [r for r in (rows or read_rows(PRIMARY)) if r.get("parent") == cid]


# ---------------------------------------------------------------------------- whole-system check
def check():
    """Everything build_corpus.py --check reports: (report, extra_problems)."""
    problems = []
    rep = validate()
    if not MIRROR:
        return rep, problems          # no mirror configured: nothing more to compare
    if not os.path.exists(MIRROR):
        problems.append("mirror copy missing: " + MIRROR)
    elif not copies_identical():
        problems.append("the two corpus copies are NOT byte-identical (%s vs %s)" % (PRIMARY, MIRROR))
    if not os.path.exists(MIRROR_RAG_QUERY):
        problems.append("mirror rag_query.py is missing: " + MIRROR_RAG_QUERY)
    else:
        with open(MIRROR_RAG_QUERY, encoding="utf-8") as f:
            wrapper = f.read()
        if "THIN WRAPPER" not in wrapper or "mcp_server" not in wrapper:
            # not the wrapper: then it must be an identical copy of the real one
            if sha(MIRROR_RAG_QUERY) != sha(os.path.join(HERE, "rag_query.py")):
                problems.append("the mirror rag_query.py is neither the thin wrapper nor an identical copy of rag/rag_query.py")
    return rep, problems


if __name__ == "__main__":
    rep, problems = check()
    print(rep.text())
    for p in problems:
        print("PROBLEM:", p)
    sys.exit(0 if rep.ok and not problems else 1)
