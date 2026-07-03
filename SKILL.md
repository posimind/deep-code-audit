---
name: deep-code-audit
description: >-
  워크스페이스(또는 지정한 저장소) 전체를 다중 에이전트로 깊이 감사해 보안·동시성·결함
  처리·로직·리소스 결함을 찾아 한국어 보고서로 낸다. 적대적 탐지 후 적대적 검증으로
  critical/major 결함을 최소 미탐·최소 오탐으로 보고한다. 사용자가 "코드 감사", "보안
  점검", "취약점/버그/결함 찾아줘", "코드 리뷰(전체)", "이 레포 audit 해줘", "security
  review", "find vulnerabilities", "deep dive the codebase for bugs" 처럼 **코드베이스
  전반의 결함·취약점·품질을 훑어달라고 요청하면 '감사'라는 단어가 없어도 이 스킬을
  적극 사용하라.** 특정 파일 한 줄 고치기나 단순 문법 질문이 아니라, 여러 파일·전체
  프로젝트를 대상으로 한 결함 탐색이면 발동 대상이다. 재개("아까 하던 감사 이어서"),
  범위 조정("tests 빼고", "api 디렉터리만") 같은 후속 요청도 이 스킬로 처리한다.
---

# deep-code-audit — 오케스트레이터

이 스킬은 **당신(메인 에이전트)이 오케스트레이터**가 되어 4단계 파이프라인을 돌린다.
각 단계 산출은 **디스크의 JSON 파일로 인계**되고, 서브에이전트(헌터·검증자)는 그 파일을
읽고 쓴다. 파일 인계 덕분에 완료된 단계·그룹은 재개 시 건너뛴다.

**설계 철학(왜 이렇게 하는가)**: 판단은 모델에게, 결정적 작업만 스크립트에게. 탐지는
관대하게·검증은 적대적으로(단 critical/major에 비용 집중). 큰 그룹·적은 에이전트로
그룹 경계 미탐을 줄인다. 자세한 근거는 `.claude/docs/deep-code-audit-design.md`.

## 번들 리소스

- `scripts/` — 결정적 작업 4종(아래 명령은 모두 `python3 <이 스킬 경로>/scripts/<name>.py`).
  - `select_targets.py` core/low/exclude 분류
  - `group_by_lines.py` 라인수+import 응집 그룹핑, 실패 그룹 이분할
  - `validate_output.py` 스키마 검증·state 갱신·힌트 라우팅·claim 발췌·병합·issues
  - `build_report.py` 한국어 보고서 렌더·분할
- `references/` — 채워서 서브에이전트에 전달하는 프롬프트 골격 + 스키마.
  - `hunt-agent.md`, `verify-agent.md`, `report-format.md`, `schemas.md`

> 명령 예시의 `$SKILL` 은 이 SKILL.md가 있는 디렉터리, `$RUN` 은 run 디렉터리다.
> 실제 실행 시 절대경로로 치환하라.

---

## 호출 인터페이스 (자연어 해석)

스킬 인자는 자유 텍스트다. 플래그와 **자연어 지시를 모두 파라미터로 해석**하라:

| 파라미터 | 기본값 | 자연어 예시 |
|----------|--------|-------------|
| 대상 루트 | 현재 워크스페이스 | "이 레포", "../service 를", 경로 지정 |
| 라인 예산 | 10000 | "그룹 크게/작게", "예산 2천으로" |
| 동시 상한 | 5 (4~6) | "천천히", "동시 3개만" |
| include/exclude | 없음 | "tests 빼고", "api 디렉터리만", "generated 도 봐줘" |
| 재개 | 자동 판별 | "아까 하던 감사 이어서", "--resume 20260702-143000" |

범위 지시는 `select_targets.py` 의 `--include`/`--exclude` glob 오버라이드로 옮긴다
(예: "tests 빼고" → 이미 low 이지만 완전 배제 원하면 `--exclude 'tests/*'`; "api 만" →
다른 최상위 디렉터리를 exclude). 애매하면 보수적으로 넓게 스캔하고 배제 요약으로
투명하게 남긴다.

---

## Stage 0 — Run 디렉터리와 재개 판별

1. 대상 루트 확정. 산출은 `<대상루트>/.deep-code-audit/<run-id>/` 아래.
2. **재개 판별**: run-id 미지정 시 `.deep-code-audit/` 의 **최신** run 디렉터리
   `state.json` 을 본다. 미완료 단계·그룹(`pending`/`retrying`/`failed`)이 있으면 그 run
   을 이어간다. 완료 상태이거나 run 디렉터리가 없으면 **새 run** 생성:
   `run-id = YYYYMMDD-HHMMSS`(현재 시각). 완료 기준은 파일 존재가 아니라 `state.json`
   의 검증 통과 기록이다.
