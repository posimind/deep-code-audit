# 파일 스키마 명세 (deep-code-audit)

단계 간 인계는 전부 이 스키마를 따르는 JSON 파일로 이뤄진다. `validate_output.py`가
§1~§4를 기계 검증하며, §5(`issues.jsonl`)는 append 전용 운용 기록으로 스키마 검증
대상이 아니다. 모든 산출물은 `<target_root>/.deep-code-audit/<run-id>/` 아래에 쓴다.

## 공통 규약

- `run-id` = 실행 시작 시각 `YYYYMMDD-HHMMSS`. 동일 워크스페이스 반복 실행을 구분한다.
- `group_id`는 정수(`3`) 또는 문자열(`"4a"`/`"4b"`, 이분할 하위 그룹). JSON 객체 키로
  쓸 때는 항상 문자열로 정규화한다(`str(group_id)`).
- 발견(finding) ID 규약:
  - primary 패스: `g<gid>-NNN` (`g3-001`)
  - sweep 패스: `g<gid>-wNNN` (`g3-w001`) — 접두사 `w`
  - 2차(second) 패스: `g<gid>-sNNN` (`g3-s001`) — 접두사 `s`
  - 이분할 하위 그룹: `g<gid>-NNN` (`g4a-001`)
  - 접두사 분리로 서로 독립 산출된 패스를 병합할 때 ID 충돌을 막는다.
- `category` 허용값: `security | concurrency | fault | logic | resource`
- `severity` 허용값: `critical | major | minor`
- 3상태 판정 허용값: `met | unmet | unknown`

---

## 0. `targets.json` (Stage 1b 산출 — 기계 검증 대상 아님)

`select_targets.py` 가 생산하고 `group_by_lines.py` 가 소비하는 계약.

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

- `files[].class`: `core | low`. exclude 대상은 `files` 에 없다.
- `excluded`/`excluded_files` 는 groups.json 으로 그대로 전달된다.

---

## 1. `groups.json` (Stage 1 산출)

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

필드 규약:

- `brief`는 오케스트레이터가 `brief.json`으로 먼저 기록한 것을 `group_by_lines.py`가
  병합한 것이다. `lens_priority`는 5개 렌즈의 부분·전체 순열.
- `brief.environment`: **선택 필드**(구 런과 하위 호환 — 없으면 검사하지 않는다).
  존재하면 `os_targets`/`arch_targets`/`concurrency_model`/`runtime`/`exposure` 5항목을
  모두 갖추고, 각 항목은 `value`(문자열 또는 문자열 배열) + `evidence`(비어 있지 않은
  문자열) 쌍이어야 한다 — `validate_output.py init-state` 가 형태를 검증한다. 근거를
  못 찾은 항목은 단정하지 말고 `value: "unknown"` 으로 남기며, 이때 `evidence` 에는
  확인을 시도한 위치를 적는다. 헌터의 가설 가지치기는 evidence 로 확정된 항목으로만
  허용된다(오단정 = 체계적 미탐 채널).
- `excluded`: 감사 범위 투명성을 위한 배제 요약(디렉터리/패턴 단위).
- `excluded_files`: 크기 가드 등으로 개별 배제된 파일 — 오배제 사후 확인용.
- 각 그룹의 `files[].class`는 `core | low`. `high_risk`는 브리프 고위험 영역과의
  경로 접두 매칭 결과. `seam_hints`는 예산 분할로 절단된 강결합 import 간선(없으면 `[]`).
- `cohesion`: `import-graph`(간선 ≥1) | `line-balance-only`(간선 0 — 응집 그룹핑이
  라인 밸런싱으로 폴백됨). 오케스트레이터는 후자일 때 사용자에게 고지해야 한다
  (SKILL.md Stage 1d).
- `unparsed_source_exts`: import 파서가 없는 소스 확장자별 파일 수(예: `{".swift": 42}`,
  없으면 `{}`). 파서 지원: Python, JS/TS, C/C++, Rust, Go, Java/Kotlin. 일부 언어만
  파서가 있는 혼합 저장소의 부분 저하도 이 필드로 드러난다.

---

## 2. `defects/<gid>.json` (Stage 2·2.5 산출)

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

필드 규약:

- `pass`: `primary | sweep | second_pass` — 발견 단위. 한 파일에 여러 패스 결과가
  병합되므로 파일 레벨이 아니라 발견 레벨에 둔다.
- `confidence`: `low | medium | high` — 헌터 자체 확신(검증 우선순위 참고용).
- `location.start`/`end`는 1-기반 정수 라인 번호(start ≤ end). `symbol`은 함수/클래스명.
- `claim`(한 줄 요지)과 `rationale`(근거 상세)은 분리 기록 — 검증 독립성 프로토콜이
  `claim`만 먼저 쓴다.
- `evidence_files`: 추론에 사용한 타 파일(검증자 재추적 경로). 없으면 `[]`.
- `coverage`: **파일별 정독 증거**. 그룹의 모든 core 파일이 등장해야 한다 —
  `validate_output.py`가 그룹 파일 목록과 대조하여 미커버 core 파일이 있으면 재시도.
  각 항목은 경로 + 한 줄 역할 + 최상위 위험 가설(없으면 "특이점 없음"+근거).
