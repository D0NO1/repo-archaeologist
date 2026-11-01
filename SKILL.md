---
name: repo-archaeologist
description: Excavate a legacy codebase into an interactive report with change hotspots, dependency graph, domain glossary and refactoring risk map. Use when onboarding to an unfamiliar repo, auditing before refactoring, or repo archaeology. Not for single-file explanations.
---

# Repo Archaeologist

You are excavating an undocumented codebase so a developer can take it over safely. Do not
explain files one by one — produce a dig report: where the bodies are buried, who buried them,
and what explodes first if touched.

## Workflow

1. **Run the excavator.** The repo must be a git checkout. From the repo's parent directory:

   ```bash
   python <skill-dir>/scripts/analyze.py <repo-path> -o <repo-path>-archaeology
   ```

   Requires Python 3.8+ and git. No other dependencies. Useful flags:
   - `--since YYYY-MM-DD` — restrict history mining (huge monorepos)
   - `--exclude "*.gen.*"` — extra ignore globs (repeatable)
   - `--notes notes.md` — embed your written analysis into the report

2. **Read the data.** Open `<outdir>/data.json`. Read `meta`, then the top ~15 entries of
   `hotspots` (already sorted by risk), `risk.quads`, `risk.zombies`, `deps.cycles`, and
   `glossary`. Do not dump this into chat — synthesize it.

3. **Deliver the narrative** (this is your real value — the HTML is the artifact). Present:
   - **Headline**: repo age, size, bus factor (top contributor share), test coverage proxy.
   - **The blast zone**: 3-5 files that combine high churn, high fan-in and low test presence.
     For each, one sentence on why it is dangerous and what to do first.
   - **The power grid**: hub files with highest fan-in — small, stable, load-bearing. Touch
     them carefully.
   - **The minefield**: circular dependency chains and zombie files (safe deletion list).
   - **The tribal language**: 5-10 domain terms from the glossary with their mined examples —
     this is the fastest way to sound native in code review.
   - **A reading order** for the new developer: 5 files max, in the order they should be read,
     derived from hubs + hotspots.
   - **A 30/60/90 plan**: characterize-test the blast zone → cut zombies → break cycles.

4. **Write the notes file and re-run** with `--notes` so the report carries your narrative
   (headings, bold, lists and inline code are supported in notes).

5. **Open the report.** Serve or open `<outdir>/archaeology-report.html` in a browser; it is
   fully self-contained (no server needed).

## Fallback (script unavailable)

If Python or the script cannot run, produce a manual dig with these git commands and the
methodology in [references/methodology.md](references/methodology.md):

```bash
git -C <repo> log --no-merges --format="%ad" --date=short | head -1        # first dig date
git -C <repo> log --format="%an" | sort | uniq -c | sort -rn | head -5     # bus factor
git -C <repo> log --no-merges --numstat --format= | awk '{print $3}' | \
  sort | uniq -c | sort -rn | head -20                                     # hotspots
git -C <repo> log --since=1.year --format="%s" | grep -icE "fix|bug"       # bugfix pressure
grep -rnE "TODO|FIXME|HACK|XXX" <repo> --include="*.py" --include="*.js"   # wall of comments
```

Then build the report sections by hand from this output plus your own file reading.

## Guardrails

- Never modify the target repository. The excavator only reads git and files.
- Monorepos over ~50k commits can be slow: use `--since` and `--exclude` first.
- Risk scores are heuristics, not verdicts — frame them as "signals", and always pair a risk
  call-out with the evidence (commits, bugfix ratio, fan-in, test presence).
- If the repo is young or tiny, say so plainly instead of inventing archaeology.
