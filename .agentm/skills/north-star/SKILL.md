---
name: north-star
category: core
description: Use when starting a new project, onboarding to an existing codebase, or whenever priorities feel unclear — helps define 1-3 quantifiable optimization targets that drive all development decisions and enable automated progress tracking. Trigger whenever the user mentions project goals, priorities, quality metrics, optimization targets, "what should we focus on", "what matters most", or when auto-harness needs to establish purpose-driven direction during project setup. Also use when evaluating whether a task or PR aligns with what the project actually cares about. 中文触发：目标、指标、优先级、分析代码、评估项目、关注什么、衡量标准、质量、方向、重点。
---

# North Star

Every project needs 1-3 optimization targets with quantifiable indicators. Without them, development drifts — you optimize for whatever the last request was instead of what actually matters.

The key insight from autonomous research systems (like autoresearch): when you have a clear metric, you can build a loop around it — change something, measure, keep or discard, repeat. Vague objectives ("be maintainable") don't drive loops. Quantified targets ("cyclomatic complexity < 10 per function") do.

## Anatomy of a target

Each north-star target has five parts:

```
Target:     What you're optimizing for (one sentence)
Indicator:  A number or checkable condition you can measure repeatedly
Mechanism:  How to observe it — script | agent | human
Baseline:   The current value — established before any optimization
Constraint: What's held constant so measurements are comparable
```

**Example (backend service — script mechanism):**
```
Target:     Maintainability — any module understandable within a day
Indicator:  Avg cyclomatic complexity per function
Mechanism:  script — `radon cc src/ -a -nc`
Baseline:   4.7 (measured 2026-04-12)
Constraint: Same source tree, same radon config
```

**Example (academic paper — agent mechanism):**
```
Target:     Logical coherence — no reasoning gaps between paragraphs
Indicator:  LLM coherence score (1-10, with gap list)
Mechanism:  agent — structured grading prompt (see LLM-as-judge section)
Baseline:   6.2 (draft v1, 2026-04-12)
Constraint: Same grading prompt, same model, full section as input
```

**Example (test suite — agent mechanism):**
```
Target:     Test quality — every test has a clear reason to exist
Indicator:  % of tests that directly verify a requirement
Mechanism:  agent — "For each test, identify which requirement it verifies.
            Flag tests that duplicate coverage or test implementation details."
Baseline:   [assess after first test suite]
Constraint: Same review prompt, same requirement index
```

The constraint envelope is critical — without fixed conditions, you can't compare measurements across time. Autoresearch uses a fixed 5-minute training budget so every experiment is directly comparable. Apply the same principle: if you change the measurement conditions, you invalidate all previous baselines.

## Guiding principle

Before defining targets, read `${CLAUDE_PLUGIN_ROOT}/references/principles.md` — especially **quality over quantity**. A small number of meaningful targets with genuine observation mechanisms beats a long list of metrics nobody acts on. The same principle applies to the observations themselves: don't add a check unless you know what decision it informs.

## Observability: the prerequisite

Before defining targets, inventory what the project can actually observe. A target without a corresponding observation mechanism is a wish, not an optimization objective. If you can't measure it, you can't iterate on it.

### Observation mechanisms

Every observable has a mechanism — how you collect the measurement:

| Mechanism | What it is | When to use | Example |
|-----------|-----------|-------------|---------|
| **script** | Run a command, get a number | Deterministic, repeatable metrics | `pytest --cov`, `radon cc`, `validate_index.py` |
| **agent** | Structured prompt to a subagent | Qualitative assessment that needs understanding | "Review each test — does it verify a requirement? Is it the most direct way?" |
| **human** | Ask the user | High-stakes or truly subjective judgment | "Does this API feel intuitive to use?" |

These are not quality tiers — an agent-assessed observation is just as valid as a script-measured one, as long as the constraint envelope is fixed (same prompt, same model, same input scope). The key requirement is **repeatability**: you must be able to run the same observation again after a change and get a comparable result.

### Inventory workflow — MANDATORY user confirmation gate

During project setup, you MUST present your scan findings to the user and get explicit confirmation for each of the four questions below. DO NOT proceed to target definition or baseline measurement until the user has responded to all four. Present them one at a time or as a numbered list, but WAIT for the user's answer before moving on.

For each question, show what you discovered from the scan first ("Here's what I found: ..."), then ask what you missed ("What else is there that I can't see from the repo?").

