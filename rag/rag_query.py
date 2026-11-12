#!/usr/bin/env python3
"""Query the CRYENGINE project knowledge base.

No dependencies beyond the standard library, so it runs anywhere Python 3.8+ does - including Sandbox's embedded interpreter.
Scoring is BM25 over the chunk body plus the title (x TITLE_W) and the tags (x TAG_W), the query is cleaned of filler words and
widened by SYNONYMS, a chunk that matches more of the query's distinct words ranks above one that matches a single rare word,
and a literal multi-word match gets a bonus.

Every chunk carries (corpus_lib.py keeps them valid):  id, title, category (one of CATEGORIES), tags, source, content, and
    status        measured | read-from-source | hypothesis | superseded | unclassified
    verified_in   none | editor | packaged | partial | unclassified      (where the claim was SEEN working; none = never run)
    date          YYYY-MM-DD the chunk was written (or "")
    superseded_by id of the chunk that replaces this one (required when status = superseded)
and optionally  history (old text kept for the record), parent (the chunk this one was split from), verified_note.
A chunk with status = superseded is NOT returned unless include_superseded=True (--include-superseded).

    python rag_query.py "navmesh unreachable after restart"
    python rag_query.py "python indentation error" --top 3
    python rag_query.py --cat trap --list            # 'traps', 'Trap', 'how-to' ... are understood (see CATEGORY_ALIASES)
    python rag_query.py --id npc-open-problem        # a superseded chunk is shown with its replacement
"""
import argparse, json, math, os, re, sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CORPUS = os.path.join(HERE, "cryengine_project_rag.jsonl")

# ---------------------------------------------------------------------------- taxonomy
CATEGORIES = ["playbook", "trap", "howto", "tooling", "fact", "ai", "navigation", "animation", "level-craft", "gameplay",
              "engine-reference", "build-package", "project"]
CATEGORY_HELP = {
    "playbook": "ordered recipes: how to do a whole job with these tools",
    "trap": "something that fails silently or costs a build cycle, and the cure",
    "howto": "one measured procedure",
    "tooling": "what an MCP tool / helper script / bridge verb does and how to read it",
    "fact": "a measured or read-from-source fact, finding or negative result",
    "ai": "NPC brains, perception, factions, behaviour trees, UQS, Schematyc",
    "navigation": "navmesh, off-mesh links, agent types, runtime updates",
    "animation": "Mannequin, blend spaces, clips, rigs, foot slide, ragdoll",
    "level-craft": "terrain, vegetation, lighting, audio, particles, rendering, level construction",
    "gameplay": "player, physics, combat, pickups, Ashvale game code",
    "engine-reference": "how the engine / the GameSDK sample / the templates are built",
    "build-package": "building Game.dll, packaging, shaders for release",
    "project": "decisions, policies, lessons, process, paths and machine environment",
}
# every category name that ever existed -> its canonical one (the old name is also kept on the chunk as the tag 'cat:<old>')
LEGACY_CATEGORY = {
    "environment": "project", "editor-control": "tooling", "trap": "trap", "build": "build-package", "tooling": "tooling",
    "content": "level-craft", "lighting": "level-craft", "player": "gameplay", "npc": "ai", "navigation": "navigation",
    "open-problem": "ai", "engine-internals": "engine-reference", "process": "project", "gamesdk-ai": "ai",
    "gamesdk-systems": "engine-reference", "level-craft": "level-craft", "animation": "animation", "ai-decisions": "ai",
    "templates": "engine-reference", "ai": "ai", "gameplay": "gameplay", "packaging": "build-package", "game": "gameplay",
    "components": "gameplay", "characters": "animation", "traps": "trap", "command": "tooling", "rendering": "level-craft",
    "physics": "gameplay", "audio": "level-craft", "howto": "howto", "decision": "project", "fact": "fact", "finding": "fact",
    "measurement": "fact", "lesson": "project", "project": "project", "learning": "project", "playbook": "playbook",
}
# spellings people type
CATEGORY_ALIASES = {
    "traps": "trap", "pitfall": "trap", "pitfalls": "trap", "gotcha": "trap", "gotchas": "trap", "how-to": "howto", "how-tos": "howto",
    "howtos": "howto", "recipe": "howto", "recipes": "howto", "tool": "tooling", "tools": "tooling", "facts": "fact", "findings": "fact",
    "measurements": "fact", "playbooks": "playbook", "nav": "navigation", "anim": "animation", "animations": "animation", "level": "level-craft",
    "levelcraft": "level-craft", "world": "level-craft", "lighting": "level-craft", "audio": "level-craft", "build": "build-package",
    "builds": "build-package", "packaging": "build-package", "package": "build-package", "release": "build-package", "engine": "engine-reference",
    "reference": "engine-reference", "gamesdk": "engine-reference", "player": "gameplay", "game": "gameplay", "decision": "project",
    "decisions": "project", "process": "project", "policy": "project", "lesson": "project", "lessons": "project", "environment": "project",
}
STATUSES = ["measured", "read-from-source", "hypothesis", "superseded", "unclassified"]
VERIFIED_IN = ["none", "editor", "packaged", "partial", "unclassified"]
OLD_CAT_PREFIX = "cat:"       # a tag that records a chunk's former category; never indexed


