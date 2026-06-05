---
name: dev-loop
category: core
description: Use when implementing any feature, fix, or content change — defines the complete development loop from implementation through testing, vibe verification, AI review, and metric-based keep/discard. Trigger when the user starts a development task, says "build this", "implement X", "fix this bug", "write this section", or when setting up a project's development workflow. Also use when reviewing whether a change is actually ready to ship, not just "tests pass". This skill ensures changes go through complete verification and are measured against the project's north-star targets before shipping. 中文触发：开发、实现、修复、写代码、改一下、做这个功能、加个功能、修个 bug、帮我写、帮我改、重构。
---

# Dev Loop

The complete development cycle: implement → test → vibe-verify → AI-review → measure → keep or discard.

Most workflows stop at "tests pass." But tests only verify what you thought to check. The gap between "all tests green" and "actually good" is real and often large. This loop closes it by adding vibe-verification (human judgment), AI review (objective alignment), and metric measurement (quantified keep/discard).

The design is inspired by autonomous research loops (like autoresearch): change something, measure, keep improvements, discard regressions, log everything, repeat. The difference is that software development has both quantifiable and subjective dimensions — so this loop blends automated measurement with human judgment.

This loop is governed by the core principles (`${CLAUDE_PLUGIN_ROOT}/references/principles.md`). In particular: **surface problems early** — each stage is a checkpoint that catches problems before they compound. And **quality over quantity** — running fewer, more deliberate iterations with meaningful measurements beats many fast cycles with noisy metrics.

## Two modes

The loop runs in two modes depending on the task:

**Metric-driven mode** — when the project has quantified north-star targets, the loop can run semi-autonomously. AI implements, tests, measures indicators, keeps improvements, discards regressions. Human reviews periodically but doesn't gate every iteration. Best for: optimization, refactoring, addressing tech debt, any task with clear metrics.

**Human-in-the-loop mode** — for work where subjective quality matters (UX, writing, API design), the human vibe-checks each iteration before proceeding. Best for: new features, UI work, documentation, anything where "feels right" is part of the definition of done.

Both modes share the same stages. The difference is who gates progression.

## The stages

```
┌──────────┐   ┌──────┐   ┌───────────┐   ┌───────────┐   ┌─────────┐
│ Implement ├──►│ Test ├──►│Vibe-Check ├──►│ AI Review ├──►│ Measure ├──► Keep / Discard
└──────────┘   └──┬───┘   └─────┬─────┘   └─────┬─────┘   └────┬────┘
                  │             │               │              │
                  └─────────────┴───────────────┴──────────────┘
                                 Loop back if issues
```

### Stage 1: Implement

Write the code, text, or artifact. Follow the project's conventions. Keep the north-star targets in mind — they're the filter for design decisions.

