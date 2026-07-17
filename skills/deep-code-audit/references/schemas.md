# File schema specification (deep-code-audit)

All inter-stage handoffs are JSON files that follow this schema. `validate_output.py`
machine-validates §1–§4; §5 (`issues.jsonl`) is an append-only operations log and not
schema-validated. All outputs are written under
`<target_root>/.deep-code-audit/<run-id>/`.

## Common conventions

- `run-id` = run start time `YYYYMMDD-HHMMSS`. Distinguishes repeated runs in the same
  workspace.
- `group_id` is an integer (`3`) or a string (`"4a"`/`"4b"`, bisected subgroups).
  Always normalize to a string when used as a JSON object key (`str(group_id)`).
- Finding ID conventions:
  - primary pass: `g<gid>-NNN` (`g3-001`)
  - sweep pass: `g<gid>-wNNN` (`g3-w001`) — prefix `w`
  - second pass: `g<gid>-sNNN` (`g3-s001`) — prefix `s`
  - bisected subgroup: `g<gid>-NNN` (`g4a-001`)
  - The prefix split prevents ID collisions when independently produced passes are
    merged.
- `category` allowed values: `security | concurrency | fault | logic | resource`
- `severity` allowed values: `critical | major | minor`
- Tri-state verdict allowed values: `met | unmet | unknown`

## Language policy (output prose)

The final report is Korean and is rendered **verbatim** from these files — there is no
translation stage. Prose value fields in the outputs must therefore be written in
**Korean**:

- §1 (brief): `purpose` — rendered verbatim into the report summary.
  (`project_type` keeps the English-label convention; `high_risk_areas[].reason` and
  `environment[].evidence` are read only by agents — any language, Korean recommended.)
- §2 (hunter): `claim`, `rationale`, `coverage[].role`, `coverage[].top_risk`
  (including the `"특이점 없음"` convention literal), `cross_refs[].hint`
- §3 (verifier): `rederivation`, `failure_scenario`, `impact`, `reject_reason`,
  `rebuttal`, the prose in `guard_scan[]`, `appraisal[].item`/`.evidence`,
  `fix_direction`, and the comments inside `fix_sample`
- Language-neutral (source language as-is): `snippet`, `entry_path`,
  `location.symbol`, the code in `fix_sample`
- `issues` fields (§5): developer-facing, any language

The Korean prose values in the example JSON below double as few-shot anchors for this
policy — keep them Korean.

---

## 0. `targets.json` (Stage 1b output — not machine-validated)

The contract produced by `select_targets.py` and consumed by `group_by_lines.py`.

```jsonc
{
  "target_root": "/path/to/audited/repo",
  "files": [
    {"path": "api/search.py", "lines": 210, "class": "core"},
    {"path": "tests/test_api.py", "lines": 80, "class": "low"}
  ],
  "excluded": ["vendor/", "dist/"],
  "excluded_files": [
    {"path": "data/cities.json", "reason": "size-guard: 비소스 텍스트 12000라인 > 5000"}
  ]
}
```

- `files[].class`: `core | low`. Excluded targets do not appear in `files`.
- `excluded`/`excluded_files` are passed through to groups.json unchanged.

---

## 1. `groups.json` (Stage 1 output)

```jsonc
{
  "run_id": "20260702-143000",
  "target_root": "/path/to/audited/repo",
  "line_budget": 10000,
  "cohesion": "import-graph",
  "unparsed_source_exts": {},
  "brief": {
    "project_type": "web_service",
    "purpose": "사용자 결제를 처리하는 REST API",
    "lens_priority": ["security", "fault", "concurrency", "logic", "resource"],
    "high_risk_areas": [
      {"path": "api/", "reason": "외부 HTTP 진입점"},
      {"path": "billing/charge.py", "reason": "결제 실행 경로"}
    ],
    "environment": {
      "os_targets":        {"value": ["linux"], "evidence": ".github/workflows/ci.yml 매트릭스가 ubuntu만, Dockerfile FROM debian"},
      "arch_targets":      {"value": ["x86_64", "aarch64"], "evidence": "CI 매트릭스 + .cargo/config.toml 타깃"},
      "concurrency_model": {"value": "tokio 멀티스레드 런타임", "evidence": "main.rs #[tokio::main(flavor=multi_thread)]"},
      "runtime":           {"value": "Python 3.9+", "evidence": "pyproject requires-python"},
      "exposure":          {"value": "unknown", "evidence": "README·docker-compose 에 배포 형태 기술 없음(확인 시도)"}
    }
  },
  "excluded": ["vendor/", "dist/"],
  "excluded_files": [
    {"path": "data/cities.json", "reason": "size-guard: 비소스 텍스트 12000라인 > 5000"}
  ],
  "groups": [
    {
      "group_id": 3,
      "files": [
        {"path": "api/search.py", "lines": 210, "class": "core"}
      ],
      "total_lines": 8900,
      "high_risk": true,
      "seam_hints": [
        {"file": "api/search.py", "peer": "db/conn.py", "peer_group": 5}
      ]
    }
  ]
}
```