- `cross_refs`: 타 그룹 결함 징후 힌트. `category` 필수 — Stage 2.5 커버 판정(위치
  중첩 + 동일 category)에 쓰인다. 없으면 `[]`. sweep/2차 산출의 cross_refs 는 병합 시
  base 파일로 **보존 병합**된다(file+line+category 중복은 base 우선 스킵) — 라우팅이
  중간 산출을 읽지 않으므로, 옮기지 않으면 힌트가 사장된다.
- `issues`: 선택. 작업 중 겪은 운용 문제(§5 형식). `validate_output.py`가
  `issues.jsonl`로 병합.

sweep/2차 패스 독립 산출은 각각 `defects/<gid>.sweep.json`,
`defects/<gid>.second.json`에 **동일 스키마**로 기록한다(단 `coverage`는 sweep/2차에서
선택). 병합은 `validate_output.py`가 담당한다.

---

## 3. `verified/<gid>.json` (Stage 3 산출)

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

필드 규약:

- `verdict`: `confirmed | false_positive`.
- `rubric`: `full`(critical/major, 5기준) | `light`(minor, ①③ + 명백 방어 확인).
- `score`: **met 개수 정수** — 기재한 `criteria` 전체에서 값이 `met`인 기준의 수
  (규칙 5로 기계 검증). full 0~5, light 0~3(통과 조건은 ①③이지만 no_guard 를 met
  으로 기재했으면 score 에 포함된다). "5/5" 같은 표기는 보고서 렌더링에서만 만든다.
- `criteria`: 3상태 판정 맵.
  - full 룰브릭: `does_this`(①), `reachable`(②), `harmful`(③), `no_guard`(④),
    `survives_rebuttal`(⑤) 5개 모두 기재.
  - light 룰브릭: `does_this`(①), `harmful`(③), `no_guard`(④의 경량 확인) 기재
    (`reachable`/`survives_rebuttal`은 생략 가능).
- `rederivation`: claim만 보고 코드에서 독립 재도출한 요지(anti-anchoring 준수 증거).
- **met 판정 증거 필드 3종** — full 룰브릭에서 해당 기준이 met일 때 **조건부 필수**
  (light는 면제; minor를 격상하면 full 재채점 의무가 있으므로 자동 적용된다):

  | 필드 | 형태 | 필수 조건 |
  |---|---|---|
  | `entry_path` | string — 진입점→결함 라인 호출 사슬 한 줄 | `rubric: "full"` 이고 `criteria.reachable == "met"` |
  | `guard_scan` | string[] — 실제 확인한 방어 표면 목록 | `rubric: "full"` 이고 `criteria.no_guard == "met"` |
  | `rebuttal` | string — 최강 반론 + 그것이 실패하는 이유 | `rubric: "full"` 이고 `criteria.survives_rebuttal == "met"` |

- **판정 일관성 규칙** — `validate_output.py`가 기계 검증한다:
  1. **게이트**: `criteria`에 `unmet`이 하나라도 있으면 `verdict`는 반드시
     `false_positive`.
  2. **임계**: full은 met(값이 `met`인 기준) 3개 미만이면 `false_positive`;
     light는 `does_this`·`harmful`이 둘 다 met이 아니면 `false_positive`.
  3. **시나리오**: `confirmed`는 비어 있지 않은 `failure_scenario` 필수.
  4. `false_positive`는 비어 있지 않은 `reject_reason` 필수. 게이트 기각(`criteria`에
     unmet 존재)이면 `reject_reason`이 **어느 기준이 unmet인지 명시**해야 한다(unmet
     기준의 키 이름 또는 ①~⑤ 표기 중 하나 이상 포함 — unmet 오판 = 미탐 방지).
  5. `score`는 `criteria`의 met 개수와 일치.
  6. `rubric: "full"` 이고 `reachable == "met"` 이면 비어 있지 않은 `entry_path` 필수.
  7. `rubric: "full"` 이고 `no_guard == "met"` 이면 비어 있지 않은 `guard_scan`
     (문자열 배열) 필수.
  8. `rubric: "full"` 이고 `survives_rebuttal == "met"` 이면 비어 있지 않은 `rebuttal`
     필수.
  9. **임계 기각 전 해소 의무**: `rubric: "full"` · `verdict: "false_positive"` ·
     `criteria`에 unmet 없음(= 임계 미달 기각)이면 `appraisal`이 비어 있으면 안 된다
     (기각 전 unknown 해소 시도 이력 — unknown 도피로 인한 미탐 방지).
  일관성 위반은 스키마 불합격으로 처리되어 재시도된다.
- `severity_final`: 검증자 재조정 결과. minor를 major/critical로 **격상하면 `rubric`은
  반드시 `full`**(경량 검증만 거친 발견의 상향 보고 방지).
