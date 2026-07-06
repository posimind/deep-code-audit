# 검증자 태스크 프롬프트 골격 (`deep-audit-verifier` 스폰용)

불변 프로토콜(anti-anchoring 2단계 열람·룰브릭·판정 3규칙·차등 검증·산출 규칙)은
에이전트 정의 `agents/deep-audit-verifier.md` **본문**에 있고, 하네스가 디스크에서 직접
로드한다. 오케스트레이터는 아래 골격의 `{{...}}` 만 채워 **새 컨텍스트**
`subagent_type: deep-audit-verifier` 스폰의 태스크 프롬프트로 전달한다(헌터와 컨텍스트
비공유). **프로토콜 전문을 태스크 프롬프트에 복사하지 마라.**

- `{{CLAIMS_EMBED}}` = `validate_output.py extract-claims` 가 만든
  `claims/{{GROUP_ID}}.json` 의 `claims` 배열 내용을 그대로 임베드(발견별 `id`·
  `severity`·`location`·`claim`). **rationale 은 절대 임베드하지 않는다** — 파일 경로만
  주고 "재도출 후 열람" 지시(에이전트 본문)로 통제한다.
- `{{SCHEMA_PATH}}` = `$SKILL/references/schemas.md` 의 **절대경로**. 서브에이전트의
  CWD 는 감사 대상 루트라서 스킬 상대경로는 해석되지 않는다 — 반드시 절대경로로 치환하라.
- `{{OUTPUT_PATH}}`:
  - 단일 검증: `{{RUN_DIR}}/verified/{{GROUP_ID}}.json`
  - 묶음(batch) 분할 검증: `{{RUN_DIR}}/verified/{{GROUP_ID}}.batch-N.json`
    (그룹 단위 병합은 `validate_output.py merge --kind verify` 담당)

---

## 태스크 프롬프트 본문 (그대로 전달)

deep-code-audit 적대적 검증 태스크.

- run 디렉터리: `{{RUN_DIR}}`
- 그룹 명세: `{{RUN_DIR}}/groups.json` (`group_id = {{GROUP_ID}}`)
- 헌터 상세 근거 파일: `{{RUN_DIR}}/defects/{{GROUP_ID}}.json` — **모든 발견의 재도출을
  마친 뒤에만 열어라** (열람 순서 프로토콜은 당신의 본문 지시를 따른다)
- 스키마 문서(절대경로): `{{SCHEMA_PATH}}` — 산출 JSON 은 이 문서 **§3** 을 따른다
- 산출 경로: `{{OUTPUT_PATH}}`

검증 대상 claim 목록(위치와 요지만 — 상세 근거 없음):

```
{{CLAIMS_EMBED}}
```

---

## 편승 격상 폴백 (2턴 분리)

산출된 `rederivation` 이 헌터 `rationale` 과 자구 수준으로 유사하면(M4 측정에서 관찰 시)
**2턴 분리**로 격상한다 — 위 골격에서 헌터 상세 근거 파일 항목을 **빼고** claim 목록만으로
스폰 → 재도출 회신 수신 → **같은 에이전트에 후속 메시지**로 `defects/{{GROUP_ID}}.json`
경로를 전달해 2단계(대조·채점)를 지시한다(컨텍스트 유지). 에이전트 타입은 동일하게
`deep-audit-verifier` 를 쓴다.