def norm_name(s):
    return re.sub(r"[\s_]+", "-", (s or "").strip().lower())


def canonical_category(name):
    """A user's category word -> a canonical category, or None. Case, plural, underscore and old-name tolerant."""
    n = norm_name(name)
    if not n:
        return None
    if n in CATEGORY_HELP:
        return n
    if n in CATEGORY_ALIASES and CATEGORY_ALIASES[n] in CATEGORY_HELP:
        return CATEGORY_ALIASES[n]
    if n.endswith("s") and n[:-1] in CATEGORY_HELP:
        return n[:-1]
    return None


def category_matches(chunk, name):
    """True when the chunk is in the category the query named. An OLD name that is not itself a canonical category
    ('decision', 'open-problem', 'gamesdk-ai') selects exactly the chunks that carried it; otherwise the canonical category."""
    n = norm_name(name)
    if not n:
        return True
    canon = n if n in CATEGORY_HELP else None
    if canon is None and n in LEGACY_CATEGORY and n not in CATEGORY_ALIASES:
        return (OLD_CAT_PREFIX + n) in chunk.get("tags", []) or chunk.get("category") == n
    canon = canonical_category(n)
    if canon is None:
        return chunk.get("category") == n or (OLD_CAT_PREFIX + n) in chunk.get("tags", [])
    return chunk.get("category") == canon


# ---------------------------------------------------------------------------- tokens
# Tokens that carry no retrieval signal in this corpus (index and query).
STOP = set("""a an the and or of to in on for with is are was were be been it its this that
these those as at by from if then than so such not no do does did how what when where which
you your i we our my me can could should would will shall may might must have has had using
use used it's dont don't there their they them he she his her""".split())

# Filler a builder types that says nothing about WHICH topic (QUERY only: the index is unchanged; if a query is made only of
# these words they are kept, so 'build' alone still finds something). Chosen on the dev set, never on the hold-out set.
QUERY_STOP = set("""build make add need want get new create help please""".split())

WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*|\d+")


def tokenize(text):
    out = []
    for m in WORD.finditer(text.lower()):
        t = m.group(0)
        if t in STOP or len(t) < 2:
            continue
        out.append(t)
        # split dotted/underscored identifiers so "ai_DebugDraw" also matches "debugdraw"
        for part in re.split(r"[._]", t):
            if len(part) >= 3 and part != t and part not in STOP:
                out.append(part)
    return out


# Chosen on knowledge_dev.json (never on the hold-out set): MRR 0.806 -> 0.842. COVER_A and MIN_SHARE were implemented and measured there
# too and gave no gain (they move single questions both ways), so they ship OFF; USE_QUERY_STOP is neutral on dev and ON because the
# hold-out style of question ("add a ...", "give the player a ...") is full of filler.
TITLE_W = 4          # a title word counts this many body words
TAG_W = 3            # a tag word counts this many body words
SYN_W = 0.6          # weight of a word added by SYNONYMS relative to one typed in the query
COVER_A = 0.0        # share of the score that depends on how many of the query's distinct words the chunk holds (0 = off)
USE_QUERY_STOP = True
MIN_SHARE = 0.0      # drop a chunk holding fewer than this share of the query's distinct words (queries of 3+ words only; 0 = off)


def prepare(c):
    """Fill the fields an older line may lack, and build the index of one chunk."""
    c.setdefault("tags", [])
    c.setdefault("source", "")
    c.setdefault("status", "unclassified")
    c.setdefault("verified_in", "unclassified")
    c.setdefault("date", "")
    c.setdefault("superseded_by", "")
    body = tokenize(c["content"])
    title = tokenize(c["title"])
    tags = tokenize(" ".join(t for t in c["tags"] if not t.startswith(OLD_CAT_PREFIX)))
    c["_tf"] = Counter(body + title * TITLE_W + tags * TAG_W)
    c["_len"] = sum(c["_tf"].values())
    return c


