# Hunter task-prompt skeleton (for spawning `deep-audit-hunter`)

The invariant protocol (read/report split, injection defense, detection procedure,
asymmetric recording, coverage, output rules) lives in the **body** of the agent
definition `agents/deep-audit-hunter.md`, which the harness loads directly from disk.
The orchestrator fills in only the `{{...}}` below and passes the result as the task
prompt of a `subagent_type: deep-audit-hunter` spawn.
**Never copy the protocol text into the task prompt** — keeping the spawn prompt down
to a few variables is the point of this structure (orchestrator-context savings +
protocol byte stability).

- `{{SCHEMA_PATH}}` = the **absolute path** of `$SKILL/references/schemas.md`. The
  subagent's CWD is the audit target root, so skill-relative paths do not resolve —
  always substitute the absolute path.
- The three modes (primary / sweep / second_pass) share this skeleton. Put the
  matching mode block below into `{{MODE_SECTION}}` **verbatim, as a whole block**.

---

## Task prompt body (fill the mode section and pass as-is)

deep-code-audit hunt task.

- Audit target root: `{{TARGET_ROOT}}`
- Run directory: `{{RUN_DIR}}`
- Assigned group: `group_id = {{GROUP_ID}}` (group spec: `{{RUN_DIR}}/groups.json`)
- Schema document (absolute path): `{{SCHEMA_PATH}}` — output JSON follows **§2** of
  this document
- Output path: `{{OUTPUT_PATH}}`

{{MODE_SECTION}}

---

## Mode section blocks

### primary (Stage 2)

- `{{OUTPUT_PATH}}` = `{{RUN_DIR}}/defects/{{GROUP_ID}}.json`
- Block:

> Mode: **primary** — full-lens first-pass detection. coverage required.
> Finding IDs run sequentially from `g{{GROUP_ID}}-001`, `pass: "primary"`.

### sweep (Stage 2.5 hint tracing)

- `{{OUTPUT_PATH}}` = `{{RUN_DIR}}/defects/{{GROUP_ID}}.sweep.json`
- Block:

> Mode: **sweep** — read the routed hint list `{{RUN_DIR}}/hints/{{GROUP_ID}}.json`.
> **Investigate only each hint's `file:line` locus, focused** (not a re-read of the
> whole group). If the defect the hint points at is real, record it; if not, record
> nothing.
> **Do not open the existing results (`{{RUN_DIR}}/defects/{{GROUP_ID}}.json`)** — the
> coverage decision is already done, and independent recording is the rule.
> Finding IDs from `g{{GROUP_ID}}-w001` (**prefix `w`**), `pass: "sweep"`.
> coverage is optional (this is a focused investigation). You may still leave
> cross_refs.

### second_pass (Stage 2.5 high-risk second hunter)

- `{{OUTPUT_PATH}}` = `{{RUN_DIR}}/defects/{{GROUP_ID}}.second.json`
- Block:

> Mode: **second_pass** — **hunt critical only**: defects that lead to exploitation,
> data loss, or crashes in normal use. major/minor are not this pass's target.
> **Do not open the primary results** (preserve detection independence).
> Finding IDs from `g{{GROUP_ID}}-s001` (**prefix `s`**), `pass: "second_pass"`.
> `severity` is `critical` throughout. coverage optional.

> Merging, dedupe, ID uniqueness, and preservation checks of existing findings for
> sweep/second outputs are `validate_output.py`'s job (the hunter's "write
> independently, only to your own file" rule lives in the agent body).
