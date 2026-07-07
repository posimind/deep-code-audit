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
- `agents/` — **전용 서브에이전트 정의 2종**. 불변 프로토콜(읽기/보고 분리·인젝션 방어·
  룰브릭·anti-anchoring 등)이 에이전트 **본문(=시스템 프롬프트)** 에 있고, 하네스가
  디스크에서 직접 로드한다 — 오케스트레이터 컨텍스트를 경유하지 않아 컴팩션 의역·축약이
  원리적으로 불가능하다.
  - `deep-audit-hunter.md` — Stage 2 primary / Stage 2.5 sweep·second_pass 3모드 겸용
  - `deep-audit-verifier.md` — Stage 3 적대적 검증(단일/batch/2턴 분리 공용)
  - **설치**: 스킬 심링크 외에 에이전트 심링크가 별도로 필요하다:
    `ln -s <이 스킬 경로>/agents ~/.claude/agents/deep-code-audit`
- `references/` — 채워서 서브에이전트에 전달하는 **태스크 프롬프트 골격(런 변수만)** + 스키마.
  - `hunt-task.md`, `verify-task.md`, `report-format.md`, `schemas.md`

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

## Stage 0 — 프리플라이트 · Run 디렉터리 · 재개 판별

### 0a. 전용 에이전트 프리플라이트 (run 디렉터리 생성 전)

헌터·검증자의 불변 프로토콜은 전용 에이전트 정의 본문에 있으므로, **에이전트 인식 실패 =
프로토콜 미전달**이다. 모른 채 진행하면 조용한 품질 저하(이 스킬이 크래시보다 위험하게
취급하는 실패 양식)가 된다. 신규 런·재개 공통으로 Stage 1 진입 전에 검사한다:

1. **정의 파일 확인(결정적)**: 에이전트 설치 위치(`~/.claude/agents/deep-code-audit/`
   심링크 또는 동등 경로)에 두 파일이 존재하고 frontmatter `name` 이
   `deep-audit-hunter` / `deep-audit-verifier` 와 일치하는지 ls/grep 으로 확인.
2. **세션 인식 확인(인모델)**: 현재 세션에서 사용 가능한 subagent 타입 목록에 두 타입이
   보이는지 확인. **파일은 있는데 안 보이면** 세션 시작 후에 설치된 정의라 등록되지 않은
   것이다(실측 확인된 동작) — 세션 재시작을 안내한다.

여기서 실패하면 **잔여물 없이 중단**한다(아래 실패 정책). 통과 시 0b로.

### 0b. Run 디렉터리와 재개 판별

1. 대상 루트 확정. 산출은 `<대상루트>/.deep-code-audit/<run-id>/` 아래.
2. **재개 판별**: run-id 미지정 시 `.deep-code-audit/` 의 **최신** run 디렉터리
   `state.json` 을 본다. 미완료 단계·그룹(`pending`/`retrying`/`failed`)이 있으면 그 run
   을 이어간다. 완료 상태이거나 run 디렉터리가 없으면 **새 run** 생성:
   `run-id = YYYYMMDD-HHMMSS`(현재 시각). 완료 기준은 파일 존재가 아니라 `state.json`
   의 검증 통과 기록이다.
   - **state.json 이 없는 최신 run 디렉터리**는 "프리플라이트(카나리)에서 중단된 런"으로
     간주한다(init-state 는 Stage 1e 에서야 실행되므로 state.json 부재가 그 시그니처다).
     새 디렉터리를 만들지 말고 **그 디렉터리를 재사용**해 0c 부터 재시도한다 — 실패할
     때마다 run 디렉터리가 늘어나는 것을 막는다.
   - **재개 시 모드 일관성**: `preflight/mode.json` 의 기록 모드와 현재 환경이 다르면
     (compat 런인데 전용 타입이 생겼거나, dedicated 런인데 사라짐) **자동 전환하지 않고**
     차이를 고지한 뒤 사용자 결정을 받는다 — 그룹 간 처리 방식이 갈리는 혼합 런은 결과
     비교 가능성을 깨므로 동의 없이 금지.
3. `.deep-code-audit/` 이 대상의 VCS에 오염되지 않도록, 없으면 대상 루트 `.gitignore`
   에 `.deep-code-audit/` 추가를 안내(또는 사용자 동의 시 직접 추가).