Field conventions:

- `brief` is what the orchestrator first recorded as `brief.json`, merged in by
  `group_by_lines.py`. `lens_priority` is a partial or full permutation of the five
  lenses. `purpose` is rendered verbatim into the report summary — write it in Korean
  (see Language policy).
- `brief.environment`: **optional field** (backward compatible with older runs — absent
  means not checked). If present, all five items
  `os_targets`/`arch_targets`/`concurrency_model`/`runtime`/`exposure` must exist, and
  each item must be a `value` (string or string array) + `evidence` (non-empty string)
  pair — `validate_output.py init-state` validates the shape. For an item whose
  grounds could not be found, do not assert — leave `value: "unknown"` and record in
  `evidence` where you attempted to check. Hunter hypothesis pruning is allowed only
  on items settled by evidence (a wrong assertion = a systematic false-negative
  channel).
- `excluded`: exclusion summary for audit-scope transparency (directory/pattern level).
- `excluded_files`: files excluded individually (size guard etc.) — for post-hoc
  review of wrongful exclusion.
- Each group's `files[].class` is `core | low`. `high_risk` is the result of
  path-prefix matching against the brief's high-risk areas. `seam_hints` are strongly
  coupled import edges cut by the budget split (`[]` if none).
- `cohesion`: `import-graph` (≥1 edge) | `line-balance-only` (0 edges — cohesive
  grouping fell back to line balancing). In the latter case the orchestrator must
  inform the user (SKILL.md Stage 1d).
- `unparsed_source_exts`: per-extension file counts for source extensions that have no
  import parser (e.g. `{".swift": 42}`; `{}` if none). Parser support: Python, JS/TS,
  C/C++, Rust, Go, Java/Kotlin. Partial degradation in mixed repositories where only
  some languages are parsed also shows up in this field.

---

## 2. `defects/<gid>.json` (Stage 2·2.5 output)

```jsonc
{
  "group_id": 3,
  "findings": [
    {
      "id": "g3-001",
      "pass": "primary",
      "category": "security",
      "severity": "critical",
      "confidence": "medium",
      "location": {"file": "api/search.py", "start": 42, "end": 48,
                   "symbol": "handle_search"},
      "claim": "HTTP 파라미터가 검증 없이 SQL 문자열에 연결됨",
      "rationale": "q는 42행에서 request.args로 유입, 47행에서 f-string으로 SQL에 직결. db/conn.py에 파라미터라이즈 없음.",
      "snippet": "cursor.execute(\"...WHERE name='\" + q + \"'\")",
      "evidence_files": ["db/conn.py"]
    }
  ],
  "coverage": [
    {"path": "api/search.py", "role": "검색 API HTTP 핸들러",
     "top_risk": "q 파라미터가 SQL 문자열에 직결 — g3-001로 기록"},
    {"path": "api/filters.py", "role": "검색 필터 상수 정의",
     "top_risk": "특이점 없음 — 외부 입력이 닿지 않는 상수 테이블"}
  ],
  "cross_refs": [
    {"file": "db/conn.py", "line": 15, "category": "concurrency",
     "hint": "커넥션이 전역 공유되나 락 없음 — 동시성 의심"}
  ],
  "issues": [
    {"symptom": "src/legacy.py가 EUC-KR 인코딩이라 UTF-8 열람 실패",
     "context": "primary 패스, 그룹 파일 정독 중", "action": "인코딩 변환 후 열람"}
  ]
}
```

Field conventions:

- `pass`: `primary | sweep | second_pass` — per finding. Results of several passes are
  merged into one file, so it lives at finding level, not file level.
- `confidence`: `low | medium | high` — the hunter's own confidence (a
  verification-priority reference).
- `location.start`/`end` are 1-based integer line numbers (start ≤ end). `symbol` is
  the function/class name.