def load(path=None):
    chunks = []
    with open(path or DEFAULT_CORPUS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(prepare(json.loads(line)))
    return chunks


# A newcomer's words are not this corpus's words ("bake the navmesh" vs "regenerate"; "falloff" vs "attenuation"). Each query token that has an entry here also searches for the corpus's own words.
# Applied to the QUERY only; the index is unchanged. Keep entries few and exact: every added token weighs like a typed one.
SYNONYMS = {
    "bake": ["regenerate", "generate", "export"], "baking": ["regenerate", "generate"], "navigation": ["navmesh"], "nav": ["navmesh"], "pathfinding": ["navmesh", "path"],
    "falloff": ["attenuation", "attenuate", "distance"], "attenuate": ["attenuation"], "volume": ["gain"], "sound": ["audio"], "sounds": ["audio"], "ambience": ["ambient"],
    "dialogue": ["dialog", "drs", "response", "speech", "subtitles"], "conversation": ["dialog", "drs", "speech"], "talk": ["dialog", "speech"], "speech": ["voice", "drs"],
    "flash": ["scaleform", "flashui"], "menu": ["scaleform", "flashui"], "ui": ["scaleform", "flashui"],
    "component": ["components", "schematyc"], "components": ["schematyc", "component"], "cpp": ["c++", "component"], "write": ["component"],
    "dll": ["game.dll", "building"], "crash": ["crashes", "crashed"], "crashes": ["crash", "access"], "freeze": ["hang", "hung"],
    "ship": ["package", "packaging"], "release": ["package", "packaging"], "build": ["building", "compile"], "fails": ["failed", "error"], "scratch": ["new", "create"], "shaders": ["shader", "cache"], "shader": ["cache"],
    "quest": ["objectives", "objective"], "quests": ["objectives"], "save": ["savegame", "serialize"], "load": ["serialize"],
    "light": ["lamp", "torch"], "lamp": ["light", "torch"], "torch": ["lamp", "light"], "fire": ["flame", "particle"], "particles": ["particle", "pfx"],
    "frame": ["frametime", "profile"], "performance": ["profile", "statoscope"], "slow": ["performance", "profile"], "slowly": ["slow", "performance", "profile"], "lag": ["performance", "profile", "frame"], "laggy": ["performance", "profile", "frame"], "stutter": ["performance", "frame", "profile"], "fps": ["frame", "performance"],
    "indoor": ["interior", "visarea"], "interior": ["visarea", "indoor"], "weather": ["rain"], "decal": ["decals"], "movie": ["sequence", "trackview"], "cutscene": ["sequence", "trackview", "cinematic"],
    "enemy": ["bandit", "faction"], "enemies": ["bandit", "faction"], "ai": ["npc"], "npc": ["villager", "ai"], "character": ["npc", "villager"],
    "tree": ["vegetation"], "trees": ["vegetation"], "grass": ["vegetation"], "undo": ["ctrl+z", "undo"], "start": ["begin", "workflow"], "begin": ["workflow", "start"],
    "stuck": ["symptom", "diagnose"], "broken": ["symptom", "diagnose"], "wrong": ["symptom", "diagnose"],
}


def expand(tokens):
    out = list(tokens)
    for t in tokens:
        out.extend(SYNONYMS.get(t, ()))
    return out


def query_terms(query):
    """-> (groups, weights). groups: one (typed word, its synonyms) per DISTINCT word typed in the query after the filler words are dropped;
    weights: every word that is searched (typed = 1.0, added by SYNONYMS = SYN_W)."""
    toks = tokenize(query)
    if USE_QUERY_STOP:
        kept = [t for t in toks if t not in QUERY_STOP]
        toks = kept or toks
    groups, seen = [], set()
    for t in toks:
        if t not in seen:
            seen.add(t)
            groups.append((t, list(SYNONYMS.get(t, ()))))
    weights = {t: 1.0 for t, _syn in groups}
    for _t, syn in groups:
        for x in syn:
            if x not in weights:
                weights[x] = SYN_W
    return groups, weights


def search(chunks, query, top=5, category=None, include_superseded=False, status=None, verified_in=None):
    """Returns [(score, chunk)] best first. category understands aliases and old names; a chunk with status superseded is left out
    unless include_superseded is True (asking for status='superseded' includes them)."""
    want_status = status
    pool = []
    for c in chunks:
        if category and not category_matches(c, category):
            continue
        st = c.get("status", "unclassified")
        if st == "superseded" and not include_superseded and want_status != "superseded":
            continue
        if want_status and st != want_status:
            continue
        if verified_in and c.get("verified_in", "unclassified") != verified_in:
            continue
        pool.append(c)
    if not pool:
        return []
    groups, weights = query_terms(query)
    if not weights:
        return []
    N = len(pool)
    avg = sum(c["_len"] for c in pool) / N
    df = Counter()
    for c in pool:
        for t in set(c["_tf"]):
            df[t] += 1
    k1, b = 1.5, 0.45   # low b: chunks are long by design, do not over-penalise length
    ql = query.lower()
    scored = []
    for c in pool:
        tfm = c["_tf"]
        s = 0.0
        for t, w in weights.items():
            tf = tfm.get(t, 0)
            if not tf:
                continue
            idf = math.log(1 + (N - df[t] + 0.5) / (df[t] + 0.5))
            s += w * idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * c["_len"] / avg))
        if s <= 0:
            continue
        # how many of the DISTINCT words typed in the query does this chunk hold (a word counts through its synonyms too)?
        hit = sum(1 for t, syn in groups if tfm.get(t) or any(tfm.get(x) for x in syn))
        cover = hit / float(len(groups))
        if MIN_SHARE and len(groups) >= 3 and cover < MIN_SHARE:
            continue
        s *= (1.0 - COVER_A) + COVER_A * cover
        # phrase bonus: reward a literal multi-word match
        if len(weights) > 1 and ql in (c["title"] + " " + c["content"]).lower():
            s *= 1.5
        scored.append((s, c))
    scored.sort(key=lambda p: -p[0])
    return scored[:top]