4. **드리프트 가드 기록**: 대상 루트가 git 저장소면 `git rev-parse HEAD` 와
   `git status --porcelain` 요약(변경 파일 목록)을 `$RUN/preflight/vcs.json` 에 기록한다
   (mode.json 과 같은 비스키마 정보 파일 — 오케스트레이터가 직접 쓴다). 런은 수 시간
   걸릴 수 있고, 도중에 대상이 수정되면 헌터가 본 코드와 검증자가 볼 코드가 어긋난다 —
   Stage 3 진입 전 재확인에 쓰인다. **비git 대상은 기록을 생략**하고 그 사실만 남긴다.
5. 이후 각 단계 시작 전 `state.json` 을 확인해 **완료된 단계·그룹은 스킵**한다.

### 0c. 카나리 스폰과 모드 기록 (run 디렉터리 확정 후)

두 에이전트를 각각 최소 과제로 **동시 스폰**해 동작을 확인한다: 태스크 프롬프트
"`$RUN/preflight/<에이전트명>.json` 에 `{"ok": true}` 를 기록하라" → 두 파일 생성 확인.
스폰 가능성·Write 동작·run 디렉터리 쓰기 권한을 한 번에 검증한다(비용은 미니 스폰 2회 —
전체 런 대비 무시 가능). 남은 미완료 단계가 서브에이전트를 요구하지 않으면(보고서만 남은
재개) 생략 가능.

통과(또는 아래 호환 동의) 시 `$RUN/preflight/mode.json` 에
`{"mode": "dedicated" | "compat"}` 를 기록한다(오케스트레이터가 직접 쓰는 비스키마 정보
파일 — 스크립트는 이 파일을 다루지 않는다).

### 프리플라이트 실패 정책

- **기본 중단**: 어느 검사가 왜 실패했는지 + 교정 방법(에이전트 심링크 설치 명령, 세션
  재시작)을 보고하고 **Stage 1 로 진행하지 않는다**. run 디렉터리가 이미 있으면
  `log-issue --stage preflight` 로 기록한다(없으면 대화 보고만 — 기록 위치가 없다).
  중단 비용은 낮다: state.json 재개 아키텍처 덕에 환경을 고치고 재개하면 완료분은
  건너뛴다. 중단이 싸고 저품질 완주가 비싸므로 기본값은 중단이다.
- **호환 모드는 명시 동의로만**: 전용 타입을 쓸 수 없는 환경에서, 저하 내용(프로토콜이
  오케스트레이터 컨텍스트를 경유 → 컴팩션 의역 위험 부활, 스폰 프롬프트 비대, frontmatter
  도구 제한 상실)을 고지받은 사용자가 **명시적으로 동의한 경우에만**: 에이전트 정의
  `agents/<name>.md` **본문** + `references/<x>-task.md` 골격을 이어붙여 범용(파일 쓰기
  가능) 서브에이전트로 전달한다 — 별도 통합본은 유지하지 않는다(단일 소스). 이때 태스크
  프롬프트에 **감사 대상 파일 수정 금지(Edit 등)** 를 명시해 도구 제한 상실을 프롬프트
  수준에서나마 보완한다. 동의 사실을 `log-issue` 로 기록하고, 최종 사용자 보고에 호환
  모드 실행임을 명시한다. 자동 발동은 금지다.

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
- `environment`: **실행 환경 사실 5항목** — `os_targets`(대상 OS), `arch_targets`
  (아키텍처), `concurrency_model`(동시성 모델), `runtime`(언어 런타임·버전 범위),
  `exposure`(노출 모델: 공용 수신/내부 전용/CLI 등). 각 항목은
  `{"value": ..., "evidence": "<근거 위치>"}` 쌍(스키마 §1 — init-state 가 형태 검증).
  이 사실들이 없으면 검증 단계의 reachable/harmful 판정이 unknown 을 양산해 오탐·미탐
  양쪽으로 샌다. **근거를 못 찾으면 단정하지 말고 `value: "unknown"`** — 잘못된
  "Linux 전용" 단정은 헌터가 Windows 경로를 통째로 안 보게 만드는 체계적 미탐 채널이라
  없느니만 못하다(evidence 에 확인 시도한 위치를 남긴다). 판정 근거 소스는 브리프 작성
  중 이미 읽는 파일 옆에 있다:
  - CI 워크플로 매트릭스(`.github/workflows/`), Dockerfile / docker-compose
  - Cargo.toml·`.cargo/config.toml`·build.rs / go.mod·`//go:build` 태그 /
    pyproject classifiers / package.json `engines`
  - 조건부 컴파일·플랫폼 분기 인벤토리: `#ifdef _WIN32`·`cfg(target_os)`·`runtime.GOOS`
    grep(분기의 존재 자체가 멀티 플랫폼 신호)
  - README 설치·배포 절

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
저장소 등), `exclude` 오분류는 스캔 자체를 누락시킨다. 브리프 작성 중 파악한 **관례 밖
테스트 경로**(`spec/`, `e2e/`, golden 파일 디렉터리 등)가 `low` 로 분류됐는지도 대조하라.
프로젝트 성격과 어긋나는 항목이 있으면 `--include`/`--exclude` 오버라이드로 교정해
**1b를 1회 재실행**한다.

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

