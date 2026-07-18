# deep-code-audit — Architecture and Operation

> The **single reference document** for anyone new to this skill. It explains the
> skill's purpose and direction, the structure and operation of the pipeline, and its
> reliability devices. It consolidates and supersedes all prior design/planning documents
> (workflow consistency review, development plan, parser-improvement plan, agent-bundling
> plan, English-conversion plan, accuracy-improvement plan — see §8 history), and
> describes the design **as currently implemented**.
>
> 한국어 버전: [deep-code-audit-design.ko.md](deep-code-audit-design.ko.md)

---

## 1. What this skill does

**deep-code-audit** is a Claude Code skill that deeply audits an entire workspace (or a
specified repository) with multiple agents, hunting for **security, concurrency,
fault-handling, logic, and resource** defects and producing a **Korean-language report**.
The implementation is a plugin:

- [.claude-plugin/plugin.json](../../.claude-plugin/plugin.json) — manifest (+ self-listing marketplace.json)
- [skills/deep-code-audit/SKILL.md](../../skills/deep-code-audit/SKILL.md) — orchestrator
- [references/](../../skills/deep-code-audit/references/) — task-prompt skeletons, schemas, report format
- [scripts/](../../skills/deep-code-audit/scripts/) — 4 Python scripts + 73 unit tests (standard library only)
- [agents/](../../agents/) — 2 dedicated subagents (hunter, verifier)

The core idea fits in one sentence: **a two-stage structure of lenient adversarial
detection followed by strict adversarial verification minimizes false negatives and false
positives at the same time.** Hunters record candidates even at low confidence (guarding
against misses), and a fresh-context verifier then tries to tear each claim apart — only
what survives makes it into the report (guarding against false alarms).

Severity has three levels: **critical** (exploitable in normal use, data loss, crash) /
**major** (serious malfunction, data corruption, or weakened security under specific
conditions) / **minor** (localized quality or robustness defects). Cost and precision are
concentrated on critical/major. The design assumes the Fable model's long-context
reasoning, hypothesis-driven detection, and adversarial self-rebuttal capabilities
(the subagent model is inherited, never hardcoded).

## 2. Design principles — the direction it pursues

1. **Judgment belongs to the model; scripts do only deterministic work.** Using keyword
   heuristics for judgment calls like project classification or defect scoring turns
   misclassification directly into missed defects. Scripts handle only line counting,
   group splitting, schema validation, merging, and report rendering.
2. **Big groups, few agents.** Group boundaries (seams) are the main cause of missed
   cross-file defects. A large per-group line budget (default 10,000 lines) reduces the
   number of boundaries itself.
3. **Hypothesis-driven detection, no checklists.** Matching against a vulnerability
   pattern list misses everything outside the list. Hunters trace trust boundaries, data
   flows, and state transitions, form hypotheses about "what could break here," and
   confirm them in the code. (A user's explicit "make sure to check X" is not an
   exception to this: it enters the brief as a lens/premise — `lens_priority`,
   `high_risk_areas`, or `environment` — that seeds hypotheses, and the hunter still
   looks beyond it. A seed the hunter starts from is not the terminal checklist this
   principle rejects.)
4. **Detect leniently, verify adversarially — but asymmetrically.** Critical/major
   candidates are recorded even at low confidence; minor ones only when obvious.
5. **Stage-to-stage handoff must go through files.** Every artifact is written to disk as
   fixed-schema JSON. A side benefit: per-stage, per-group resume.
6. **Silent failure must be structurally blocked.** States where EXIT=0 and the schema
   validates but quality has quietly collapsed (partial coverage, unwarned grouping
   fallback, findings lost during merge) are more common and more dangerous than crashes.
   Each failure mode is explicitly detected and surfaced (§5). This principle was
   promoted to principle rank after a real-world audit in which the absence of a Rust
   parser passed without warning (§8-3).

## 3. Pipeline overview

The orchestrator (main agent) runs four stages (plus reinforcement pass 2.5), delegating
detection and verification to per-group subagents in background parallel (4–6 concurrent):