3. `.deep-code-audit/` 이 대상의 VCS에 오염되지 않도록, 없으면 대상 루트 `.gitignore`
   에 `.deep-code-audit/` 추가를 안내(또는 사용자 동의 시 직접 추가).
4. 이후 각 단계 시작 전 `state.json` 을 확인해 **완료된 단계·그룹은 스킵**한다.

---

## Stage 1 — 감사 브리프 · 대상 선별 · 그룹핑

### 1a. 감사 브리프 (인모델 — 당신이 직접 판단)

README·매니페스트(package.json, Cargo.toml, pom.xml, go.mod, pyproject.toml 등)·진입점을
**직접 읽고** 다음을 판단해 `$RUN/brief.json` 에 기록한다(스키마 §1 `brief`):

- `project_type` + 한 줄 `purpose`
- `lens_priority`: security/concurrency/fault/logic/resource 중 이 프로젝트에서 치명적인
  축의 우선순위
- `high_risk_areas`: 외부 진입점·인증/인가·결제/데이터 파괴 경로·동시성 핫스팟을 파일/
  디렉터리 단위로 지목(`{"path":..., "reason":...}`). **이 목록이 Stage 2.5 2차 패스
  대상**이 되므로 비용과 직결된다 — 최상위 디렉터리 통째 지목은 피하고 가장 치명적인
  경로를 좁혀라. 그룹핑 후 **전 그룹이 high_risk 면** 브리프가 판별력을 잃은 신호다:
  (a) 고위험 영역을 더 좁혀 재작성하거나 (b) 전면 2차 패스 비용을 사용자에게 고지하고
  의도적으로 진행하거나, 어느 쪽인지 명시적으로 선택하라(순수 시스템 저장소처럼 정말
  전부 고위험인 프로젝트는 (b)가 정답일 수 있다).

키워드 스크립트를 쓰지 않는 이유: 휴리스틱 오분류가 렌즈 가중치를 틀어 미탐으로 직결되고,
문서를 직접 읽는 편이 정확하고 저렴하다.

### 1b. 대상 선별

```
python3 $SKILL/scripts/select_targets.py <대상루트> --out $RUN/targets.json \
  [--include '<glob>' ...] [--exclude '<glob>' ...] [--size-guard 5000]
```

### 1c. 분류 검토 (인모델, 필수)

브리프를 쓰며 이미 README·매니페스트를 읽은 상태이므로, `targets.json` 의 `excluded`·
`low`·`excluded_files`(크기 가드 배제)를 **검토**하라. 패턴 분류 오류는 심각도 게이트로
직결된다 — `low` 오분류는 critical을 minor로 기계 강등시키고(테스트 프레임워크가 제품인
저장소 등), `exclude` 오분류는 스캔 자체를 누락시킨다. 프로젝트 성격과 어긋나는 항목이
있으면 `--include`/`--exclude` 오버라이드로 교정해 **1b를 1회 재실행**한다.

### 1d. 그룹핑

```
python3 $SKILL/scripts/group_by_lines.py build \
  --targets $RUN/targets.json --brief $RUN/brief.json \
  --budget 10000 --run-id <run-id> --out $RUN/groups.json
```

라인수 균형 + import 그래프 응집. 예산 초과 클러스터는 절단 간선 최소화로 분할되고
절단 간선은 `seam_hints` 로 양쪽 그룹에 기록된다. `high_risk` 는 브리프 고위험 영역과의
경로 접두 매칭으로 계산된다. import 파서 지원 언어: Python, JS/TS, C/C++, Rust, Go,
Java/Kotlin.

**실행 후 groups.json 을 확인하라(응집 저하 점검)**: `cohesion` 이 `"line-balance-only"`
이거나 `unparsed_source_exts` 가 비어 있지 않으면, 해당 언어에 import 파서가 없어 응집
그룹핑이 라인 밸런싱으로 폴백된 것이다(다파일 코드 저장소인데 전 그룹 `seam_hints` 가
`[]` 인 경우도 같은 신호). 이때: (1) `log-issue` 로 기록하고 (2) 사용자에게 "언어 X
미지원 → 라인 밸런싱 폴백, cross-file 결함 미탐 위험 증가"를 고지한 뒤 진행 여부를
확인하라. 이 저하는 스크립트가 정상 종료(EXIT=0)하므로 여기서 잡지 않으면 조용히
지나간다.

### 1e. state 초기화

```
python3 $SKILL/scripts/validate_output.py init-state --run-dir $RUN
```

