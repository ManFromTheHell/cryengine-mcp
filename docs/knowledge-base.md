# Knowledge base

`rag/cryengine_project_rag.jsonl` is a searchable collection of short notes ("chunks") about
CRYENGINE 5.7.1 LTS: things that fail silently, how the editor and the engine's own tools behave,
procedures that were measured, and what each MCP tool does. The MCP tool `query_knowledge` and the
command-line program `rag/rag_query.py` search it. Neither needs the editor, the network or any
package outside the Python standard library.

## Read this first

- **It is one author's project history, not documentation of CRYENGINE.** Every chunk was written
  while building a particular game and its tools. Many chunks name that game's levels, assets and
  scripts, give paths from the author's machine (27 chunks name the author's
  engine source folder, and others carry drive paths of tool installs; the code no longer defaults to
  those paths, see [installation.md](installation.md)), or are written in the first person. Treat them as
  field notes. Some chunks paraphrase or quote Crytek documentation or engine source text; the
  licence of those sources was not checked here.
- **Measurements are for CRYENGINE 5.7.1 LTS on Windows.** Nothing was checked against another
  engine version.
- **A chunk is not a guarantee.** Most chunks have no recorded evidence level
  (`status: unclassified`, `verified_in: unclassified`). Where evidence was recorded, check it:
  `verified_in: none` means the claim was never seen working, and `partial` means only part of it
  was (see `verified_note`). Where a chunk and the engine's manual disagree, the author preferred
  the measurement; you should re-measure.
- **The search always returns something.** There is no "no answer" score, so a question the corpus
  does not cover still gets a confident-looking wrong top hit. Read the title and `verified_in`
  before acting on a result.

## Contents

The counts below were taken from the file in this repository when this page was written. Corpora
change, so recount rather than trusting them (command at the end of this section).

| field | values (chunk counts) |
|---|---|
| total | 538 chunks: 527 live, 11 superseded; 60 of them are children split from a longer chunk |
| `status` | measured 214, read-from-source 117, unclassified 196, superseded 11 (`hypothesis` is allowed but unused) |
| `verified_in` | unclassified 355, none 75, editor 42, packaged 38, partial 28 |

Categories (live and superseded chunks together; `python rag/rag_query.py --cats` prints the same
table with descriptions):

| category | chunks | contains |
|---|---|---|
| `trap` | 112 | something that fails silently or costs a build cycle, and the cure |
| `tooling` | 95 | what an MCP tool, helper script or bridge verb does and how to read its output |
| `playbook` | 71 | ordered recipes: how to do a whole job with these tools |
| `fact` | 44 | a measured or read-from-source fact, finding or negative result |
| `howto` | 36 | one measured procedure |
| `ai` | 33 | NPC brains, perception, factions, behaviour trees, UQS, Schematyc |
| `project` | 32 | decisions, policies, lessons, process, paths, machine environment |
| `level-craft` | 29 | terrain, vegetation, lighting, audio, particles, rendering, level construction |
| `engine-reference` | 25 | how the engine, the GameSDK sample and the templates are built |
| `gameplay` | 21 | player, physics, combat, pickups, game code |
| `navigation` | 19 | navmesh, off-mesh links, agent types, runtime updates |
| `animation` | 14 | Mannequin, blend spaces, clips, rigs, foot slide, ragdoll |
| `build-package` | 7 | building the game DLL, packaging, shaders for release |

To recount from the file (works from the repository root):

```python
import json, collections
rows = [json.loads(l) for l in open("rag/cryengine_project_rag.jsonl", encoding="utf-8") if l.strip()]
for field in ("category", "status", "verified_in"):
    print(field, collections.Counter(r[field] for r in rows).most_common())
```

## Chunk schema

One JSON object per line. `corpus_lib.py` enforces the rules.

