# 헌터 태스크 프롬프트 골격 (`deep-audit-hunter` 스폰용)

불변 프로토콜(읽기/보고 분리·인젝션 방어·탐지 절차·비대칭 기록·coverage·산출 규칙)은
에이전트 정의 `agents/deep-audit-hunter.md` **본문**에 있고, 하네스가 디스크에서 직접
로드한다. 오케스트레이터는 아래 골격의 `{{...}}` 만 채워
`subagent_type: deep-audit-hunter` 스폰의 태스크 프롬프트로 전달한다.
**프로토콜 전문을 태스크 프롬프트에 복사하지 마라** — 스폰 프롬프트를 변수 몇 개로
유지하는 것이 이 구조의 목적이다(오케스트레이터 컨텍스트 절약 + 프로토콜 바이트 안정성).

- `{{SCHEMA_PATH}}` = `$SKILL/references/schemas.md` 의 **절대경로**. 서브에이전트의
  CWD 는 감사 대상 루트라서 스킬 상대경로는 해석되지 않는다 — 반드시 절대경로로 치환하라.
- 세 모드(primary / sweep / second_pass)는 이 골격을 공유한다. `{{모드 절}}` 에 아래
  해당 모드 블록을 **블록 전문 그대로** 넣는다.

---

## 태스크 프롬프트 본문 (모드 절까지 채워 그대로 전달)

deep-code-audit 헌트 태스크.

- 감사 대상 루트: `{{TARGET_ROOT}}`
- run 디렉터리: `{{RUN_DIR}}`
- 담당 그룹: `group_id = {{GROUP_ID}}` (그룹 명세: `{{RUN_DIR}}/groups.json`)
- 스키마 문서(절대경로): `{{SCHEMA_PATH}}` — 산출 JSON 은 이 문서 **§2** 를 따른다
- 산출 경로: `{{OUTPUT_PATH}}`

{{모드 절}}

---

## 모드 절 블록

### primary (Stage 2)

- `{{OUTPUT_PATH}}` = `{{RUN_DIR}}/defects/{{GROUP_ID}}.json`
- 블록:

> 모드: **primary** — 전 렌즈 1차 탐지. coverage 필수.
> 발견 ID 는 `g{{GROUP_ID}}-001` 부터 순차, `pass: "primary"`.

### sweep (Stage 2.5 힌트 추적)

- `{{OUTPUT_PATH}}` = `{{RUN_DIR}}/defects/{{GROUP_ID}}.sweep.json`
- 블록:

> 모드: **sweep** — 라우팅된 힌트 목록 `{{RUN_DIR}}/hints/{{GROUP_ID}}.json` 을 읽어라.
> 각 힌트의 `file:line` **지점만 집중 조사**한다(그룹 전체 재정독 아님). 힌트가 가리키는
> 결함이 실재하면 기록하고, 아니면 기록하지 않는다.
> **기존 결과(`{{RUN_DIR}}/defects/{{GROUP_ID}}.json`)는 열지 마라** — 커버 판정은 이미
> 끝났고, 독립 기록이 원칙이다.
> 발견 ID 는 `g{{GROUP_ID}}-w001` 부터(**접두사 `w`**), `pass: "sweep"`.
> coverage 는 선택(집중 조사이므로). cross_refs 는 여전히 남길 수 있다.

### second_pass (Stage 2.5 고위험 2차 헌터)

- `{{OUTPUT_PATH}}` = `{{RUN_DIR}}/defects/{{GROUP_ID}}.second.json`
- 블록:

> 모드: **second_pass** — **critical 만 찾아라**: 정상 사용에서 악용·데이터 손실·크래시로
> 이어지는 결함만. major/minor 는 이번 패스의 대상이 아니다. **1차 결과를 열지 마라**
> (탐지 독립성 유지).
> 발견 ID 는 `g{{GROUP_ID}}-s001` 부터(**접두사 `s`**), `pass: "second_pass"`.
> `severity` 는 전부 `critical`. coverage 는 선택.

> sweep·2차 산출의 병합·중복 제거·ID 유일성·기존 발견 보존 검사는 `validate_output.py`
> 가 담당한다(헌터의 "자기 파일에만 독립 기록" 규칙은 에이전트 본문에 있다).