`grouping=done`, 그룹별 `hunt`/`verify=pending`, 고위험 그룹만 `second=pending`.

---

## Stage 2 — 적대적 탐지 (그룹별 헌터, 병렬)

`state.hunt` 가 `pending`/`retrying` 인 그룹마다 헌터 서브에이전트 1개를 **백그라운드
병렬**로 위임한다. **동시 상한 4~6**을 오케스트레이터가 직접 관리한다: 상한만큼 스폰 →
완료 통지를 받을 때마다 다음 그룹 스폰.

- **에이전트 타입**: 파일 쓰기가 가능한 범용 서브에이전트(읽기 전용 탐색 타입 금지 —
  산출 JSON을 못 쓴다). **모델은 지정하지 않고 상속**한다.
- **프롬프트**: `references/hunt-agent.md` 의 골격에 `{{TARGET_ROOT}}`, `{{RUN_DIR}}`,
  `{{GROUP_ID}}` 를 채우고 **primary 모드 스위치**를 적용해 전달한다.
- 산출: `$RUN/defects/<gid>.json`.

각 헌터 완료 시 검증:

```
python3 $SKILL/scripts/validate_output.py validate --stage hunt --group <gid> --run-dir $RUN
```

- 통과 시 스크립트가 `state.hunt[gid]=done` 으로 기록(+ low 심각도 강등, coverage 대조,
  서브에이전트 issues 병합까지 결정적으로 수행).
- **실패 시 재시도 정책**(아래 "오케스트레이션 정책" 참조): 스키마/일관성·미커버 불합격은
  오류 메시지를 **동일 에이전트에 후속 메시지로 회신**해 1회 재시도.

---

## Stage 2.5 — 보강 패스 (힌트 라우팅 + 고위험 2차 패스)

전 그룹 hunt 가 끝난 뒤 실행한다.

### 2.5a. 힌트 라우팅 → sweep

```
python3 $SKILL/scripts/validate_output.py route-hints --run-dir $RUN
```

`cross_refs` 를 소유 그룹으로 라우팅하고, 기존 findings가 커버(위치 중첩+동일 category)하는
힌트와 exclude 대상 힌트를 걸러 `$RUN/hints/<gid>.json` 을 만든다. 힌트 파일이 생긴 그룹
마다 **sweep 헌터**를 위임한다:

- 프롬프트: `hunt-agent.md` + **sweep 모드 스위치**. 힌트 지점만 집중 조사, 기존 결과
  미열람, 산출 `$RUN/defects/<gid>.sweep.json`(ID 접두사 `w`).
- 검증·병합:
  ```
  python3 $SKILL/scripts/validate_output.py validate --stage sweep --group <gid> --run-dir $RUN --no-coverage
  python3 $SKILL/scripts/validate_output.py merge --kind sweep --group <gid> --run-dir $RUN
  ```
  병합은 ID 유일성 + 기존 발견 보존(상위집합) 검사를 수행한다.

### 2.5b. 고위험 2차 패스

`high_risk: true` 그룹(= `state.second` 에 키가 있는 그룹)마다 **critical 전용 2차
헌터**를 위임한다:

- 프롬프트: `hunt-agent.md` + **second_pass 모드 스위치**. "critical만", 1차 결과 미열람,
  산출 `$RUN/defects/<gid>.second.json`(ID 접두사 `s`, severity 전부 critical).
- 검증·병합:
  ```
  python3 $SKILL/scripts/validate_output.py validate --stage second --group <gid> --run-dir $RUN --no-coverage
  python3 $SKILL/scripts/validate_output.py merge --kind second --group <gid> --run-dir $RUN
  ```
  병합은 위치 중첩+동일 category 중복 제거(다른 category는 별개로 보존) + ID 유일성 +
  기존 발견 보존 검사.

---

## Stage 3 — 적대적 검증 (그룹별, 새 컨텍스트)

### 3a. claim 발췌

```
python3 $SKILL/scripts/validate_output.py extract-claims --run-dir $RUN
```

발견별 `id`·`location`·`claim`·`severity` 만 담은 `$RUN/claims/<gid>.json` 생성(rationale
제거 — 오케스트레이터 컨텍스트에도 rationale이 안 실린다).

### 3b. 검증 위임

발견이 있는 그룹마다 **새 컨텍스트** 검증자를 위임한다(헌터와 컨텍스트 비공유):

