---
name: deep-code-audit
description: >-
  Deeply audits an entire workspace (or a specified repository) with multiple agents to
  find security, concurrency, fault-handling, logic, and resource defects, and produces
  a Korean-language report. Adversarial detection followed by adversarial verification
  reports critical/major defects with minimal false negatives and false positives.
  **Use this skill aggressively whenever the user asks for a codebase-wide sweep of
  defects, vulnerabilities, or quality — even without the word "audit"** — e.g. Korean
  requests like "코드 감사", "보안 점검", "취약점/버그/결함 찾아줘", "코드 리뷰(전체)",
  "이 레포 audit 해줘", or English ones like "security review", "find vulnerabilities",
  "deep dive the codebase for bugs". It triggers on defect hunting across many files or
  a whole project — not on one-line fixes to a specific file or simple syntax
  questions. Follow-up requests such as resume ("아까 하던 감사 이어서") and scope
  adjustment ("tests 빼고", "api 디렉터리만") are also handled by this skill.
---

# deep-code-audit — Orchestrator

This skill makes **you (the main agent) the orchestrator** of a 4-stage pipeline. Each
stage's output is **handed off as JSON files on disk**, and the subagents (hunter,
verifier) read and write those files. Thanks to the file handoff, completed
stages/groups are skipped on resume.

**Design philosophy (why it works this way)**: judgment to the model, deterministic
work only to scripts. Detect generously, verify adversarially (with cost concentrated
on critical/major). Large groups and few agents reduce group-boundary misses. Detailed
rationale: `.claude/docs/deep-code-audit-design.md`.

**Conversation language**: everything user-facing — questions, interim notices, and
the final summary — is written in **Korean**. (The instruction layer is English; do
not let that drift your user-facing language. The report artifacts are Korean too.)

## Bundled resources

- `scripts/` — 4 deterministic tools (every command below is
  `python3 <this skill's path>/scripts/<name>.py`).
  - `select_targets.py` core/low/exclude classification
  - `group_by_lines.py` line-count + import-cohesion grouping, bisecting failed groups
  - `validate_output.py` schema validation, state updates, hint routing, claim
    extraction, merge, issues
  - `build_report.py` Korean report rendering/splitting
- `agents/` — **2 dedicated subagent definitions**. The invariant protocol
  (read/report split, injection defense, rubric, anti-anchoring, …) lives in the agent
  **body (= system prompt)**, which the harness loads directly from disk — it does not
  pass through the orchestrator's context, so compaction paraphrase/truncation is
  impossible in principle.
  - `deep-audit-hunter.md` — Stage 2 primary / Stage 2.5 sweep·second_pass, 3 modes
  - `deep-audit-verifier.md` — Stage 3 adversarial verification (single/batch/2-turn
    split)
  - **Install**: besides the skill symlink, a separate agents symlink is required:
    `ln -s <this skill's path>/agents ~/.claude/agents/deep-code-audit`
- `references/` — **task-prompt skeletons (run variables only)** to fill and pass to
  subagents, plus the schema.
  - `hunt-task.md`, `verify-task.md`, `report-format.md`, `schemas.md`

> In the command examples, `$SKILL` is the directory containing this SKILL.md and
> `$RUN` is the run directory. Substitute absolute paths at execution time.

---

## Invocation interface (natural-language parsing)

Skill arguments are free text. Interpret both flags and **natural-language
directives** as parameters:

| Parameter | Default | Natural-language examples |
|----------|--------|-------------|
| Target root | current workspace | "이 레포", "../service 를", an explicit path |
| Line budget | 10000 | "그룹 크게/작게", "예산 2천으로" |
| Concurrency cap | 5 (4–6) | "천천히", "동시 3개만" |
| include/exclude | none | "tests 빼고", "api 디렉터리만", "generated 도 봐줘" |
| Resume | auto-detect | "아까 하던 감사 이어서", "--resume 20260702-143000" |

Move scope directives into `select_targets.py`'s `--include`/`--exclude` glob
overrides (e.g. "tests 빼고" → already low, but if full exclusion is wanted,
`--exclude 'tests/*'`; "api 만" → exclude the other top-level directories). When
ambiguous, scan conservatively wide and stay transparent via the exclusion summary.