```
[0] Preflight        dedicated agent definitions exist + session recognizes them
                     → canary spawn → abort by default on failure
                     (compatibility mode only with explicit consent)
[1] Select & group   audit brief (in-model) + core/low/exclude classification
                     + classification review (in-model)
                     + line-count / import-cohesion grouping      ──▶ groups.json
[2] Adversarial hunt one hunter per group (repo-wide read / group-only
                     reporting, asymmetric recording,
                     per-file coverage evidence)                  ──▶ defects/<gid>.json
[2.5] Reinforcement  critical-only second-pass hunter on high-risk groups
                     → cross_refs hint routing → targeted sweep
                     → residue check                              ──▶ defects updated + residue
[3] Adversarial      fresh-context verifier (anti-anchoring independent
    verify           re-derivation, tri-state rubric + gates,
                     machine-checked consistency)                 ──▶ verified/<gid>.json
[4] Report           build_report.py — Korean, critical→major→minor,
                     split into 3 files past 15 findings          ──▶ 감사보고서.md
```

All artifacts are written under `<target root>/.deep-code-audit/<run-id>/` (run-id =
`YYYYMMDD-HHMMSS`). **The single source of truth for resume is `state.json`**, and the
completion criterion is a **recorded schema-validation pass**, not file existence
(distinguishing completion from an artifact half-written by a crash). Operational
problems are logged to `issues.jsonl` as symptom/context/action/outcome — the raw
material for improving the skill (the M4 retrospective). The full file-schema
specification is in
[references/schemas.md](../../skills/deep-code-audit/references/schemas.md).

## 4. Stage-by-stage operation

### Stage 0 — Preflight (dedicated-agent gate)

The hunter's and verifier's invariant protocols (read/report split, injection defense,
rubric, anti-anchoring) live in the **body (= system prompt)** of the dedicated agent
definitions ([agents/](../../agents/)), loaded from disk directly by the harness. What
this placement buys: (1) compaction during a long run cannot paraphrase or truncate the
protocol, (2) the ~130-line template does not accumulate in the orchestrator's context on
every spawn (a spawn prompt = a handful of run variables), (3) agent selection is
deterministic via explicit `subagent_type`.

The flip side is that **agent-recognition failure = protocol not delivered**, so
preflight is a hard gate: confirm the definition files exist → confirm the session
recognizes them (either plugin-scoped or unscoped identifiers; whichever form the session
lists is pinned in `mode.json`) → canary-spawn both types. On failure, the default is
**abort** — thanks to state.json resume, aborting is cheap, while a low-quality full run
is expensive. **Compatibility mode** (agent body + task skeleton concatenated onto a
general-purpose subagent) runs only when the user, informed of the degradation, gives
explicit consent — and it is logged and stated in the final report.

### Stage 1 — Audit brief · target selection · grouping

**Brief (in-model).** The orchestrator directly reads the README, manifests, and entry
points, and records in `brief.json` the project type and purpose, the **lens priority**
(which of the five defect axes is fatal for this project), and **high-risk areas**
(including runtime-environment facts — OS, architecture, concurrency model, exposure
model; grounds that reduce `unknown` verdicts at the verification stage).

**Selection (`select_targets.py`).** Files are classified three ways — `core` (regular
source + textual config/infra files, scanned at full priority) / `low` (tests, fixtures —
scanned but **severity capped at minor**, machine-enforced) / `exclude` (vendor, build
output, generated code, lock/data/binary files, plus **self-exclusion** of
`.deep-code-audit/` — without it, from the second run onward previous reports become
audit targets, a threefold contamination). The orchestrator reviews the classification
in-model and corrects it via `--include`/`--exclude` overrides — a `low` misclassification
mechanically demotes a critical to minor, and an `exclude` misclassification means the
file is never scanned at all.

**Grouping (`group_by_lines.py build`).** Line-count balance + **import-graph connected
components** for cohesion. Over-budget clusters are split by minimizing the number of cut
import edges, and cut edges are recorded as `seam_hints` for both groups, injected into
the hunters' prompts as "trace flows through this interface first" — **if a boundary
cannot be removed, illuminate it.** Import parsers are best-effort (Python, JS/TS, C/C++,
Rust, Go, Java/Kotlin); languages without a parser fall back to directory cohesion, but
the degradation is **always surfaced** via the `cohesion`/`unparsed_source_exts` fields
and warnings (principle 6). On detected degradation the orchestrator logs it, informs the
user, and confirms whether to proceed.

### Stage 2 — Adversarial hunt (per-group hunters)