- 프롬프트: `references/verify-agent.md` 골격에 `{{CLAIMS_EMBED}}` = `claims/<gid>.json`
  의 `claims` 배열을 **그대로 임베드**, `{{GROUP_ID}}`·`{{RUN_DIR}}`·`{{OUTPUT_PATH}}` 채움.
  **rationale 은 임베드하지 말고** `defects/<gid>.json` 경로만 주며 "전체 재도출 후에만
  열람" 지시를 유지한다(anti-anchoring).
- 산출: `$RUN/verified/<gid>.json`. 발견이 많아 묶음 분할하면 각 검증자는
  `verified/<gid>.batch-N.json` 에 쓰고 병합:
  ```
  python3 $SKILL/scripts/validate_output.py merge --kind verify --group <gid> --run-dir $RUN
  ```
- 검증:
  ```
  python3 $SKILL/scripts/validate_output.py validate --stage verify --group <gid> --run-dir $RUN
  ```
  스크립트가 판정 일관성(게이트·임계·시나리오·격상 시 full 룰브릭·score=met개수)을 기계
  검증하고 통과 시 `state.verify[gid]=done`.

편승 징후(`rederivation` 이 헌터 rationale과 자구 수준으로 유사)가 보이면 verify-agent.md
말미의 **2턴 분리**로 격상한다.

---

## Stage 4 — 보고서 생성

전 그룹 verify 완료 후:

```
python3 $SKILL/scripts/build_report.py --run-dir $RUN [--out-dir <원하는 위치>]
```

confirmed 발견을 critical→major→minor 정렬해 한국어 보고서를 낸다. 15건 초과면
`00_요약.md`/`01_critical_major.md`/`02_minor.md` 분할, 이하면 `감사보고서.md`. 검증 불능
그룹은 요약부에 명시된다. 완료 후:

```
python3 $SKILL/scripts/validate_output.py set-state --run-dir $RUN --stage report --status done
```

사용자에게 보고서 경로와 핵심 통계(critical/major 건수, 검증 불능 그룹 유무)를 요약 보고한다.

---

## 오케스트레이션 정책

### 동시성

하네스에 내장 동시성 리미터가 없으므로 **직접 관리**: 상한(4~6)만큼 백그라운드 스폰 →
완료 통지마다 다음 그룹 스폰. 헌터와 검증자 모두 동일.

### 재시도 (3가지 실패 양식을 구분)

1. **스키마/일관성·미커버 불합격(산출 파일은 있음)**: `validate_output.py` 가 오류 메시지를
   낸다. **동일 에이전트에 후속 메시지로 오류를 회신**해 1회 재시도(새 스폰 아님 —
   컨텍스트가 살아 있어 수정이 가장 싸다). 미커버는 첨부된 파일 목록을 정독하도록 지시.
2. **무응답·크래시(산출 파일 자체가 없음)**: 회신 상대가 없으므로 **새 에이전트 스폰**으로
   1회 재시도.
3. **재실패 시 폴백**(곧바로 포기하지 않는다 — 포기 대가는 예산 규모의 감사 공백):
   - **헌터**: 이분할 재시도 1회.
     ```
     python3 $SKILL/scripts/group_by_lines.py subgroup --groups-file $RUN/groups.json --group <gid> --budget <절반>
     ```
     `<gid>a`/`<gid>b` 하위 그룹이 groups.json에 추가된다. 각각 새 헌터 스폰(state에
     `set-state --stage hunt --group <gid>a --status pending` 후 진행). 실패 그룹으로
     라우팅된 힌트의 sweep은 그대로 수행한다.
   - **검증자**: 묶음 축소(발견 수 절반) 재시도 1회.
   - 그래도 실패한 하위 그룹·묶음만 `set-state ... --status failed`. 보고서에 "검증 불능
     그룹"으로 명시된다(포기하되 은폐하지 않는다).

### 운용 문제 기록 (필수)

위 재시도·크래시·스크립트 오류·예상 밖 산출·편승 징후 등은 발생 즉시 기록한다. 서브에이전트
issues는 산출 JSON `issues` 필드→검증 시 자동 병합되고, 오케스트레이터가 직접 겪은 문제는:

```
python3 $SKILL/scripts/validate_output.py log-issue --run-dir $RUN --stage <stage> \
  --actor orchestrator --symptom '<관찰된 사실>' --context '<입력/상태>' \
  --action '<조치>' --outcome '<결과>'
```

**"에러 발생" 수준 요약 금지** — 이 파일(`issues.jsonl`)은 사후 원인 재구성·스킬 개선의
근거 자료다. 증상·맥락·조치·결과를 정밀하게.

### 스킵/재개

각 단계 시작 전 `state.json` 을 읽어 `done` 인 단계·그룹은 건너뛴다. 이로써 크래시·중단
후 재호출해도 완료분을 재실행하지 않는다.