- **에이전트 타입**: `subagent_type: deep-audit-hunter` **명시 지정**(범용 타입 금지 —
  불변 프로토콜이 에이전트 본문에 있어, 타입을 잘못 고르면 프로토콜 미전달이다).
  모델은 에이전트 frontmatter 에서 `model` 을 생략해 **상속(inherit)** 이 보장된다 —
  스폰 시에도 지정하지 않는다.
- **프롬프트**: `references/hunt-task.md` 골격에 `{{TARGET_ROOT}}`·`{{RUN_DIR}}`·
  `{{GROUP_ID}}`·`{{SCHEMA_PATH}}`(= `$SKILL/references/schemas.md` **절대경로**)·
  `{{OUTPUT_PATH}}` 를 채우고 **primary 모드 절**을 넣어 전달한다. **프로토콜 전문을
  태스크 프롬프트에 복사하지 마라** — 변수 몇 개짜리 태스크 프롬프트가 이 구조의 목적이다.
- 산출: `$RUN/defects/<gid>.json`.

각 헌터 완료 시 검증:

```
python3 $SKILL/scripts/validate_output.py validate --stage hunt --group <gid> --run-dir $RUN
```

- 통과 시 스크립트가 `state.hunt[gid]=done` 으로 기록(+ low 심각도 강등, coverage 대조,
  서브에이전트 issues 병합까지 결정적으로 수행).
- **실패 시 재시도 정책**(아래 "오케스트레이션 정책" 참조): 스키마/일관성·미커버 불합격은
  오류 메시지를 **동일 에이전트에 후속 메시지로 회신**해 1회 재시도.
- `[validate:WARN] ... 자기중복 의심 쌍` 경고(단일 산출 내 위치중첩+동일 category)가
  나오면 **재시도하지 말고 그대로 진행**하라 — 자동 제거는 발견 소실 채널이라 스크립트가
  하지 않으며, 실제 중복 여부는 검증 단계가 판별한다(중복이면 한쪽이 false_positive).
  경고는 issues.jsonl 에 이미 기록되어 있다.

---

## Stage 2.5 — 보강 패스 (고위험 2차 패스 → 힌트 라우팅 → 잔여 검사)

전 그룹 hunt 가 끝난 뒤 실행한다. **순서가 의미 있다**: 2차 패스를 먼저 돌려 병합해야
2차 헌터가 남긴 `cross_refs`(critical 전용 패스의 힌트 — 가장 잃어선 안 되는 축)까지
힌트 라우팅이 소비하고, 2차 발견이 커버 판정에 반영되어 불필요한 sweep 이 줄어든다.

### 2.5a. 고위험 2차 패스

`high_risk: true` 그룹(= `state.second` 에 키가 있는 그룹)마다 **critical 전용 2차
헌터**를 위임한다:

- 스폰: `subagent_type: deep-audit-hunter` + `hunt-task.md` 골격의 **second_pass 모드
  절**. "critical만", 1차 결과 미열람, 산출 `$RUN/defects/<gid>.second.json`(ID 접두사
  `s`, severity 전부 critical).
- 검증·병합:
  ```
  python3 $SKILL/scripts/validate_output.py validate --stage second --group <gid> --run-dir $RUN --no-coverage
  python3 $SKILL/scripts/validate_output.py merge --kind second --group <gid> --run-dir $RUN
  ```
  병합은 위치 중첩+동일 category 중복 제거(다른 category는 별개로 보존) + ID 유일성 +
  기존 발견 보존 검사, 그리고 **cross_refs 보존 병합**(2차 힌트를 base 로 옮겨 다음
  단계가 소비하게 한다)을 수행한다.

### 2.5b. 힌트 라우팅 → sweep

```
python3 $SKILL/scripts/validate_output.py route-hints --run-dir $RUN
```

`cross_refs`(1차 + 병합된 2차)를 소유 그룹으로 라우팅하고, 기존 findings가 커버(위치
중첩+동일 category)하는 힌트·이미 라우팅된 힌트(재개 시 멱등)·exclude 대상 힌트를 걸러
`$RUN/hints/<gid>.json` 을 만든다. 힌트 파일이 생긴 그룹마다 **sweep 헌터**를 위임한다:

- 스폰: `subagent_type: deep-audit-hunter` + `hunt-task.md` 골격의 **sweep 모드 절**.
  힌트 지점만 집중 조사, 기존 결과 미열람, 산출 `$RUN/defects/<gid>.sweep.json`(ID
  접두사 `w`).
- 검증·병합:
  ```
  python3 $SKILL/scripts/validate_output.py validate --stage sweep --group <gid> --run-dir $RUN --no-coverage
  python3 $SKILL/scripts/validate_output.py merge --kind sweep --group <gid> --run-dir $RUN
  ```
  병합은 위치 중첩+동일 category 중복 제거(선행 병합된 2차 발견과의 중복 방지) +
  ID 유일성 + 기존 발견 보존(상위집합) 검사 + cross_refs 보존 병합을 수행한다.

### 2.5c. 잔여 힌트 검사 (전 sweep 병합 후, 필수)

```
python3 $SKILL/scripts/validate_output.py route-hints --run-dir $RUN --residue-check
```

sweep 헌터가 조사 중 **새로** 남긴 cross_refs 는 병합으로 base 에 보존되지만, 이번 런에
추가 sweep 라운드는 없다(무한 재귀 방지). 이 명령이 그런 미소진 힌트를 걸러
`$RUN/hints/residue.json` 에 기록하고(잔여 없음이면 빈 목록 — 검사 수행 증거), 보고서
요약부가 "미소진 힌트(추가 확인 권장 지점)"로 표면화한다 — 포기하되 은폐하지 않는다.
잔여가 있으면 최종 사용자 보고에도 건수를 포함하라.

---

## Stage 3 — 적대적 검증 (그룹별, 새 컨텍스트)

**진입 전 드리프트 확인(재개로 Stage 3 에 들어올 때도 동일)**: `$RUN/preflight/vcs.json`
이 있으면 `git rev-parse HEAD`·`git status --porcelain` 을 재실행해 대조한다. 달라졌으면
헌터가 본 코드와 검증자가 볼 코드가 어긋난 것이다 — 위치·라인이 어긋난 채 검증하면
오탐·미탐 이전에 **판정 자체가 무의미**해진다. 차단이 아니라 고지다: 사용자에게 알리고
`log-issue` 로 기록한 뒤 진행 여부를 확인한다. 비git 대상(vcs.json 없음)은 생략.

### 3a. claim 발췌

```
python3 $SKILL/scripts/validate_output.py extract-claims --run-dir $RUN
```

발견별 `id`·`location`·`claim`·`severity` 만 담은 `$RUN/claims/<gid>.json` 생성(rationale
제거 — 오케스트레이터 컨텍스트에도 rationale이 안 실린다).

### 3b. 검증 위임

발견이 있는 그룹마다 **새 컨텍스트** 검증자를 위임한다(헌터와 컨텍스트 비공유):

- 스폰: `subagent_type: deep-audit-verifier` **명시 지정**. 프롬프트는
  `references/verify-task.md` 골격에 `{{CLAIMS_EMBED}}` = `claims/<gid>.json` 의 `claims`
  배열을 **그대로 임베드**, `{{GROUP_ID}}`·`{{RUN_DIR}}`·`{{OUTPUT_PATH}}`·
  `{{SCHEMA_PATH}}`(절대경로) 채움. **rationale 은 임베드하지 말고** `defects/<gid>.json`
  경로만 준다 — "전체 재도출 후에만 열람" 프로토콜은 에이전트 본문이 담당한다
  (anti-anchoring).
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

편승 징후(`rederivation` 이 헌터 rationale과 자구 수준으로 유사)가 보이면 verify-task.md
말미의 **2턴 분리**로 격상한다(동일 `deep-audit-verifier` 타입, 골격에서 defects 경로를
빼고 스폰 → 재도출 수신 → 후속 메시지로 경로 전달).

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
   회신에는 "산출 파일을 먼저 Read 한 뒤 고쳐 써라"를 포함하라 — 재개된 에이전트는 파일
   상태가 초기화되어 Read 없는 Write(덮어쓰기)가 거부된다(실측).
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

### 런 중간 타입 미인식 = 인프라 실패 (재시도 사다리와 별개)

런 도중 스폰이 `Agent type '...' not found` 류 오류로 실패하면(재개 세션의 환경 변화 등)
해당 그룹을 범용 타입으로 **강등하지 않는다** — 혼합 모드는 그룹 간 결과 비교 가능성을
깨고 문제를 가린다. `log-issue` 후 **중단**하고 환경 교정(에이전트 심링크 복원, 세션
재시작) → 재개를 안내한다. 위 재시도 사다리는 산출물 불량용이며, 이 실패 계열에는
적용하지 않는다.

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