One hunter per group is delegated in parallel (`subagent_type: deep-audit-hunter`; the
task prompt is the variable skeleton
[hunt-task.md](../../skills/deep-code-audit/references/hunt-task.md), shared by the
primary/sweep/second_pass modes).

- **Read scope ≠ report scope (the core rule).** Reading is unrestricted across the whole
  repository — checking upstream validation and call contracts cuts false positives, and
  tracing flows that start or end outside the group cuts false negatives. Reporting is
  allowed only for defects whose line lives inside the hunter's own group (unique
  ownership → no duplicate reports; machine-enforced). Suspicions about other groups are
  left as `cross_refs` hints — Stage 2.5 is guaranteed to consume them.
- **Coverage — per-file evidence of close reading.** For every core file in the group the
  hunter must record path + role + top risk hypothesis (or "특이점 없음" [nothing
  notable] + grounds). Formally valid partial coverage (a silent miss) is caught by the
  script, which cross-checks against the group's file list.
- **Prompt-injection defense.** Repository content is untrusted data under analysis.
  Directives like "already reviewed" are never followed and are escalated as suspicion
  signals. The target repo's CLAUDE.md/AGENTS.md auto-injected by the harness is
  neutralized the same way (stated explicitly in the agent bodies). **Executing the
  audited code is forbidden** — it is both an execution channel for injection and a
  misjudgment channel due to local-vs-target environment differences.

### Stage 2.5 — Reinforcement pass

If `cross_refs` were only recorded and nothing consumed them, hints would die on disk —
this stage closes that gap. The order matters: ① a **critical-only second-pass hunter**
runs first on `high_risk` groups and is merged (so the second hunter's hints also join
routing) → ② `route-hints` routes all hints to their owning groups (dropping hints
already covered by existing findings — location overlap + same category) and a **targeted
sweep hunter** investigates each group that still has hints → ③ the sweep round is fixed
at one (no infinite recursion); hints still unconsumed afterward are collected by
`--residue-check` and surfaced in the report as "unconsumed hints" — **give up, but never
conceal.**

Second-pass and sweep outputs are **recorded independently in separate files** without
reading existing results, and merging is done only by script (`validate_output.py
merge`) — an LLM's read-modify-rewrite can lose existing findings while still passing
schema validation, a silent-loss channel, so it is forbidden. The merge performs
deduplication + ID uniqueness + a **superset check on pre-merge finding IDs** (verifying
every prior finding survived).

### Stage 3 — Adversarial verification

For every group with findings, a **fresh-context** verifier is delegated
(`subagent_type: deep-audit-verifier`; no context shared with the hunters).