Before implementing, git commit (or snapshot) the current state. This is your rollback point — if the change turns out to be a regression, you need a clean state to return to (like autoresearch's git reset on failed experiments).

### Stage 2: Test

Run the project's automated test suite. If tests don't exist for this change, write them.

Tests answer: **"Does it do what I said it should do?"**

**Move forward when:** Tests pass and you've covered the important paths.

**Loop back if:** Tests fail. Fix and re-test. Don't advance with broken tests.

### Stage 3: Vibe-verify

Actually experience the change as a user, reader, or consumer would. This stage catches **unspecified behavior** — things nobody wrote requirements for but everyone notices when they're wrong.

What vibe-verification looks like depends on the project:

| Project type | What to do |
|---|---|
| **Frontend** | Open browser, click through flow, try edge cases, resize, keyboard nav |
| **Backend API** | Hit endpoints with curl, check response shape, try malformed input |
| **CLI tool** | Run on real input, check output, try wrong arguments |
| **Library** | Write a small consumer script — is the API ergonomic? |
| **Paper / docs** | Read as if for the first time — does it flow? |
| **Data pipeline** | Run on sample data, eyeball output, check row counts |

**In metric-driven mode:** AI can propose what to vibe-check and self-verify where possible (e.g., screenshot comparison, response shape validation). Human vibe-check happens periodically, not every iteration.

**In human-in-the-loop mode:** Human does the vibe-check each time. AI prompts: "Tests pass. Here's what I'd check — [list]. Can you confirm?"

**Move forward when:** Nothing feels broken or off. Feelings are data.

### Stage 4: AI review

Systematic check against the project's north-star targets and conventions. Not a traditional code review — an **objective-alignment audit**.

For each north-star target:
- Does this change maintain or improve our standing?
- Does it introduce any regression?
- Is there an obvious improvement we're leaving on the table?

Also check:
- **Consistency** — matches the rest of the codebase in naming, patterns, style
- **Scope** — is the change minimal? No scope creep or speculative abstractions?
- **Secondary criteria** — does it pass the simplicity tiebreaker? ("All else being equal, simpler is better")

### Stage 5: Measure

This is the stage that makes the loop quantitative. Run the project's observations and compare to baseline (or to the previous measurement).

Every north-star target has an observation mechanism — script, agent, or human (see `core/north-star/references/observability.md`). Run them according to their frequency:

- **Script observations** — every iteration. Cheap, deterministic.
- **Agent observations** — periodically, or when script metrics plateau. Captures qualitative aspects (test quality, code clarity, requirement-code mapping).
- **Human observations** — at milestones or when agent assessment flags uncertainty.

```bash
# Example: measure after a change to a Python backend
radon cc src/ -a -nc                          # script: complexity
pytest --cov=src --cov-report=term-missing    # script: coverage
vulture src/                                  # script: dead code
# + agent observation if scheduled this iteration (e.g., test quality review)
```

Compare against the last recorded values. The outcome determines what happens next:

**Keep** — indicators improved or held steady, and no stage found issues.
Git commit. Update the progress log. This is now the new baseline.

**Discard** — indicators regressed (unless a higher-priority target improved to compensate).
Git reset to the snapshot from stage 1. Log the attempt with "discard" status. Think about why it regressed before trying a new approach.

**Investigate** — mixed results (one target up, another down), or the change is too small to measure.
Look at the trade-off. If the improvement on the primary target outweighs the regression on a secondary one, keep. Otherwise discard. When in doubt, prefer the simpler code.

### After the keep/discard decision

Log the iteration outcome for skill-evolve telemetry (Layer 2 — agent self-report):

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/skill-evolve/scripts/log_usage.py \
  --skill dev-loop --project <project-name> \
  --outcome <keep|discard> --stages "5/5" \
  --notes "<one-line summary of what changed>"
```

This is best-effort — if the conversation ends before this step, the PostToolUse hook (Layer 1) still captures that dev-loop was triggered. But when you do reach this point, log it: the data feeds into the skill health report and helps identify optimization opportunities.

## The experiment log

Track every iteration — successful or not. A simple TSV (like autoresearch's `results.tsv`):

```
date	commit	status	indicator_1	indicator_2	indicator_3	description
2026-04-12	a1b2c3d	baseline	4.7	62%	14	initial measurement
2026-04-12	b2c3d4e	keep	4.5	65%	12	refactored auth module
2026-04-12	c3d4e5f	discard	4.8	64%	12	tried extracting base class (added complexity)
2026-04-13	d4e5f6g	keep	4.3	68%	10	added error handling tests
```

This log serves three purposes:
1. **Progress visibility** — you can plot the trend and see the line improving
2. **Institutional memory** — what was tried and why it was kept or discarded
3. **Debugging** — if a metric suddenly regresses, check what changed since the last "keep"

Store the log at the project root (e.g., `progress.tsv`) or wherever CLAUDE.md specifies. Keep it human-readable.

## Adapting the intensity

Not every change needs every stage at full intensity:

| Change type | Stages to run | Notes |
|---|---|---|
| **Trivial** (typo, config) | Test → Measure | Skip vibe + review |
| **Bug fix** | Full loop | Bugs have cousins — check everything |
| **New feature** | Full loop, human-in-the-loop | Subjective quality matters |
| **Refactoring** | Test → AI review → Measure | Behavior shouldn't change, so vibe-check is lighter |
| **Performance opt** | Full loop + benchmark | Add timing indicator to the measurement |
| **Paper section** | Vibe → AI review → Measure | "Measure" = run the LLM grading prompt |
| **Optimization sprint** | Metric-driven, autonomous | Many iterations, human reviews at intervals |

## Loop-back decision guide

| Issue type | Go back to | Rationale |
|---|---|---|
| Test failure | Stage 2 | Fix and re-verify everything after |
| Cosmetic vibe issue | Stage 3 | Small fix, no re-test needed |
| Structural vibe issue | Stage 2 | Structural change might break tests |
| AI review nit | Stage 4 | Fix and re-review |
| Metric regression | **Discard and rethink** | Don't patch a regression — the approach may be wrong |
| Iteration 4+ on same task | **Stop. Step back.** | Something is structurally wrong — rethink the approach |

## Autonomous operation

In metric-driven mode, the loop can run without human intervention — the same way autoresearch runs overnight. The key requirements:

1. **Clear metrics** — north-star targets with measurable indicators
2. **Automated measurement** — a command or script that produces the indicator values
3. **Keep/discard logic** — improved → keep, regressed → discard, mixed → investigate
4. **Experiment log** — every iteration recorded
5. **Rollback mechanism** — git commit before each change, git reset on discard

A typical autonomous session:
```
AI: [reads north-star targets and current baselines]
AI: [implements change #1] → tests pass → measures → improved → keep → logs
AI: [implements change #2] → tests pass → measures → regressed → discard → logs
AI: [implements change #3] → tests fail → fixes → re-tests → measures → improved → keep → logs
... (continues indefinitely until interrupted or stuck)
```

Human reviews the experiment log periodically and can redirect ("stop optimizing complexity, focus on coverage instead") or stop the loop.

## Connection to other skills

- **north-star** defines the targets and indicators that stage 5 (measure) checks against
- **north-star/observability** defines the observation mechanisms (script/agent/human) and how to design them — especially agent-assessed observations for qualitative targets
- **auto-harness** wires this skill into new projects, inventories observables, and sets up the initial measurement commands
- **notify** sends iteration reports after the measure stage — configure `send_on` rules in `notify.json` to control when reports fire
- **CLAUDE.md managed section** contains the project-specific dev-loop stage commands (test, lint, measure). Auto-harness generates this from domain templates during project setup — agents read commands directly from CLAUDE.md, not from separate profile files
- The experiment log format (`progress.tsv`) is standardized so tools can parse and visualize it — its location is specified in CLAUDE.md under "Iteration tracking"