1. **What can we observe automatically?**
   Present: tools you detected (linter, type checker, test runner, CI pipeline, coverage, build scripts).
   Ask: "Are there other automated checks I missed? Any internal scripts, dashboards, or CI steps I can't see from the repo?"

2. **What needs agent judgment?**
   Present: areas where scripts can't measure quality (test relevance, code clarity, design coherence).
   Ask: "Which of these matter for this project? Are there other qualitative aspects you care about?"

3. **What needs human judgment?**
   Present: candidates for human-only assessment (UX feel, API ergonomics, architectural direction).
   Ask: "What do you want to keep in your hands — what should I escalate rather than decide?"

4. **From these observables, what matters most?**
   Present: a ranked draft based on answers 1-3.
   Ask: "Does this priority order match your intuition? What would you move up or drop?"

**Hard rule:** if the user hasn't confirmed these four points, you do NOT have enough information to define targets. Writing targets or baselines without this confirmation produces guesswork that looks like agreement — the worst outcome.

The principle: **targets are composed from observables, not the other way around.** Don't pick a target and then scramble to find a way to measure it. Start from what you can see, and build your optimization objectives on that foundation.

For a detailed framework on designing observations (especially agent-assessed ones), see `references/observability.md`.

## How to find targets

Ask these three questions — answers usually cluster into 1-3 themes:

1. **"If this project could only be great at one thing, what would it be?"**
   Forces prioritization. The answer is almost always the real north star.

2. **"What would make someone say 'this is well-done'?"**
   Shifts perspective from builder to consumer.

3. **"What number, if you saw it improving week over week, would make you confident the project is on track?"**
   Forces quantification. If you can't name a number, the target isn't concrete enough yet.

Then for each target, nail down the indicator. The indicator doesn't have to be a perfect measure — it just has to move in the right direction when things improve and the wrong direction when they regress. A proxy that's 80% correlated with what you care about is infinitely better than no measurement at all.

## Quantifiable indicators by project type

These are starting points — every project should customize.

**Backend service:**
| Target | Indicator | Tool / Method |
|---|---|---|
| Maintainability | Avg cyclomatic complexity per function | `radon cc -a` |
| Maintainability | Max function length (lines) | `wc -l` + grep |
| Reliability | % of error paths with explicit handling | grep for bare `except:` / unchecked errors |
| Reliability | Test coverage on error paths | `pytest --cov` + manual review |
| Minimalism | Dead code ratio | `vulture` or manual audit |
| Performance | p95 response time (ms) | load test with `wrk` / `hey` |

**Frontend application:**
| Target | Indicator | Tool / Method |
|---|---|---|
| UX quality | Lighthouse performance score | `lighthouse` CLI |
| UX quality | Core Web Vitals (LCP, CLS) | Chrome DevTools / `web-vitals` |
| Accessibility | Lighthouse accessibility score | `lighthouse` CLI |
| Component clarity | Avg component size (lines) | `wc -l src/components/**/*.tsx` |
| Bundle efficiency | Total bundle size (KB) | `next build` / `vite build` output |

**Academic paper:**
| Target | Indicator | Tool / Method |
|---|---|---|
| Logical coherence | LLM coherence score (1-10) | Structured grading prompt (see below) |
| Clarity | Flesch-Kincaid grade level | `textstat` python library |
| Clarity | LLM clarity score (1-10) | "Rate clarity for adjacent-field reader" |
| Reproducibility | Method specification checklist (% complete) | Manual checklist |

**Data pipeline:**
| Target | Indicator | Tool / Method |
|---|---|---|
| Correctness | Row count delta vs expected | `wc -l` + assertion |
| Correctness | Schema validation pass rate | `great_expectations` / `pandera` |
| Observability | Time to diagnose last failure (minutes) | Post-incident log |
| Idempotency | Output diff on re-run (should be 0) | `diff` output files |

**Open-source library:**
| Target | Indicator | Tool / Method |
|---|---|---|
| API ergonomics | Lines-of-code for common use case | Example scripts |
| Documentation | % public symbols with docstrings | `interrogate` or custom script |
| Test coverage | Line + branch coverage | `pytest --cov` / `nyc` |
| Backward compat | Breaking changes per release | Changelog audit |

### LLM-as-judge grading prompt (for subjective targets)

When a target is inherently subjective (like paper coherence), use a structured LLM prompt as the indicator. The key is to use the **same prompt consistently** so scores are comparable:

```
Rate the following section on logical coherence from 1-10.

Criteria:
- Does each paragraph follow logically from the previous?
- Are there reasoning gaps where the reader has to fill in steps?
- Are transitions explicit rather than assumed?

Score 1-3: Major gaps, hard to follow
Score 4-6: Some gaps, reader needs to re-read parts
Score 7-9: Flows well, minor issues
Score 10: Flawless logical chain

Section:
---
{text}
---

Respond with:
Score: X
Gaps found: [list each gap]
Strongest transition: [quote]
Weakest transition: [quote]
```

Pin the model version in the constraint envelope. Different models score differently.

## Establishing a baseline

Before optimizing, measure where you are now. This is the equivalent of autoresearch's "first run is always the baseline." Without a baseline, you can't tell if changes are improvements or regressions.

```bash
# Example: establish baseline for a Python backend
radon cc src/ -a -nc         # avg complexity
pytest --cov=src --cov-report=term-missing  # coverage
vulture src/                 # dead code
```

Record the baseline values with a date:

```markdown
## Baseline (2026-04-12)
| Target | Indicator | Value |
|---|---|---|
| Maintainability | Avg cyclomatic complexity | 4.7 |
| Maintainability | Max function length | 89 lines |
| Reliability | Test coverage (error paths) | 62% |
| Minimalism | Dead code items | 14 |
```

## Writing targets in CLAUDE.md

Put targets at the very top so every session sees them immediately:

```markdown
## North-star targets

1. **Maintainability** — avg cyclomatic complexity < 4.0 (currently 4.7)
   Measure: `radon cc src/ -a -nc`

2. **Reliability** — error path coverage > 85% (currently 62%)
   Measure: `pytest --cov=src` + review uncovered error branches

3. **Minimalism** — zero dead code items (currently 14)
   Measure: `vulture src/`
```

The format `target description (currently X)` makes progress visible at a glance. When the number moves, update it.

## Secondary criteria

Beyond the primary targets, most projects benefit from one secondary principle that acts as a tiebreaker. Autoresearch uses "all else being equal, simpler is better" — a 0.001 improvement that adds 20 lines of ugly code isn't worth it, but a 0.001 improvement from deleting code definitely is.

Good secondary criteria:
- **Simplicity** — if two approaches give similar metrics, prefer the one with less code
- **Consistency** — if two patterns give similar metrics, prefer the one already used in the codebase
- **Reversibility** — if uncertain, prefer the change that's easier to undo

Write the secondary criterion alongside the targets in CLAUDE.md.

## Using targets in the dev-loop

Targets connect to dev-loop at two points:

**Keep/discard gate.** After a change, measure the indicators. If they improved (or held steady), keep. If they regressed, investigate — maybe discard, maybe the regression is acceptable if a higher-priority target improved. This is the automated heartbeat of the loop.

**AI review stage.** The AI review in dev-loop checks each change against these targets. With quantified indicators, the review becomes "did cyclomatic complexity increase?" rather than "does this feel maintainable?" — much more actionable.

## Progress tracking

Record indicator values over time so you can visualize progress. A simple TSV works:

```
date	commit	complexity	coverage	dead_code	notes
2026-04-12	a1b2c3d	4.7	62%	14	baseline
2026-04-13	b2c3d4e	4.5	65%	12	refactored auth module
2026-04-14	c3d4e5f	4.3	68%	10	added error handling tests
```

This log serves the same role as autoresearch's `results.tsv` — it turns progress from a feeling into a visible trend. When you can see the line going down (or up, for coverage), you know the loop is working.

## When to revise targets

Targets aren't permanent, but changing them is a deliberate act:

- **After a major milestone** — targets that mattered pre-launch may not matter post-launch
- **When a target is consistently achieved** — if complexity has been < 3.0 for months, retire it and pick a new frontier
- **When two targets consistently conflict** — resolve the conflict or adjust priority order
- **When the indicator stops correlating with what you care about** — the number improves but the project doesn't feel better? The indicator is wrong, find a new one

Don't revise mid-task. Finish the current work, then reassess.

## References

| Reference | When to read |
|-----------|-------------|
| `references/observability.md` | Designing observations — especially agent-assessed ones, observation-driven refactoring |

## Connection to other skills

- **auto-harness** uses this skill during project setup to inventory observables, define targets, and establish baselines — targets and measurement commands are written to the CLAUDE.md managed section
- **dev-loop** uses these targets in the keep/discard gate and AI review stage
- **CLAUDE.md managed section** contains the project-specific measurement commands for each target. Auto-harness generates this from domain templates (e.g., `domains/softdev/templates/north-star-template.md`) during project setup