---

## Stage 0 — Preflight · run directory · resume detection

### 0a. Dedicated-agent preflight (before creating the run directory)

The hunter/verifier invariant protocol lives in the dedicated agent definition bodies,
so **agent-recognition failure = protocol not delivered**. Proceeding unaware produces
silent quality degradation (the failure mode this skill treats as more dangerous than
crashes). Check before entering Stage 1, for new runs and resumes alike:

1. **Definition files check (deterministic)**: confirm with ls/grep that both files
   exist at the agent install location (the `~/.claude/agents/deep-code-audit/`
   symlink or an equivalent path) and that their frontmatter `name` matches
   `deep-audit-hunter` / `deep-audit-verifier`.
2. **Session recognition check (in-model)**: confirm both types appear in the list of
   subagent types available in the current session. **If the files exist but the types
   are not visible**, the definitions were installed after the session started and are
   unregistered (empirically confirmed behavior) — advise a session restart.

On failure here, **abort leaving no residue** (failure policy below). On pass, go
to 0b.

### 0b. Run directory and resume detection

1. Fix the target root. Outputs go under `<target root>/.deep-code-audit/<run-id>/`.
2. **Resume detection**: if no run-id was given, look at `state.json` of the
   **latest** run directory in `.deep-code-audit/`. If it has incomplete stages/groups
   (`pending`/`retrying`/`failed`), continue that run. If it is complete or no run
   directory exists, create a **new run**: `run-id = YYYYMMDD-HHMMSS` (current time).
   The completion criterion is the validation-pass record in `state.json`, not file
   existence.
   - A **latest run directory without state.json** is treated as "a run aborted at
     preflight (canary)" (init-state only runs at Stage 1e, so a missing state.json is
     that signature). Do not create a new directory — **reuse that directory** and
     retry from 0c. This keeps failed attempts from piling up run directories.
   - **Mode consistency on resume**: if the mode recorded in `preflight/mode.json`
     differs from the current environment (a compat run but the dedicated types now
     exist, or a dedicated run but they disappeared), **do not switch automatically** —
     announce the difference and get the user's decision. A mixed run whose groups
     were processed under different modes breaks result comparability and is forbidden
     without consent.
3. To keep `.deep-code-audit/` from polluting the target's VCS, if absent advise
   adding `.deep-code-audit/` to the target root's `.gitignore` (or add it directly
   with user consent).
4. **Drift-guard record**: if the target root is a git repository, record
   `git rev-parse HEAD` and a `git status --porcelain` summary (list of changed files)
   into `$RUN/preflight/vcs.json` (a non-schema info file like mode.json — the
   orchestrator writes it directly). A run can take hours; if the target is modified
   midway, the code the hunter saw and the code the verifier will see diverge — this
   record feeds the re-check before Stage 3. **For non-git targets skip the record**
   and note that fact.
5. Before each subsequent stage, check `state.json` and **skip completed
   stages/groups**.

### 0c. Canary spawn and mode record (after the run directory is fixed)

Spawn both agents **concurrently** on a minimal task to confirm they work: task prompt
"write `{"ok": true}` to `$RUN/preflight/<agent name>.json`" → confirm both files got
created. This verifies spawnability, Write behavior, and run-directory write
permission at once (cost: two mini spawns — negligible against the whole run). May be
skipped if the remaining incomplete stages need no subagents (a resume with only the
report left).

On pass (or compat consent below), record `{"mode": "dedicated" | "compat"}` into
`$RUN/preflight/mode.json` (a non-schema info file the orchestrator writes directly —
the scripts do not touch it).

### Preflight failure policy

- **Abort by default**: report which check failed and why, plus how to fix it (agent
  symlink install command, session restart), and **do not proceed to Stage 1**. If the
  run directory already exists, record via `log-issue --stage preflight` (if not,
  conversation report only — there is nowhere to record yet). The cost of aborting is
  low: thanks to the state.json resume architecture, fixing the environment and
  resuming skips completed work. Aborting is cheap and a low-quality complete run is
  expensive, so the default is abort.