- `claim` (one-line gist) and `rationale` (detailed grounds) are recorded separately —
  the verification-independence protocol uses `claim` alone first.
- `evidence_files`: other files used in the reasoning (the verifier's re-trace path).
  `[]` if none.
- `coverage`: **per-file close-reading evidence**. Every core file of the group must
  appear — `validate_output.py` compares against the group file list and retries if
  any core file is uncovered. Each entry: path + one-line role + top risk hypothesis
  (or "특이점 없음" + grounds).
- `cross_refs`: hints of defect signals in other groups. `category` is required — used
  by the Stage 2.5 coverage decision (location overlap + same category). `[]` if none.
  cross_refs from sweep/second outputs are **preservation-merged** into the base file
  (file+line+category duplicates are skipped, base wins) — routing does not read
  intermediate outputs, so a hint not moved is a hint lost.
- `issues`: optional. Operational problems encountered during the work (§5 format).
  Merged into `issues.jsonl` by `validate_output.py`.
- Within a single output, a pair of findings with **overlapping location + the same
  `category`** only gets a self-duplication suspicion **warning** from validate
  (issues.jsonl + stderr — no auto-removal, no fail: genuinely distinct defects can
  coexist in the same range and category). The real duplicate verdict belongs to the
  verification stage (the verifier protocol's duplicate-report rule — keep one, mark
  the rest false_positive).

Sweep/second-pass independent outputs are written to `defects/<gid>.sweep.json` and
`defects/<gid>.second.json` respectively, with the **same schema** (but `coverage` is
optional in sweep/second). Merging is `validate_output.py`'s job.

---

## 3. `verified/<gid>.json` (Stage 3 output)

```jsonc
{
  "group_id": 3,
  "results": [
    {
      "id": "g3-001",
      "verdict": "confirmed",
      "rubric": "full",
      "score": 5,
      "rederivation": "claim만 보고 코드에서 독립 재확인: q가 검증 없이 f-string SQL에 직결",
      "criteria": {"does_this": "met", "reachable": "met", "harmful": "met",
                   "no_guard": "met", "survives_rebuttal": "met"},
      "severity_final": "critical",
      "failure_scenario": "GET /search?q=' OR '1'='1 요청 시 WHERE 절이 항상 참이 되어 전체 테이블이 유출된다.",
      "impact": "로그인 없이 누구나 고객 개인정보 전체를 빼갈 수 있다.",
      "entry_path": "main → route /search → handle_search:42 → cursor.execute:47",
      "guard_scan": ["app.py 미들웨어 체인", "nginx.conf", "handle_search 호출부 2곳"],
      "rebuttal": "최강 반론: ORM 계층이 이스케이프할 것 — 실패: 이 경로는 raw cursor 직접 사용, ORM 미경유(db/conn.py:12)",
      "appraisal": [
        {"item": "상류 WAF/미들웨어 존재 여부", "evidence": "app.py 미들웨어 체인에 입력 필터 없음 확인"}
      ],
      "fix_sample": "cursor.execute(\"...WHERE name LIKE %s\", (f\"%{q}%\",))",
      "fix_direction": "바인드 파라미터 사용, 입력을 데이터로만 취급"
    },
    {
      "id": "g3-002",
      "verdict": "false_positive",
      "rubric": "full",
      "score": 3,
      "rederivation": "claim만 보고 재도출: reflected 파라미터가 응답에 들어가는 것으로 보였음",
      "criteria": {"does_this": "met", "reachable": "met", "harmful": "met",
                   "no_guard": "unmet", "survives_rebuttal": "unmet"},
      "entry_path": "main → route /echo → render:88",
      "severity_final": "major",
      "reject_reason": "기준 ④ unmet 확정 — middleware/auth.py:30에서 동일 입력을 이미 정규화함 (게이트: 점수 무관 배제)"
    }
  ],
  "issues": []
}
```

Field conventions:

- `verdict`: `confirmed | false_positive`.
- `rubric`: `full` (critical/major, 5 criteria) | `light` (minor, ①③ + obvious-guard
  check).
- `score`: **integer met count** — the number of criteria valued `met` across all
  recorded `criteria`. full 0–5, light 0–3 (the pass condition is ①③, but if
  `no_guard` was recorded as met the score includes it). Notation like "5/5" is
  produced only at report rendering. **It is a deterministic function of `criteria`
  and is used in no gate/threshold decision** (those count `met` directly), so a wrong
  value is not a retry cause — `validate_output.py` overwrites it with the met count
  and warns (rule 5). Still record your honest count; the auto-correction is a safety
  net, not a license to guess.
- `criteria`: the tri-state verdict map.
  - full rubric: record all five — `does_this` (①), `reachable` (②), `harmful` (③),
    `no_guard` (④), `survives_rebuttal` (⑤).
  - light rubric: record `does_this` (①), `harmful` (③), `no_guard` (④'s lightweight
    check) (`reachable`/`survives_rebuttal` may be omitted).
- `rederivation`: the gist re-derived independently from the code with only the claim
  (evidence of anti-anchoring compliance).
- **The three met-evidence fields** — conditionally required in the full rubric when
  the matching criterion is met (light is exempt; upgrading a minor obliges full
  re-scoring, so the requirement applies automatically):

  | Field | Shape | Required when |
  |---|---|---|
  | `entry_path` | string — one-line call chain from entry point to defect line | `rubric: "full"` and `criteria.reachable == "met"` |
  | `guard_scan` | string[] — list of defense surfaces actually checked | `rubric: "full"` and `criteria.no_guard == "met"` |
  | `rebuttal` | string — strongest counter-argument + why it fails | `rubric: "full"` and `criteria.survives_rebuttal == "met"` |

- **Verdict consistency rules** — machine-checked by `validate_output.py`:
  1. **Gate**: if `criteria` contains even one `unmet`, `verdict` must be
     `false_positive`.
  2. **Threshold**: full with fewer than 3 met (criteria valued `met`) →
     `false_positive`; light where `does_this` and `harmful` are not both met →
     `false_positive`.
  3. **Scenario**: `confirmed` requires a non-empty `failure_scenario`.
  4. `false_positive` requires a non-empty `reject_reason`. For a gate rejection
     (`criteria` contains unmet), `reject_reason` must **name which criterion is
     unmet** (contain at least one of the unmet criterion's key name or its ①–⑤ mark —
     prevents wrong-unmet = false negatives).
  5. `score` is **auto-corrected** to the met count in `criteria` (overwrite + warn,
     never a retry — score drives no decision).
  6. `rubric: "full"` and `reachable == "met"` requires a non-empty `entry_path`.
  7. `rubric: "full"` and `no_guard == "met"` requires a non-empty `guard_scan`
     (array of strings).
  8. `rubric: "full"` and `survives_rebuttal == "met"` requires a non-empty
     `rebuttal`.
  9. **Resolution duty before a threshold rejection**: `rubric: "full"` ·
     `verdict: "false_positive"` · no unmet in `criteria` (= threshold rejection)
     requires a non-empty `appraisal` (the history of unknown-resolution attempts
     before rejecting — prevents false negatives via fleeing into unknown).
  10. **Completeness gate**: the group's `results` must judge **every finding ID** in
     `defects/<gid>.json` (a verdict for each — confirmed or false_positive). A verified
     output missing an ID (a partial write) is retried, because `build_report` renders
     only what `results` contains, so an unjudged finding would silently vanish from the
     report (neither confirmed nor counted as a false positive). Checked on the merged
     `verified/<gid>.json`, not individual `batch-N` files.
  Consistency violations are treated as schema failures and retried (rule 5 excepted —
  score is auto-corrected, not retried).
- `severity_final`: the verifier's re-grade. **Upgrading a minor to major/critical
  requires `rubric: "full"`** (prevents findings that only went through light
  verification from being reported upward).
- `appraisal`: the critical/major appraisal-reinforcement history (further tracing of
  unknown criteria). `[]` if none. For a **full false_positive without unmet**
  (threshold rejection, and duplicate-report rejections that reject even at met≥3),
  rule 9 forbids it to be empty — a duplicate rejection records here how the two
  claims were established to share one root cause.
- A `confirmed` with unknown remaining in `criteria` means "a defect if the premise
  holds" — the verifier states that premise in `failure_scenario`, and the report
  renders a **[조건부]** badge plus the list of unknown criteria (`build_report.py`).
- `fix_sample`/`fix_direction`: recommended on confirmed (used in report rendering).
- `impact`: **near-mandatory recommendation on confirmed critical/major** — one line
  of "what actually happens" for a reviewer unfamiliar with the codebase (victim and
  asset in non-expert language). Where `failure_scenario` is the reproduction view
  (which input → which wrong result), `impact` is what that result means. Omission is
  a **warning** (recorded to issues.jsonl — backward compatible with older runs), not
  a schema failure; the report renders without its '영향' item.
- Batch-split verification writes `verified/<gid>.batch-N.json` with the same schema;
  `validate_output.py` merges into `verified/<gid>.json`.

---

## 4. `state.json` (shared across stages — progress manifest)

The **single source of truth** for resume decisions, unverifiable-group reporting, and
stage-completion tracking. The completion criterion is schema-validation pass, not
file existence. Updated by `validate_output.py` (`done` on validation pass) and the
orchestrator (`set-state` for `retrying`/`failed`).

```jsonc
{
  "run_id": "20260702-143000",
  "target_root": "/path/to/audited/repo",
  "stages": {
    "grouping": "done",                       // pending | done
    "hunt":   {"3": "done", "4": "retrying", "5": "pending"},
    "sweep":  {"3": "done"},
    "second": {"3": "done"},
    "verify": {"3": "done", "4": "failed"},
    "report": "pending"                        // pending | done
  }
}
```

- Per-group state values: `pending | retrying | done | failed`.
- `failed` = retry plus fallback (hunter bisect / verifier batch shrink) also failed →
  named in the report as an "unverifiable group".
- Bisected subgroups are recorded under keys `"4a"`/`"4b"`.
- `sweep`/`second` keys exist only for applicable groups (no second key for
  non-high-risk groups, no sweep key for groups with no routed hints).

---

## 5. `issues.jsonl` (shared across stages — operational problem log)

**The evidence base for improving the skill.** One problem per line (JSON Lines),
append-only. Writers are unified to the orchestrator and `validate_output.py`;
problems a subagent hits are left in the `issues` field of its output JSON and merged
here at validation time.

```jsonc
{"ts": "2026-07-02T14:35:12", "stage": "hunt", "group_id": 4,
 "actor": "hunter",                          // orchestrator | hunter | verifier | script
 "symptom": "findings[2].location.end가 문자열 \"48\"로 기록되어 스키마 불합격",
 "context": "1차 산출 defects/4.json, 재시도 전",
 "action": "오류 메시지 첨부해 동일 에이전트에 재시도 요청",
 "outcome": "재시도 산출 스키마 통과"}
```

Precision requirements: `symptom` holds the observed fact, `context` the inputs/state
at the time, `action`/`outcome` the measure taken and its result. "An error
occurred"-level summaries are forbidden — post-hoc cause reconstruction is this
file's reason to exist. A subagent `issues` entry may carry only
`symptom`/`context`/`action`; `ts`, `stage`, `group_id`, `actor`, `outcome` are
filled in at merge time.

---

## 6. Derived outputs (script-only)

### 6-1. `claims/<gid>.json` (`validate_output.py extract-claims` output, Stage 3 input)

An excerpt with rationale stripped — embedded to the verifier first (anti-anchoring).

```jsonc
{
  "group_id": 3,
  "claims": [
    {"id": "g3-001", "severity": "critical",
     "location": {"file": "api/search.py", "start": 42, "end": 48, "symbol": "handle_search"},
     "claim": "HTTP 파라미터가 검증 없이 SQL 문자열에 연결됨"}
  ]
}
```

### 6-2. `hints/<gid>.json` (`validate_output.py route-hints` output, Stage 2.5 sweep input)

Uncovered cross_refs routed to their owning groups. Hints already present in existing
`hints/*.json` (same file+line+category) are not re-routed — this prevents
re-consumption of hints rejected after investigation and makes re-execution (resume)
idempotent.

```jsonc
{
  "group_id": 5,
  "hints": [
    {"file": "db/conn.py", "line": 15, "category": "concurrency",
     "hint": "커넥션이 전역 공유되나 락 없음 — 동시성 의심", "from_group": 3}
  ]
}
```

### 6-3. `hints/residue.json` (`route-hints --residue-check` output, Stage 2.5c)

The **unconsumed hints** remaining after all sweep merges (mostly cross_refs newly
left by sweep hunters during their investigation — this run has no further
investigation round). `build_report.py` surfaces them in the summary as
"미소진 힌트(추가 확인 권장 지점)". **Recorded even when empty** (evidence the check
ran). Subsequent ordinary `route-hints` runs treat residue hints as "already handled"
and do not re-route them — the "one sweep round" invariant survives resume (only the
residue check itself recomputes, excluding residue.json).

```jsonc
{
  "hints": [
    {"file": "db/conn.py", "line": 40, "category": "resource",
     "hint": "커서 미해제 의심", "from_group": "3", "owner_group": "5"}
  ]
}
```
