# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository state

This repo is a **Claude Code plugin** ([.claude-plugin/plugin.json](.claude-plugin/plugin.json),
plus a self-listing [marketplace.json](.claude-plugin/marketplace.json) making the GitHub
repo directly installable). The `deep-code-audit` skill lives at
[skills/deep-code-audit/SKILL.md](skills/deep-code-audit/SKILL.md) (orchestrator) together
with its [references/](skills/deep-code-audit/references/) (task-prompt skeletons + schema
spec) and [scripts/](skills/deep-code-audit/scripts/) (4 Python scripts + 67 unit tests,
standard library only); [agents/](agents/) (2 dedicated subagent definitions) sits at the
**plugin root** — the spec's fixed component location (only plugin.json belongs inside
`.claude-plugin/`). The single design document (Korean) is
[.claude/docs/deep-code-audit-design.md](.claude/docs/deep-code-audit-design.md) — it
consolidates and supersedes the former workflow review, development plan, and improvement
plan, and describes the design as implemented plus the remaining milestones (M3 eval
fixture + scoring script, M4 measurement/tuning).

The hunter and verifier are **dedicated subagents** (`agents/deep-audit-hunter.md`,
`agents/deep-audit-verifier.md`): their invariant protocol lives in the agent **body**
(= system prompt, loaded from disk by the harness) rather than being passed through the
orchestrator's context. The orchestrator spawns them by `subagent_type` with a lean
task prompt (run variables only) built from `references/hunt-task.md` / `verify-task.md`.
This is the single source of truth — there is no combined body+task file; compatibility
mode concatenates the two on the fly only under explicit user consent (see Stage 0).

Install is **plugin-first**: `~/.claude/skills/deep-code-audit` is a **symlink to this
repo root**, and since the repo carries `.claude-plugin/plugin.json`, the skills-dir scan
loads it as plugin `deep-code-audit@skills-dir` (Claude Code v2.1.142+) — discovered in
place, so edits here are live with no sync step — shipping the skill and both agents
together under plugin-scoped identifiers (`deep-code-audit:deep-audit-hunter` /
`deep-code-audit:deep-audit-verifier`). The legacy **agents symlink**
`~/.claude/agents/deep-code-audit → <repo>/agents` (user-scope agents dir is recursively
scanned; identifier = frontmatter `name`, unscoped) still works and currently coexists as
a transition safety net — SKILL.md Stage 0a resolves whichever identifier form the session
lists (preferring unscoped when both appear); drop the agents symlink once the plugin
route is confirmed in a fresh session. Others install via
`/plugin marketplace add posimind/deep-code-audit`. Agents installed after a session
starts are not recognized until restart — Stage 0 preflight checks this. (A field-test run
against the ssam repo, 2026-07-02, produced the problem-analysis report that motivated the
Rust-parser improvement round; its run directory is no longer kept in this repo.)

### Commands

- **Unit tests** (67, no external deps): `python3 skills/deep-code-audit/scripts/test_scripts.py`
- **Compile check**: `python3 -m py_compile skills/deep-code-audit/scripts/*.py`
- **Plugin manifest check**: `claude plugin validate . --strict`

There is no build step or linter configured. The scripts do only deterministic work; all
judgment (project classification, defect scoring) lives in the prompts — the hunter/verifier
detection & rubric protocols in the agent bodies under `agents/`, and the orchestrator's
in-model steps plus task/report skeletons under the skill's `references/`.

## Purpose

`deep-code-audit` is a **Claude Code Skill**: a multi-agent workflow that audits a target
codebase for defects (security, concurrency, fault handling, logic, resource) using
adversarial detection followed by adversarial verification, then produces a structured
Korean-language report. It is designed to run on the Fable model, prioritizing
critical/major defects over minor ones with minimal false negatives and false positives.

## Architecture (as implemented)

The pipeline hands off state between stages **via files on disk** (under
`<target>/.deep-code-audit/<run-id>/`), not in-context, so each stage can be run by a
separate subagent and completed stages can be skipped on resume:

```
[0] Preflight         confirm dedicated agent defs exist (deterministic) + are recognized this
                       session (in-model) BEFORE creating the run dir; canary-spawn both types
                       after; abort by default on failure (compat mode only on explicit consent);
                       record preflight/mode.json (dedicated|compat)
[1] Select & group    audit brief (in-model: project purpose, lens priority, high-risk areas)
                       → select_targets (core / low / exclude) → in-model classification review
                       → group_by_lines build (line-count balance + import-graph cohesion,
                         default budget 10K lines; cohesion-degradation detection)
                       ──▶ groups.json
[2] Adversarial hunt  one subagent per group (4-6 concurrent, background):
                       - may READ any file in the repo for cross-file context
                       - may only REPORT findings whose defect line lives in its own group
                       - asymmetric recording; per-file coverage evidence
                       ──▶ defects/<group_id>.json
[2.5] Reinforcement   critical-only second-pass hunter on high_risk groups FIRST (merged so
                       its cross_refs join routing); then route unconsumed cross_refs hints
                       to owning-group sweep agents; independent outputs merged by script
                       (dedupe + ID/superset checks + cross_refs preservation); finally
                       route-hints --residue-check records hints left unconsumed after the
                       single sweep round (surfaced in the report, never silently dropped)
                       ──▶ defects/<group_id>.json (updated) + hints/residue.json
[3] Adversarial verify fresh-context verifier per group; anti-anchoring protocol
                       (claims embedded without rationale; re-derive all findings before
                       opening the hunter's file); tri-state rubric with gates,
                       machine-checked consistency; full rubric + appraisal for
                       critical/major, lightweight batch check for minor
                       ──▶ verified/<group_id>.json
[4] Report            build_report joins verified+defects and renders Korean markdown
                       aimed at reviewers unfamiliar with the codebase (claim as title,
                       file role, impact line, snippet, entry path, mechanism, failure
                       scenario, collapsible verification note, fix sample per finding;
                       summary carries a terminology legend + findings index table);
                       critical→major→minor; split into 3 files past ~15 findings
                       ──▶ 감사보고서.md (or 00_요약.md / 01_critical_major.md / 02_minor.md)
```

Key design decisions worth preserving when modifying this skill:

- **Invariant protocol lives in the agent body; only run variables ride the task prompt.**
  The hunter/verifier protocols (read-vs-report split, injection defense, tri-state rubric,
  anti-anchoring) are the agent-definition **body** = system prompt, loaded from disk by the
  harness — so compaction can't paraphrase or truncate them mid-run (the prompt analogue of
  the silent-degradation class this skill guards against), the orchestrator's context isn't
  re-billed the ~130-line template per spawn, and `subagent_type` selection is explicit
  (a wrong general-type pick = protocol not delivered). Task-prompt skeletons
  (`hunt-task.md`/`verify-task.md`) carry only `{{...}}` variables — never copy the protocol
  into them. `tools` is an allowlist (`Read, Grep, Glob, Bash, Write`, no Edit/MCP) — an
  accident-prevention measure, **not** a security boundary while Bash is present. Because the
  protocol moved off the orchestrator's context path, **agent-recognition failure now equals
  protocol-not-delivered**: Stage 0 preflight makes this a hard gate (abort by default;
  compatibility mode — concatenating body + task skeleton onto a general subagent — runs only
  on explicit user consent, is logged, and is surfaced in the final report). The target repo's
  CLAUDE.md/AGENTS.md is auto-injected into the dedicated agents' context (a privileged
  injection channel the hunter can't decline), so both agent bodies explicitly neutralize
  auto-injected directives as untrusted audit data.
- **File handoff between stages is mandatory** — findings must be recorded to disk
  (`defects/<group_id>.json`, `verified/<group_id>.json`) rather than kept only in a
  subagent's context. Every stage output is schema-validated (`validate_output.py`); a
  malformed output gets one retry with the error attached (same-agent follow-up message if
  the file exists, fresh spawn on no-output/crash, then bisect/batch-shrink fallback,
  then `failed` in state.json → reported as an unverifiable group, never silently dropped).
- **Read scope vs. report scope are deliberately different** per hunting subagent:
  unrestricted reading serves both directions — checking upstream guards/call contracts
  cuts false positives, and tracing flows that start or end outside the group (hypothesis
  formation, not just confirmation) cuts false negatives; hunters are told not to stop a
  trace at the group boundary, and that seam_hints only mark statically-cut import edges
  (parser-invisible couplings — dynamic dispatch, DI, config wiring, IPC — must be crossed
  unprompted). Restricted reporting prevents duplicate/overlapping reports and is
  machine-enforced at validation. Cross-group suspicions go into `cross_refs` hints, which
  stage 2.5 explicitly consumes (they must not die on disk): merge preserves sweep/second
  cross_refs into the base file, and hints still unconsumed after the single sweep round
  are recorded by `--residue-check` and surfaced in the report.
- **Judgment belongs to the model, scripts do only deterministic work.** Project
  classification and finding scoring live in prompts, not heuristics; the 4 scripts are
  `select_targets.py`, `group_by_lines.py` (`build`/`subgroup`), `validate_output.py`
  (`validate`/`init-state`/`set-state`/`route-hints`/`extract-claims`/`merge`/`log-issue`),
  and `build_report.py`.
- **Silent degradation must be detected and surfaced.** Grouping records
  `cohesion: import-graph|line-balance-only` and `unparsed_source_exts` in groups.json and
  warns on zero edges; SKILL.md Stage 1d obliges the orchestrator to check them, log the
  issue, and inform the user before proceeding. This class of failure (EXIT=0, schema-valid,
  quality silently gone) is treated as more dangerous than crashes — same rationale behind
  coverage checking and merge superset checks.