- **Compatibility mode only on explicit consent**: in an environment where the
  dedicated types cannot be used, and only when the user — informed of the degradation
  (protocol passes through the orchestrator context → compaction-paraphrase risk
  returns, bloated spawn prompts, loss of the frontmatter tool restriction) —
  **explicitly consents**: concatenate the agent definition `agents/<name>.md`
  **body** + the `references/<x>-task.md` skeleton and pass the result to a
  general-purpose (file-writing capable) subagent — no separate combined copy is
  maintained (single source). In this case state **no modification of audit-target
  files (Edit etc.)** in the task prompt, to compensate at the prompt level for the
  lost tool restriction. Record the consent via `log-issue`, and state in the final
  user report that the run was in compatibility mode. Automatic activation is
  forbidden.

---

## Stage 1 — Audit brief · target selection · grouping

### 1a. Audit brief (in-model — you judge directly)

**Read directly** the README, the manifests (package.json, Cargo.toml, pom.xml,
go.mod, pyproject.toml, …), and the entry points, then decide the following and record
it to `$RUN/brief.json` (schema §1 `brief`):

- `project_type` + a one-line `purpose`. **Write `purpose` in Korean** —
  `build_report.py` renders it verbatim into the report summary
  (`**프로젝트**: {project_type} — {purpose}`).
- `lens_priority`: the priority among security/concurrency/fault/logic/resource — the
  axes fatal to this project first.
- `high_risk_areas`: point at external entry points, authn/authz, payment/
  data-destruction paths, and concurrency hotspots at file/directory granularity
  (`{"path":..., "reason":...}`). **This list becomes the Stage 2.5 second-pass
  target**, so it bears directly on cost — avoid naming whole top-level directories;
  narrow to the most fatal paths. If after grouping **every group is high_risk**, the
  brief has lost its discriminating power: either (a) rewrite with narrower high-risk
  areas, or (b) inform the user of the cost of a full second pass and proceed
  deliberately — choose one explicitly (for a project that genuinely is all
  high-risk, e.g. a pure systems repository, (b) can be the right answer).
- `environment`: **five execution-environment facts** — `os_targets` (target OSes),
  `arch_targets` (architectures), `concurrency_model`, `runtime` (language
  runtime/version range), `exposure` (exposure model: public listener / internal
  only / CLI, …). Each item is a `{"value": ..., "evidence": "<where the grounds
  are>"}` pair (schema §1 — init-state validates the shape). Without these facts the
  verification stage's reachable/harmful verdicts mass-produce unknown, leaking into
  both false positives and false negatives. **If you cannot find grounds, do not
  assert — leave `value: "unknown"`**: a wrong "Linux-only" assertion is a systematic
  false-negative channel that makes hunters skip Windows paths entirely — worse than
  nothing (record in `evidence` where you attempted to check). The verdict-ground
  sources sit right next to the files you already read while writing the brief:
  - CI workflow matrices (`.github/workflows/`), Dockerfile / docker-compose
  - Cargo.toml, `.cargo/config.toml`, build.rs / go.mod, `//go:build` tags /
    pyproject classifiers / package.json `engines`
  - a conditional-compilation/platform-branch inventory: grep for `#ifdef _WIN32`,
    `cfg(target_os)`, `runtime.GOOS` (the existence of branches is itself a
    multi-platform signal)
  - README install/deployment sections

Why no keyword script here: heuristic misclassification skews the lens weights
straight into false negatives, and reading the documents directly is both more
accurate and cheaper.

### 1b. Target selection

```
python3 $SKILL/scripts/select_targets.py <target root> --out $RUN/targets.json \
  [--include '<glob>' ...] [--exclude '<glob>' ...] [--size-guard 5000]
```

### 1c. Classification review (in-model, required)

Having already read the README/manifests for the brief, **review** `excluded`, `low`,
and `excluded_files` (size-guard exclusions) in `targets.json`. Pattern-classification
errors feed straight into the severity gate — a `low` misclassification
machine-demotes critical to minor (e.g. a repository whose product is a test
framework), and an `exclude` misclassification drops the scan altogether. Also
cross-check that the **unconventional test paths** you noticed while writing the brief
(`spec/`, `e2e/`, golden-file directories, …) were classified `low`. If any item
contradicts the project's nature, correct it with `--include`/`--exclude` overrides
and **re-run 1b once**.