| field | required | meaning |
|---|---|---|
| `id` | yes | lowercase letters, digits and hyphens; unique |
| `title` | yes | at most 140 characters; unique; write the symptom for a trap |
| `category` | yes | one of the 13 above |
| `tags` | yes | list of strings: the words a newcomer would type (weighted x3 in search) |
| `source` | yes | where the note came from (free text; may be empty) |
| `content` | yes | the note. Lint warns above 3000 characters; split into one-symptom chunks |
| `status` | yes | `measured`, `read-from-source`, `hypothesis`, `superseded` or `unclassified` (nobody recorded the evidence; this does not mean "wrong") |
| `verified_in` | yes | `none` (never run or seen), `editor`, `packaged`, `partial` or `unclassified` |
| `date` | yes | `YYYY-MM-DD` or empty |
| `superseded_by` | yes | id of the replacing chunk; required when `status` is `superseded`, empty otherwise |
| `verified_note` | no | what was and was not seen, for `partial` and for `measured` with `verified_in: none` |
| `history` | no | older text kept for the record |
| `parent` | no | id of the chunk this one was split from |

A superseded chunk is not returned by a search unless you ask for it. Old category names are still
accepted by the filter and by `corpus_lib` (`traps`, `how-to`, `gamesdk-ai`, `open-problem` and
others); `corpus_lib` stores the old name as the tag `cat:<old>`.

## Searching

### From the command line

Run from the repository root. Python 3.8 or newer is enough for this program.

```
python rag/rag_query.py "execute_python indentation error" --top 3
python rag/rag_query.py "terrain is black" --top 3
python rag/rag_query.py "NPC cannot find a path" --top 3
python rag/rag_query.py --cat trap --list
python rag/rag_query.py --verified-in none --list
python rag/rag_query.py --id trap-main-thread
python rag/rag_query.py --cats
```

Options: `--top N` (default 5), `--cat <name>` (case, plural and old names understood),
`--status <value>`, `--verified-in <value>`, `--include-superseded`, `--id <id>` (print one chunk,
with its history; a superseded chunk shows its replacement), `--list` (ids and titles), `--cats`,
`--corpus <path>` (search another file).

Top hits for the first three queries above, as run against this corpus:

| query | top hits (in order) |
|---|---|
| `execute_python indentation error` | `trap-python-whitespace`, `tool-python-script`, `trap-main-thread` |
| `terrain is black` | `trap-new-level-terrain-is-black-until-terrain-layers-bake`, `playbook-build-world-tools`, `trap-cave-floor-or-terrain-material-renders-black-on-a-mesh` |
| `NPC cannot find a path` | `ashvale-nav-indoors`, `playbook-navmesh`, `trap-npc-cannot-pass-a-door-or-gap-in-the-dungeon-agent-size` |

The search is keyword-based, so it can miss. A query phrased as `editor commands time out` returned
no chunk about timeouts in the top three. Rephrase with the words a note would use (`timed out
waiting for the main thread`, `modal dialog`) or filter with `--cat trap --list` and read titles.

### From an MCP client

```
query_knowledge(query="material renders black", top=4, category="", include_superseded=False)
```

`top` is clamped to 1-20. Each result carries `id`, `title`, `category`, `score`, `status`,
`verified_in`, `date`, `superseded_by`, `verified_note` and the full `content`. The tool's built-in
description still lists some pre-merge category names; the filter accepts them, but the 13 names in
the table above are the current ones.

### How ranking works

`rag_query.py` implements BM25 over the chunk body, the title (weight x4) and the tags (weight x3).
Filler words are dropped from the query. A small synonym table (`SYNONYMS` in the file) adds the
corpus's own words for common newcomer words ("bake" adds "regenerate", "generate", "export") at
weight x0.6. A chunk that matches more of the distinct query words ranks above one that matches a
single rare word, and a literal multi-word match gets a bonus. The weights and synonyms were tuned on
the dev question set and on the 76 self-graded questions, never on the hold-out set (below).

## Adding or changing a chunk

Only `corpus_lib.py` should write the corpus; do not edit the `.jsonl` by hand.

```python
import sys; sys.path.insert(0, "rag")
import corpus_lib as L

L.upsert(dict(
    id="trap-example-symptom",
    title="Symptom as the title, in the words a user would type",
    category="trap",
    tags=["words", "a newcomer", "types"],
    source="Where this was observed",
    content="What happens, why, and the cure.",
    status="measured",          # or read-from-source / hypothesis
    verified_in="editor",       # none / editor / packaged / partial
    date="YYYY-MM-DD",
))
print(L.validate().text())      # errors and lint warnings for the whole corpus
```

What `upsert` does, from the code:

- Fills missing optional fields, maps an old category name to a current one, and validates the
  whole corpus: required fields, types, unique ids and titles, title length, category, status and
  `verified_in` values, date format, and that `superseded_by` and `parent` point at existing ids.
- Refuses and writes nothing if any error remains, including an error elsewhere in the file.
  Passing `sync_now=False` validates and writes nothing, which makes a dry run.
- Copies the current file to `rag/backups/` (the newest 25 are kept) and then writes it atomically.
- Optionally writes a second, byte-identical "mirror" copy. The mirror path comes from the
  environment variable `CRYENGINE_CORPUS_MIRROR`; it is empty by default, and then only the corpus
  file in `rag/` is written. When the variable is set, the file it names must already exist (copy
  `rag/cryengine_project_rag.jsonl` to a folder you control first); otherwise the write raises
  `CorpusError` and nothing is written. `rag/build_corpus.py --check` also compares the mirror when
  one is configured.

Lint warnings (never blocking): an undated hypothesis or one older than 14 days, text that names a
chunk id that does not exist, a chunk over 3000 characters, and a `measured` chunk with
`verified_in: none` and no `verified_note`.

To retire a chunk, set `status: superseded` and `superseded_by` to the replacing id with
`upsert`; `L.remove(id)` refuses while another chunk uses the id as `parent` or `superseded_by`.
`rag/build_corpus.py` has `--check`, `--sync`, `--status`, `--readme` and `--all`; it regenerates
`rag/README.md` and a status chunk from the corpus and the code (the `--status` run rewrites the
status chunk in the corpus). Its checks include the mirror copy when `CRYENGINE_CORPUS_MIRROR` is set.

### Your own corpus

`query_knowledge` always reads `rag/cryengine_project_rag.jsonl` directly, never a mirror. To start a
corpus for your own project, replace that file with your own chunks (written with
`corpus_lib.upsert`, or a file that `python rag/build_corpus.py --check` accepts). The evaluation
files `knowledge_holdout.json` and `knowledge_dev.json` and the checks in
`tests/test_knowledge_eval.py` refer to the bundled chunks and will fail against a different
corpus.

## Evaluation

`catalogs/knowledge_eval.py` grades the search against question files whose expected
answers are chunk ids, not title substrings. It is editor-free and uses only the standard library.

```
python catalogs/knowledge_eval.py                      # hold-out and dev sets against the live corpus
python catalogs/knowledge_eval.py --file knowledge_holdout.json --verbose
python catalogs/knowledge_eval.py --rag path/to/rag_query.py --corpus path/to/corpus.jsonl   # before/after a change
python catalogs/knowledge_eval.py --json
```

| file | role | questions |
|---|---|---|
| `knowledge_holdout.json` | hold-out: real questions in the words of someone who has not read the corpus. Run for the record, never used to tune | 52 (47 graded, 5 marked as gaps) |
| `knowledge_dev.json` | dev: the set synonyms, stop words and weights are tuned on; shares no question with the hold-out | 48 |
| the list inside `tests/test_knowledge_eval.py` | self-graded regression check (written by the chunks' authors, graded by title substring); not an accuracy figure | 76 |

A question's `expected` is a list of acceptable chunk ids; `null` marks a gap (the corpus has no
answer), which is reported but not graded. A hit counts if its id, or the id of the chunk it was
split from, is in the list. Reported: rank-1, top-3 and top-5 rate and mean reciprocal rank over the
top 10.

One run of this repository's evaluation on the corpus above (538 chunks) gave:

| set | graded | rank-1 | top-3 | top-5 | MRR |
|---|---|---|---|---|---|
| hold-out | 47 | 70.2% | 95.7% | 97.9% | 0.824 |
| dev | 48 | 77.1% | 87.5% | 89.6% | 0.835 |

Read these as rough. The graders accept two to six chunk ids per question, so they are more lenient
than a strict single-answer grade. The figures move with every corpus change; run the evaluation
again before quoting them. The questions were written by the corpus's own author, in the context of one
game. `tests/test_knowledge_eval.py` also checks the corpus machinery (validation, superseded handling,
category aliases); its mirror and wrapper checks run only when `CRYENGINE_CORPUS_MIRROR` points at a
mirror folder and are skipped otherwise.