def describe(c):
    """One line: where a claim stands. Shown with every hit so a result can be weighed."""
    bits = ["status=" + c.get("status", "unclassified"), "verified_in=" + c.get("verified_in", "unclassified")]
    if c.get("date"):
        bits.append("date=" + c["date"])
    if c.get("superseded_by"):
        bits.append("superseded_by=" + c["superseded_by"])
    if c.get("parent"):
        bits.append("part of " + c["parent"])
    return "  ".join(bits)


def main():
    ap = argparse.ArgumentParser(description="Query the CRYENGINE project knowledge base.")
    ap.add_argument("query", nargs="*", help="free text")
    ap.add_argument("--corpus", default=DEFAULT_CORPUS)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--cat", help="restrict to a category (case, plural and old names are understood)")
    ap.add_argument("--status", choices=STATUSES, help="restrict to one status")
    ap.add_argument("--verified-in", dest="verified_in", choices=VERIFIED_IN, help="restrict to chunks verified in none / editor / packaged / partial")
    ap.add_argument("--include-superseded", action="store_true", help="also return chunks that have been replaced")
    ap.add_argument("--id", help="print one chunk by id")
    ap.add_argument("--list", action="store_true", help="list ids and titles")
    ap.add_argument("--cats", action="store_true", help="list categories")
    a = ap.parse_args()

    if not os.path.exists(a.corpus):
        sys.exit("corpus not found: " + a.corpus)
    chunks = load(a.corpus)

    if a.cats:
        counts = Counter(c["category"] for c in chunks)
        for k in CATEGORIES:
            print(f"{k:18} {counts.get(k, 0):4}  {CATEGORY_HELP[k]}")
        for k, v in sorted(counts.items()):
            if k not in CATEGORY_HELP:
                print(f"{k:18} {v:4}  (not in CATEGORIES - run build_corpus.py --check)")
        return

    if a.id:
        for c in chunks:
            if c["id"] == a.id:
                print(f"### {c['title']}  [{c['id']}]")
                print("    " + describe(c))
                if c.get("verified_note"):
                    print("    verified: " + c["verified_note"])
                print(c["content"])
                if c.get("history"):
                    print("\n--- HISTORY (kept for the record; may be wrong) ---")
                    print(c["history"])
                return
        sys.exit("no such id: " + a.id)

    if a.list:
        for c in chunks:
            if a.cat and not category_matches(c, a.cat):
                continue
            if a.status and c.get("status") != a.status:
                continue
            if not a.include_superseded and not a.status and c.get("status") == "superseded":
                continue
            print(f"{c['id']:34} {c['category']:16} {c['title']}")
        return

    if not a.query:
        ap.print_help()
        return

    if a.cat and canonical_category(a.cat) is None and not any(category_matches(c, a.cat) for c in chunks):
        sys.exit("unknown category %r; the categories are: %s" % (a.cat, ", ".join(CATEGORIES)))
    hits = search(chunks, " ".join(a.query), a.top, a.cat, a.include_superseded, a.status, a.verified_in)
    if not hits:
        print("no match. try --list to see what is in here.")
        return
    for score, c in hits:
        print("=" * 78)
        print(f"### {c['title']}")
        print(f"    id={c['id']}  category={c['category']}  score={score:.2f}")
        print("    " + describe(c))
        if c.get("source"):
            print(f"    source: {c['source']}")
        print()
        print(c["content"])
        print()


if __name__ == "__main__":
    main()