- **Import parsers are best-effort** with per-language resolvers: Python, JS/TS, C/C++,
  Rust (crate index, `crate::`/`super::`/cross-crate, `mod` decls), Go (go.mod module
  paths, package-level edges), Java/Kotlin (shared package-declaration index). Unsupported
  languages fall back to directory cohesion **with detection** (see above); add new
  languages as `resolve_*` functions with paired tests.
- **Verification rubric is tri-state (met/unmet/unknown) with gates**: five criteria —
  code actually does X, precondition reachable, outcome genuinely harmful, no upstream
  guard, survives adversarial rebuttal. Any criterion established as unmet kills the
  finding regardless of score (gate); score = met count with threshold 3 (unknowns
  tolerated, appraised further for critical/major); a concrete failure scenario is required
  to confirm. Verdict consistency (gate/threshold/scenario/score/upgrade-requires-full) is
  machine-checked by `validate_output.py`. Verifiers may re-grade severity, but upgrading a
  minor requires full re-scoring.
- **Anti-anchoring in verification**: verifiers get claims (id/location/claim/severity)
  embedded without rationale, must record an independent `rederivation` for every finding
  before opening `defects/<gid>.json`, and escalate to a 2-turn split if parroting is
  observed.
- **Grouping is by line count** (not token count) plus import-graph connected components;
  large groups (10K-line default budget) reduce group-boundary seams that cause cross-file
  misses. Over-budget clusters are split minimizing cut import edges, and cut edges are
  recorded as `seam_hints` injected into both hunters' prompts.
- **File classification for scanning**: `core` (scan, full priority — includes textual
  config/infra files) / `low` (tests, fixtures — scan but cap severity at minor; the cap is
  also machine-enforced at validation) / `exclude` (vendor, build output, generated code,
  lock/data/binary files, and tool/VCS dirs including `.deep-code-audit/` itself —
  excluding it prevents re-run self-contamination). The orchestrator reviews the
  classification summary in-model and corrects via `--include`/`--exclude` overrides.
- **Sweep/second-pass/batch outputs are written to separate files** and merged by
  `validate_output.py` (location-overlap + category dedupe, ID-prefix uniqueness, superset
  preservation of pre-merge finding IDs) — LLM read-modify-rewrite of an existing findings
  file is a silent-loss channel and is forbidden. Duplicates *within* a single hunter
  output (location-overlap + same category) are only **warned** at validation
  (issues.jsonl + stderr, no auto-removal — same range/category can hold genuinely
  distinct defects, so machine removal would be a finding-loss channel); the verifier
  resolves them (keep one, reject the other as `false_positive` with a duplicate
  reject_reason — its protocol has an explicit duplicate-handling rule).
- **Run directories are timestamped** (`<run-id>` = `YYYYMMDD-HHMMSS`) so repeated runs in
  the same workspace stay separate; `state.json` (schema-validation-passed markers, not
  file existence) is the single source of truth for resume and stage completion.
- **Repository content is untrusted data** for hunters and verifiers — directives embedded
  in code/comments/docs ("already reviewed", "skip this file") are never followed and are
  treated as suspicion signals (prompt-injection defense).
- **Operational problems must be logged precisely** to `issues.jsonl` in the run directory
  (symptom / context / action / outcome — no "an error occurred" summaries). This log is
  the raw material for improving the skill later (reviewed in milestone M4). Single writer:
  the orchestrator and `validate_output.py`; subagents report their problems via an
  `issues` field in their own output JSON, merged at validation time.
- **Final report language is Korean**, regardless of the audited codebase's language.
- **Language policy (since 2026-07-07)**: the *instruction layer* is English — SKILL.md
  body/description, both agent bodies/descriptions, `hunt/verify-task.md`, `schemas.md`
  rule prose (token cost: instruction files are billed multiplicatively, spawns × turns;
  the conversion saves ~36% of instruction-layer tokens per run — see
  `.claude/docs/english-conversion-plan.md`). The *data layer* (prose value fields in
  output JSON: hunter `claim`/`rationale`/`coverage[].role`/`.top_risk`/`cross_refs[].hint`;
  verifier `rederivation`/`failure_scenario`/`impact`/`reject_reason`/`rebuttal`/
  `guard_scan[]`/`appraisal[]`/`fix_direction`; brief `purpose`) and the *render layer*
  (`build_report.py` literals, `report-format.md`) stay **Korean** — the report is rendered
  verbatim from these fields, and `rederivation` must share the hunter's language or the
  anti-parroting similarity check goes blind. Korean deliberately kept inside English
  files (residual-Hangul-check allowlist): the severity-criteria 3 lines (verbatim 3-way
  sync with `render_legend`), the Korean trigger phrases in SKILL.md's description,
  example-JSON prose values in schemas.md (few-shot anchors), script/report literals
  quoted in SKILL.md, and Korean literals inside language-policy directives
  (`"특이점 없음"`). `validate_output.py` warns (never fails) when high-signal prose
  fields (`claim`/`rationale`, `rederivation`/`failure_scenario`/`impact`) contain no
  Hangul — the standing drift detector for this policy.