### 1d. Grouping

```
python3 $SKILL/scripts/group_by_lines.py build \
  --targets $RUN/targets.json --brief $RUN/brief.json \
  --budget 10000 --run-id <run-id> --out $RUN/groups.json
```

Line-count balancing + import-graph cohesion. Over-budget clusters are split
minimizing cut import edges, and the cut edges are recorded as `seam_hints` in both
groups. `high_risk` is computed by path-prefix matching against the brief's high-risk
areas. Import-parser languages: Python, JS/TS, C/C++, Rust, Go, Java/Kotlin.

**Check groups.json after the run (cohesion-degradation check)**: if `cohesion` is
`"line-balance-only"` or `unparsed_source_exts` is non-empty, the language has no
import parser and cohesive grouping fell back to line balancing (a multi-file code
repository where every group's `seam_hints` is `[]` is the same signal). In that
case: (1) record via `log-issue` and (2) inform the user — "language X unsupported →
line-balancing fallback, higher risk of missing cross-file defects" — and confirm
whether to proceed. This degradation exits 0, so if you don't catch it here it passes
silently.

### 1e. State init

```
python3 $SKILL/scripts/validate_output.py init-state --run-dir $RUN
```

`grouping=done`, per-group `hunt`/`verify=pending`, `second=pending` for high-risk
groups only.

---

## Stage 2 — Adversarial hunt (per-group hunters, parallel)

For every group whose `state.hunt` is `pending`/`retrying`, delegate one hunter
subagent in **background parallel**. Manage the **concurrency cap of 4–6** yourself:
spawn up to the cap → on each completion notice, spawn the next group.

- **Agent type**: `subagent_type: deep-audit-hunter`, **explicitly** (general-purpose
  types forbidden — the invariant protocol lives in the agent body, so picking the
  wrong type = protocol not delivered). The model is guaranteed **inherited** because
  the agent frontmatter omits `model` — do not specify one at spawn either.
- **Prompt**: fill the `references/hunt-task.md` skeleton with `{{TARGET_ROOT}}`,
  `{{RUN_DIR}}`, `{{GROUP_ID}}`, `{{SCHEMA_PATH}}` (= the **absolute path** of
  `$SKILL/references/schemas.md`), `{{OUTPUT_PATH}}`, and put the **primary mode
  block** into `{{MODE_SECTION}}`. **Never copy the protocol text into the task
  prompt** — a task prompt of a few variables is the point of this structure.
- Output: `$RUN/defects/<gid>.json`.

On each hunter's completion, validate:

```
python3 $SKILL/scripts/validate_output.py validate --stage hunt --group <gid> --run-dir $RUN
```

- On pass, the script records `state.hunt[gid]=done` (and deterministically performs
  the low-severity demotion, the coverage comparison, and the subagent-issues merge).
- **On failure, apply the retry policy** (see "Orchestration policy" below):
  schema/consistency and uncovered-file failures are retried once by **replying the
  error to the same agent as a follow-up message**.
- If the `[validate:WARN] ... 자기중복 의심 쌍` warning appears (overlapping
  location + same category within a single output), **do not retry — proceed as is**:
  auto-removal is a finding-loss channel so the script does not do it, and the
  verification stage decides real duplication (if duplicate, one side becomes
  false_positive). The warning is already recorded in issues.jsonl.

---

## Stage 2.5 — Reinforcement pass (high-risk second pass → hint routing → residue check)

Run after every group's hunt is done. **The order is meaningful**: running and merging
the second pass first lets hint routing consume the `cross_refs` left by second-pass
hunters (hints from the critical-only pass — the axis you can least afford to lose),
and second-pass findings feed the coverage decision, reducing unnecessary sweeps.

### 2.5a. High-risk second pass

For each `high_risk: true` group (= groups that have a key in `state.second`),
delegate a **critical-only second hunter**:

- Spawn: `subagent_type: deep-audit-hunter` + the **second_pass mode block** of the
  `hunt-task.md` skeleton. "critical only", no reading the primary results, output
  `$RUN/defects/<gid>.second.json` (ID prefix `s`, severity all critical).
- Validate & merge:
  ```
  python3 $SKILL/scripts/validate_output.py validate --stage second --group <gid> --run-dir $RUN --no-coverage
  python3 $SKILL/scripts/validate_output.py merge --kind second --group <gid> --run-dir $RUN
  ```
  The merge performs location-overlap + same-category dedupe (different categories
  are preserved as distinct) + ID uniqueness + preservation checks of existing
  findings, plus the **cross_refs preservation merge** (moving second-pass hints into
  base so the next stage consumes them).

### 2.5b. Hint routing → sweep

```
python3 $SKILL/scripts/validate_output.py route-hints --run-dir $RUN
```

Routes `cross_refs` (primary + merged second) to their owning groups; filters out
hints covered by existing findings (location overlap + same category), hints already
routed (idempotent on resume), and hints pointing at excluded targets; produces
`$RUN/hints/<gid>.json`. For each group that got a hint file, delegate a **sweep
hunter**:

- Spawn: `subagent_type: deep-audit-hunter` + the **sweep mode block** of the
  `hunt-task.md` skeleton. Focused investigation of the hint loci only, no reading of
  existing results, output `$RUN/defects/<gid>.sweep.json` (ID prefix `w`).
- Validate & merge:
  ```
  python3 $SKILL/scripts/validate_output.py validate --stage sweep --group <gid> --run-dir $RUN --no-coverage
  python3 $SKILL/scripts/validate_output.py merge --kind sweep --group <gid> --run-dir $RUN
  ```
  The merge performs location-overlap + same-category dedupe (preventing duplication
  against the previously merged second-pass findings) + ID uniqueness +
  existing-finding preservation (superset) checks + the cross_refs preservation
  merge.

### 2.5c. Residue hint check (after all sweep merges, required)

```
python3 $SKILL/scripts/validate_output.py route-hints --run-dir $RUN --residue-check
```

cross_refs **newly** left by sweep hunters during their investigation are preserved
into base by the merge, but this run has no further sweep round (prevents infinite
recursion). This command filters such unconsumed hints into `$RUN/hints/residue.json`
(an empty list when there is no residue — evidence the check ran), and the report
summary surfaces them as "미소진 힌트(추가 확인 권장 지점)" — give up, but never
conceal. If residue exists, include the count in the final user report too.

---

## Stage 3 — Adversarial verification (per group, fresh context)

**Drift check before entry (also when entering Stage 3 via resume)**: if
`$RUN/preflight/vcs.json` exists, re-run `git rev-parse HEAD` and
`git status --porcelain` and compare. If they differ, the code the hunter saw and the
code the verifier will see have diverged — verifying with shifted locations/lines
makes the **verdict itself meaningless**, before any false-positive/false-negative
concern. This is a notification, not a block: inform the user, record via
`log-issue`, and confirm whether to proceed. Skip for non-git targets (no vcs.json).

### 3a. Claim extraction

```
python3 $SKILL/scripts/validate_output.py extract-claims --run-dir $RUN
```

Produces `$RUN/claims/<gid>.json` holding only each finding's `id`, `location`,
`claim`, `severity` (rationale stripped — rationale does not land in the
orchestrator's context either).

### 3b. Verification delegation

For each group with findings, delegate a **fresh-context** verifier (no context
shared with the hunter):

- Spawn: `subagent_type: deep-audit-verifier`, **explicitly**. The prompt is the
  `references/verify-task.md` skeleton with `{{CLAIMS_EMBED}}` = the `claims` array
  of `claims/<gid>.json` **embedded as-is**, and `{{GROUP_ID}}`, `{{RUN_DIR}}`,
  `{{OUTPUT_PATH}}`, `{{SCHEMA_PATH}}` (absolute path) filled. **Do not embed
  rationale** — hand over only the `defects/<gid>.json` path; the "open only after
  full re-derivation" protocol is the agent body's job (anti-anchoring).
- Output: `$RUN/verified/<gid>.json`. If findings are many and you batch-split, each
  verifier writes `verified/<gid>.batch-N.json`, then merge:
  ```
  python3 $SKILL/scripts/validate_output.py merge --kind verify --group <gid> --run-dir $RUN
  ```
- Validate:
  ```
  python3 $SKILL/scripts/validate_output.py validate --stage verify --group <gid> --run-dir $RUN
  ```
  The script machine-checks verdict consistency (gate, threshold, scenario,
  full-rubric-on-upgrade, score = met count) and records `state.verify[gid]=done` on
  pass.

If parroting signs appear (`rederivation` word-level similar to the hunter's
`rationale`), escalate to the **2-turn split** at the end of verify-task.md (same
`deep-audit-verifier` type; spawn from the skeleton with the defects path removed →
receive the re-derivation → deliver the path in a follow-up message).

---

## Stage 4 — Report generation

After every group's verify completes:

```
python3 $SKILL/scripts/build_report.py --run-dir $RUN [--out-dir <desired location>]
```

Sorts confirmed findings critical→major→minor and emits the Korean report. Over 15
findings it splits into `00_요약.md`/`01_critical_major.md`/`02_minor.md`; otherwise
`감사보고서.md`. Unverifiable groups are named in the summary. Afterwards:

```
python3 $SKILL/scripts/validate_output.py set-state --run-dir $RUN --stage report --status done
```

Report to the user (in Korean) the report path and the key statistics
(critical/major counts, whether any group was unverifiable).

---

## Orchestration policy

### Concurrency

The harness has no built-in concurrency limiter, so **manage it yourself**: spawn in
background up to the cap (4–6) → on each completion notice, spawn the next group.
Same for hunters and verifiers.

### Retry (distinguish three failure shapes)

1. **Schema/consistency or uncovered-file failure (output file exists)**:
   `validate_output.py` prints the error. **Reply the error to the same agent as a
   follow-up message** and retry once (not a new spawn — its context is alive, so
   fixing there is cheapest). For uncovered files, direct it to close-read the
   attached list. Include in the reply "Read the output file first, then rewrite
   it" — a resumed agent's file state is reset, and a Write without Read (an
   overwrite) is rejected (empirically observed).
2. **No response / crash (no output file at all)**: there is no counterpart to reply
   to, so retry once with a **fresh agent spawn**.
3. **Fallback on second failure** (do not give up immediately — the price of giving
   up is an audit hole the size of the budget):
   - **Hunter**: one bisect retry.
     ```
     python3 $SKILL/scripts/group_by_lines.py subgroup --groups-file $RUN/groups.json --group <gid> --budget <half>
     ```
     Subgroups `<gid>a`/`<gid>b` are added to groups.json. Spawn a new hunter for
     each (proceed after `set-state --stage hunt --group <gid>a --status pending`).
     Sweeps for hints routed to the failed group still run.
   - **Verifier**: one batch-shrink retry (half the findings).
   - Only the subgroups/batches that still fail get
     `set-state ... --status failed`. They are named in the report as unverifiable
     groups (give up, but never conceal).

### Mid-run type non-recognition = infrastructure failure (separate from the retry ladder)

If a spawn fails mid-run with an `Agent type '...' not found`-class error (an
environment change in a resumed session, …), **do not demote** the group to a
general-purpose type — a mixed-mode run breaks cross-group result comparability and
hides the problem. `log-issue`, then **abort**, and guide environment repair (restore
the agents symlink, restart the session) → resume. The retry ladder above is for
defective outputs and does not apply to this failure class.

### Operational problem logging (required)

Record the retries, crashes, script errors, unexpected outputs, parroting signs, etc.
above the moment they occur. Subagent issues flow in via the output JSON `issues`
field and are auto-merged at validation; for problems the orchestrator itself hits:

```
python3 $SKILL/scripts/validate_output.py log-issue --run-dir $RUN --stage <stage> \
  --actor orchestrator --symptom '<observed fact>' --context '<inputs/state>' \
  --action '<measure taken>' --outcome '<result>'
```

**"An error occurred"-level summaries are forbidden** — this file (`issues.jsonl`) is
the evidence base for post-hoc cause reconstruction and skill improvement. Symptom,
context, action, outcome — precisely.

### Skip/resume

Before each stage, read `state.json` and skip stages/groups marked `done`. Thus
re-invocation after a crash or abort never re-runs completed work.
