# Excavation Methodology

How each layer of the dig report is computed, and what the numbers mean. Read this when you
need to defend a claim, tune the analysis, or reproduce results manually.

## 1. Change hotspots (git log mining)

The excavator parses `git log --no-merges --numstat` and aggregates per file:

- **commits** — how many times the file was touched (survivorship of editing pressure)
- **churn** — total added + deleted lines
- **bugfix ratio** — share of touching commits whose message matches
  `fix|bug|hotfix|patch|revert|crash|regression|workaround`
- **authors** — distinct authors; a file touched by many authors is shared knowledge
- **first / last touched** — the file's lifetime

Interpretation: commits ≈ interest, churn ≈ pain, bugfix ratio ≘ defect density proxy
(this is the well-documented "change history predicts defects" result — modules that change
often and badly keep changing badly).

## 2. Dependency graph

Import statements are extracted per language and resolved to files inside the repo only
(external packages are counted separately from manifests):

| Language | Signal |
|---|---|
| Python | `import x.y`, `from .rel import` (resolves packages via `__init__.py`) |
| JS/TS/Vue/Svelte | `import … from`, `require()`, dynamic `import()`, `@/` alias → root |
| Java/Kotlin/Scala | `import a.b.C;` (exact dotted path, then unique class-name match) |
| Go | import block + `go.mod` module prefix |
| C# | `using` namespaces |
| Ruby | `require_relative` / `require` |
| PHP | `use` (PSR-4 guess), `include/require` |
| C/C++ | `#include "…"` |

Derived measures:

- **fan-in** — how many files import me = blast radius. The real architecture of a legacy
  codebase is its fan-in ranking, not the folder names.
- **fan-out** — how many files I import = coupling surface / change amplification.
- **cycles** — strongly connected components (Kosaraju). Any SCC with >1 file means those
  files must be understood together; they are effectively one big file with bad tooling.
- **zombies** — ≤1 commit, fan-in 0, not an entry point, ≥40 loc: nothing depends on them.

External dependencies are harvested from `package.json`, `requirements*.txt`, `pyproject.toml`,
`go.mod`, `pom.xml`, `Gemfile`, `composer.json`, `Cargo.toml`, `*.csproj`.

## 3. Domain glossary (comment mining)

From comments and docstrings (`//`, `#`, `/* */`, `""" """`, `<!-- -->`, …) the miner extracts:

- Capitalized multi-word phrases ("Settlement Engine")
- CamelCase identifiers mentioned in prose ("settlementQueue")
- SCREAMING_SNAKE constants referenced in comments

A term enters the glossary when it appears ≥3 times. The output table shows mentions, file
spread, and one example comment. This is a proxy for the team's tribal vocabulary — the words
that appear in comments but rarely in onboarding docs. Filter out programming stopwords first;
whatever survives is domain.

The same pass harvests the **wall of legacy comments**: TODO / FIXME / HACK / XXX / WORKAROUND
/ DEPRECATED and warnings like "DO NOT TOUCH". These are oral history — each one marks a
graveyard (a bug that resisted fixing) or a booby trap (an invariant nobody documented).

## 4. Refactoring risk map

For every current code file:

```
risk = 100 × ( 0.28·norm(commits) + 0.22·bugfix_ratio + 0.18·norm(loc)
             + 0.22·norm(fan_in) + 0.10·(no matching test) )
```

All `norm()` are `log1p(x)/log1p(max)` — logarithmic, so one mega-file doesn't flatten the
rest. A "matching test" means a sibling file named `test_x`, `x_test`, `x.test`, `x.spec`, …

The quadrant map (Michael Feathers, *Working Effectively with Legacy Code*) splits files on
median change frequency × median size:

| | big | small |
|---|---|---|
| **hot** | Refactor first (red) — hot + heavy + (often) untested = blast zone | Workhorses (gold) — hot but cheap, keep them small |
| **cold** | Landmines (orange) — feared and frozen; characterize before touching | Backwaters (blue) — ignore |

Read the map as a strategy, not a scoreboard: red = where tests buy the most safety, orange =
where knowledge is evaporating fastest (nobody touches it, so nobody remembers it), gold =
guard rails for code review, blue = deletion backlog.

## Manual reproduction (no script)

```bash
# hotspots
git log --no-merges --numstat --format= | awk -F'\t' '{add[$3]+=$1; del[$3]+=$2; n[$3]++}
  END {for (f in n) printf "%5d %8d %8d %s\n", n[f], add[f], del[f], f}' | sort -rn | head -20

# bus factor
git log --format=%an | sort | uniq -c | sort -rn | awk 'NR==1{top=$1} {s+=$1} END {print top/s}'

# bugfix pressure per file (last year)
git log --since=1.year --no-merges --numstat --format=%s | awk 'tolower($0) ~ /fix|bug/ {getline; print}' | sort | uniq -c | sort -rn | head

# fan-in (JS/TS example)
grep -rE "from ['\"]\./|require\(['\"]\./" src --include='*.ts' -h | grep -oE "['\"][^'\"]+['\"]" | sort | uniq -c | sort -rn | head
```

## Limits (be honest about these)

- Comment-mined glossaries pick up noise; the stopwords list is English-centric.
- Dependency resolution is heuristic — dynamic imports, DI containers, and reflection are
  invisible to static grep.
- Risk weights are opinionated priors, not measurements. Show the underlying signals
  (commits, bugfix %, fan-in, tests) next to any score.
- History only covers what git knows: squashed histories and vendored code distort results.