- `appraisal`: critical/major 감정 보강 이력(unknown 기준 추가 추적). 없으면 `[]`.
  임계 미달 기각(unmet 없는 full false_positive)에서는 규칙 9에 따라 비어 있으면 안 된다.
- `criteria`에 unknown이 남은 `confirmed`는 "전제가 참이면 결함"이다 — 검증자는
  `failure_scenario`에 그 전제를 명시하고, 보고서는 **[조건부]** 배지 + unknown 기준
  나열로 렌더링한다(`build_report.py`).
- `fix_sample`/`fix_direction`: confirmed에 권장(보고서 렌더링에 사용).
- 묶음 분할 검증은 `verified/<gid>.batch-N.json`에 동일 스키마로 쓰고
  `validate_output.py`가 `verified/<gid>.json`으로 병합한다.

---

## 4. `state.json` (전 단계 공유 — 진행 상태 매니페스트)

재개 판별·검증 불능 그룹 보고·단계 완료 추적의 **단일 근거**. 완료 기준은 파일 존재가
아니라 스키마 검증 통과다. 갱신은 `validate_output.py`(검증 통과 시 `done`) 및
오케스트레이터(`set-state`로 `retrying`/`failed`)가 수행한다.

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

- 그룹별 상태값: `pending | retrying | done | failed`.
- `failed` = 재시도 + 폴백(헌터 이분할 / 검증자 묶음 축소)까지 실패 → 보고서에
  "검증 불능 그룹"으로 명시.
- 이분할된 하위 그룹은 `"4a"`/`"4b"` 키로 기록한다.
- `sweep`/`second`는 해당 그룹에만 키가 생긴다(비고위험 그룹은 second 키 없음,
  라우팅된 힌트가 없는 그룹은 sweep 키 없음).

---

## 5. `issues.jsonl` (전 단계 공유 — 운용 문제 기록)

**스킬 개선의 근거 자료.** 한 줄에 문제 하나(JSON Lines), append 전용. 기록 주체는
오케스트레이터와 `validate_output.py`로 단일화하며, 서브에이전트가 겪은 문제는 산출
JSON의 `issues` 필드로 남기면 검증 시점에 여기로 병합된다.

```jsonc
{"ts": "2026-07-02T14:35:12", "stage": "hunt", "group_id": 4,
 "actor": "hunter",                          // orchestrator | hunter | verifier | script
 "symptom": "findings[2].location.end가 문자열 \"48\"로 기록되어 스키마 불합격",
 "context": "1차 산출 defects/4.json, 재시도 전",
 "action": "오류 메시지 첨부해 동일 에이전트에 재시도 요청",
 "outcome": "재시도 산출 스키마 통과"}
```

기술 정밀도 요건: `symptom`은 관찰된 사실을, `context`는 당시 입력·상태를, `action`·
`outcome`은 조치와 결과를 담는다. "에러 발생" 수준 요약 금지 — 사후 원인 재구성이
이 파일의 존재 이유다. 서브에이전트 `issues` 항목은 `symptom`/`context`/`action`만
있어도 되며, 병합 시 `ts`·`stage`·`group_id`·`actor`·`outcome`이 채워진다.

---

## 6. 파생 산출물 (스크립트 전용)

### 6-1. `claims/<gid>.json` (`validate_output.py extract-claims` 산출, Stage 3 입력)

rationale을 제거한 발췌 — 검증자에게 먼저 임베드된다(anti-anchoring).

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

### 6-2. `hints/<gid>.json` (`validate_output.py route-hints` 산출, Stage 2.5 sweep 입력)

미커버 cross_refs를 소유 그룹으로 라우팅한 결과. 기존 `hints/*.json` 에 이미 있는
힌트(file+line+category 동일)는 재라우팅하지 않는다 — 조사 후 기각된 힌트의 재소비를
막고 재실행(재개)을 멱등하게 만든다.

```jsonc
{
  "group_id": 5,
  "hints": [
    {"file": "db/conn.py", "line": 15, "category": "concurrency",
     "hint": "커넥션이 전역 공유되나 락 없음 — 동시성 의심", "from_group": 3}
  ]
}
```

### 6-3. `hints/residue.json` (`route-hints --residue-check` 산출, Stage 2.5c)

전 sweep 병합 후 남은 **미소진 힌트**(주로 sweep 헌터가 조사 중 새로 남긴
cross_refs — 이번 런에 추가 조사 라운드가 없다). `build_report.py` 가 요약부에
"미소진 힌트(추가 확인 권장 지점)"로 표면화한다. **잔여가 없어도 빈 목록으로
기록된다**(검사 수행 증거). 이후의 일반 `route-hints` 실행은 residue 힌트도 "이미
처리됨"으로 간주해 재라우팅하지 않는다 — "sweep 라운드 1회" 불변식이 재개 후에도
유지된다(잔여 검사 자신만 residue.json 을 제외하고 재산출한다).

```jsonc
{
  "hints": [
    {"file": "db/conn.py", "line": 40, "category": "resource",
     "hint": "커서 미해제 의심", "from_group": "3", "owner_group": "5"}
  ]
}
```
