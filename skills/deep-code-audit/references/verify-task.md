# Verifier task-prompt skeleton (for spawning `deep-audit-verifier`)

The invariant protocol (anti-anchoring two-stage reading, rubric, three confirmed
rules, tiered verification, output rules) lives in the **body** of the agent
definition `agents/deep-audit-verifier.md`, which the harness loads directly from
disk. The orchestrator fills in only the `{{...}}` below and passes the result as the
task prompt of a **fresh-context** `subagent_type: deep-audit-verifier` spawn (no
context shared with the hunter). **Never copy the protocol text into the task
prompt.**

- `{{CLAIMS_EMBED}}` = the contents of the `claims` array of
  `claims/{{GROUP_ID}}.json` produced by `validate_output.py extract-claims`, embedded
  as-is (per finding: `id`, `severity`, `location`, `claim`). **Never embed
  rationale** — hand over only the file path, controlled by the
  "open after re-derivation" directive (agent body).
- `{{SCHEMA_PATH}}` = the **absolute path** of `$SKILL/references/schemas.md`. The
  subagent's CWD is the audit target root, so skill-relative paths do not resolve —
  always substitute the absolute path.
- `{{OUTPUT_PATH}}`:
  - single verification: `{{RUN_DIR}}/verified/{{GROUP_ID}}.json`
  - batch-split verification: `{{RUN_DIR}}/verified/{{GROUP_ID}}.batch-N.json`
    (the group-level merge is `validate_output.py merge --kind verify`'s job)

---

## Task prompt body (pass as-is)

deep-code-audit adversarial verification task.

- Run directory: `{{RUN_DIR}}`
- Group spec: `{{RUN_DIR}}/groups.json` (`group_id = {{GROUP_ID}}`)
- Hunter detailed-grounds file: `{{RUN_DIR}}/defects/{{GROUP_ID}}.json` — **open it
  only after you have finished re-deriving every finding** (the reading-order
  protocol follows your body instructions)
- Schema document (absolute path): `{{SCHEMA_PATH}}` — output JSON follows **§3** of
  this document
- Output path: `{{OUTPUT_PATH}}`

Claim list to verify (locations and gists only — no detailed grounds):

```
{{CLAIMS_EMBED}}
```

---

## Parroting-escalation fallback (2-turn split)

If produced `rederivation` turns out word-level similar to the hunter's `rationale`
(when observed in M4 measurement), escalate to a **2-turn split** — spawn from the
skeleton above with the hunter detailed-grounds file item **removed**, claim list
only → receive the re-derivation reply → send a **follow-up message to the same
agent** carrying the `defects/{{GROUP_ID}}.json` path and direct stage 2
(comparison/scoring) (context retained). Use the same agent type,
`deep-audit-verifier`.