**Independence protocol (anti-anchoring).** It blocks the confirmation bias of a verifier
free-riding on the hunter's logic: ① a script produces an excerpt with rationale stripped
(`claims/<gid>.json` — id, location, claim, severity only), which is embedded in the
prompt; ② the verifier, seeing only the claims, **independently re-derives** each defect
from the code and records it in `rederivation`; ③ only **after re-deriving every
finding** may it open the hunter's file to compare and score. ④ If parroting is observed
(re-derivation verbally similar to the hunter's rationale), escalate to a 2-turn split.

**Rubric — tri-state (met/unmet/unknown), five criteria.** ① the code actually does this
② the precondition is reachable ③ the outcome is genuinely harmful ④ no upstream guard
⑤ survives adversarial rebuttal. Three rules for a confirmed verdict: **gate** (one
established unmet → excluded regardless of score), **threshold** (fewer than 3 met →
excluded; the threshold is 3 rather than 5 to tolerate unknowns — criteria unverifiable
due to runtime dependence may remain, passed on for appraisal), **scenario** (confirmed
requires a concrete failure scenario). Why tri-state instead of boolean: without
separating "established false" from "cannot verify," a finding could formally reach
confirmed even after an upstream guard was actually verified. Verdict consistency is
**machine-checked** by `validate_output.py`. Met/unmet verdicts require evidence
(file:line), and before a threshold rejection an attempt to resolve unknowns is
mandatory (a wrong rejection = a missed defect).

**Severity-differentiated verification (principle 4).** Critical/major get all five
criteria + **appraisal** (if unknowns remain, additionally trace call sites, config, and
the thread model, recording the trail in `appraisal`); minor gets a lightweight check of
①③ plus one look for an obvious upstream guard. The verifier may re-grade severity, but
**upgrading a minor requires full rubric re-scoring**. Confirmed findings get a fix
direction and a code sample.

### Stage 4 — Report generation

`build_report.py` joins verified + defects and renders the **Korean** report. The target
reader is **a reviewer unfamiliar with the codebase** — each finding carries the claim as
title, location/severity/verification score, file role, a one-line plain-language impact,
the defective snippet, entry path, mechanism, failure scenario, a collapsible
verification note (rebuttal, guard scan, appraisal trail), and a fix sample. The summary
carries a terminology legend, a findings index, the audit scope, and **unverifiable
groups** (audit gaps — manual review recommended). Past 15 findings the report splits
into three files (`00_요약.md` / `01_critical_major.md` / `02_minor.md`). The format
specification is
[references/report-format.md](../../skills/deep-code-audit/references/report-format.md).

### Orchestration policy

- **Concurrency**: managed directly by the orchestrator — spawn up to the cap (4–6) in
  the background, spawning the next group on each completion notice.
- **Retry — three failure modes distinguished**: schema failure (file exists) → reply the
  error to the same agent, one retry; no response / crash (no file) → one fresh spawn;
  repeated failure → hunters retry via bisection, verifiers via batch shrinking; if that
  also fails, record `failed` and state the group as unverifiable in the report.
- **Invocation interface**: free text — target root, line budget, include/exclude, and
  resume are interpreted both as flags and as natural language ("tests 빼고", "아까 하던
  감사 이어서").
- **Language policy**: instructions to the model (SKILL.md, agent bodies, task skeletons,
  schema prose) are in **English** (token cost — the instruction layer is billed
  multiplicatively, spawns × turns; the conversion saved ~36% of instruction-layer
  tokens); text that reaches humans (prose fields in output JSON, report rendering) stays
  **Korean**. `validate_output.py` doubles as the drift detector, warning when
  high-signal prose fields contain no Hangul.

## 5. Reliability devices (failure mode → defense)

| Failure mode | Defense |
|--------------|---------|
| Formally valid partial coverage (silent miss) | per-file coverage evidence + script cross-check & retry |
| Unwarned grouping fallback on unsupported languages | `cohesion`/`unparsed_source_exts` + warnings + Stage 1 check-and-inform duty |
| Findings lost during LLM merge | independent per-pass files + script merge + ID superset check |
| Verifier free-riding on hunter logic (confirmation bias) | claims-only embed + re-derive-then-open + 2-turn-split fallback |
| Additive scoring's "unverifiable ≠ false" hole | tri-state rubric + unmet gate + scenario requirement (machine-checked) |
| Complying with in-repo directives (prompt injection) | untrusted-data principle + suspicion escalation + neutralized auto-injection (target CLAUDE.md) + no execution of audited code |
| Protocol paraphrased/truncated after compaction | invariant protocol in agent bodies (disk-loaded) |
| Dedicated type unrecognized (protocol not delivered) | Stage 0 preflight hard gate + abort by default |
| Re-scanning own artifacts (second-run contamination) | scanner-level self-exclusion of `.deep-code-audit/` |
| Classification errors feeding the severity gate | in-model classification review + machine safety net for low-cap demotion |
| Half-written artifact mistaken for complete | completion = validation-pass record in `state.json` |
| cross_refs hints dying on disk | Stage 2.5 routing & consumption + residue check surfaced in report |

## 6. Implementation layout

```
.claude-plugin/
  plugin.json / marketplace.json   # manifest + self-listing marketplace
skills/deep-code-audit/
  SKILL.md                         # full orchestrator procedure
  references/
    hunt-task.md / verify-task.md  # spawn-prompt skeletons (run variables only)
    report-format.md               # Korean report format specification
    schemas.md                     # JSON schema spec for all artifacts
  scripts/                         # stdlib only, standalone CLIs
    select_targets.py              # core/low/exclude classification
    group_by_lines.py              # cohesion grouping & min-cut split / bisection
    validate_output.py             # validation, state, routing, claims, merge, issue log
    build_report.py                # Korean report rendering & splitting
    test_scripts.py                # 73 unit tests
agents/                            # dedicated subagents (body = invariant protocol)
  deep-audit-hunter.md             # hunter (primary/sweep/second_pass, 3 modes)
  deep-audit-verifier.md           # verifier (single/batch/2-turn split)
```

Verification commands: `python3 skills/deep-code-audit/scripts/test_scripts.py` ·
`python3 -m py_compile skills/deep-code-audit/scripts/*.py` ·
`claude plugin validate . --strict`. Installation is **plugin-first**: symlink the repo
root under `~/.claude/skills/` and the skills-dir auto-load (Claude Code v2.1.142+) ships
the skill and both agents together. Others install via
`/plugin marketplace add posimind/deep-code-audit`.

## 7. Operating parameters and remaining work

| Item | Fixed value | Tuning signal (M4) |
|------|-------------|--------------------|
| Group line budget | 10,000 (CLI-adjustable) | cross-file FN → raise / hunter quality drop → lower |
| Verification threshold | met 3 | too many FP → 4 / critical FN → consider 2 for critical only |
| Second-pass scope | high_risk groups only | critical FN in non-high-risk → widen high-risk criteria |
| Concurrency cap | 4–6 | operating-environment resources |
| Report split | 3 files past 15 findings | — |

**The remaining milestones are M3 and M4**:

- **M3 — eval fixture + scoring script**: a small repository seeded with ~20 defects + an
  answer key + automatic TP/FP/FN tallying. The fixture targets miss-prone spots —
  cross-file defects (deliberately placed across groups), false-positive bait (code with
  upstream guards), prompt-injection bait. The line budget is lowered (e.g. 2,000) to
  force at least 3 groups — so that the miss defenses (group boundaries, hint routing,
  sweep) actually execute.
- **M4 — measurement & tuning**: precision/recall measurement (critical/major recall is
  the key metric; record variance over 3 runs of the same fixture) + a separate
  false-positive rate on a clean real-world repository + per-run `issues.jsonl`
  retrospectives feeding prompt/script fixes + `description` trigger-accuracy
  measurement.

## 8. Design history (superseded documents, summarized)

This document consolidates and supersedes all of the following. Individual design
rationale has been absorbed into §2–§5.

1. **Workflow consistency review** (initial): defined the prototypes of the 4-stage
   pipeline, file handoff, read/report split, 3-level severity, and report splitting.
2. **Development plan (Fable-optimized final, 2026-07-02)**: fixed most of the current
   design — judgment moved in-model (6 scripts → 4), Stage 2.5 added, tri-state rubric
   with gates, anti-anchoring, coverage evidence, seam_hints, self-exclusion, state.json,
   retry policy. Five rounds of design review pre-fixed ~30 defects.
3. **Parser improvement (2026-07-03)**: triggered by the first real-world audit (ssam —
   100% Rust) where missing import parsers **silently** disabled cohesion grouping. Added
   Rust/Go/Java-Kotlin parsers + degradation detection & disclosure (promoted to
   principle 6). Re-run: edges 0 → 142.
4. **Dedicated-subagent bundling (2026-07-06)**: fixed the weakness of protocols passing
   through the orchestrator's context (compaction paraphrase risk, template accumulation,
   type mis-selection) by moving protocols into agent bodies + variable-only task
   skeletons. Added the Stage 0 preflight. Simultaneously fixed two latent defects:
   schema relative-path resolution and the auto-injection injection surface.
5. **Instruction-layer English conversion (2026-07-07)**: established the language-policy
   invariant — instruction layer in English (billed spawns × turns; instruction-layer
   effective input ≈ −36%, ~340K tokens saved per run), data and render layers stay
   Korean. Korean `rederivation` is a precondition for the parroting check. Accompanied
   by a residual-Hangul allowlist + a no-Hangul warning on high-signal fields (drift
   detector).
6. **Accuracy improvement (planned 2026-07-07, Phases 1–3 implemented)**: added
   runtime-environment facts (OS, architecture, concurrency, exposure model) to the brief
   to stem `unknown` inflation (track A); operationalized the rubric (track B) — evidence
   required for met/unmet verdicts, mandatory unknown-resolution attempt before threshold
   rejection, severity anchor in the verifier body, surfacing the premises of conditional
   confirms. Banned the exclusive reading of lens priority and codified the no-execution
   rule.
7. **Plugin structure conversion (2026-07-13)**: added `.claude-plugin/plugin.json`,
   moved the skill body to `skills/deep-code-audit/`, kept `agents/` at the plugin-spec
   root location. Installation shrank to a single symlink. Since agents register under
   scoped identifiers, Stage 0a gained an identifier-pinning step. The legacy agents
   symlink remains as a transition safety net.
