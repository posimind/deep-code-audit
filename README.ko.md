<p align="center">
  <img src=".github/assert/deep-code-audit-logo.jpeg" alt="deep-code-audit 로고" width="720">
</p>

<h1 align="center">deep-code-audit</h1>

<p align="center">
  Claude Code용 멀티 에이전트 적대적 코드 감사 — 병렬 헌터가 결함을 찾아내고, 새 컨텍스트의 검증자가 그 주장을 무너뜨리려 시도하며, 살아남은 것만 보고서에 실립니다.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/Claude%20Code-plugin-d97757.svg" alt="Claude Code plugin">
  <img src="https://img.shields.io/badge/python-stdlib%20only-3776ab.svg" alt="Python stdlib only">
</p>

<p align="center">
  <a href="README.md">English</a> | <b>한국어</b>
</p>

---

## 무엇인가요?

`deep-code-audit`은 [Claude Code](https://docs.anthropic.com/en/docs/claude-code) 플러그인(스킬 + 전용 서브에이전트 2종)으로, 저장소 전체를 대상으로 **보안·동시성·장애 처리·로직·리소스 결함**을 감사하고, 해당 코드베이스를 모르는 리뷰어도 읽을 수 있는 **한국어 구조화 보고서**를 생성합니다.

모델 하나가 코드를 한 번 훑고 끝내는 대신, 적대적 파이프라인을 돌립니다:

```
[1] 선별 & 그룹핑     파일 분류(core / low / exclude), 감사 브리프 작성,
                      라인 수 + import 그래프 응집도 기반 그룹 구성
[2] 적대적 탐지       그룹당 헌터 서브에이전트 1개, 4–6개 병렬 —
                      저장소 전체를 읽되, 보고는 자기 그룹 안에서만
[2.5] 보강 패스       고위험 그룹 대상 critical 전용 2차 탐지 +
                      그룹 간 힌트 라우팅, 스크립트 병합(무손실 보장)
[3] 적대적 검증       그룹당 새 컨텍스트 검증자: 헌터의 근거를 열어보기 전에
                      모든 주장을 독립적으로 재도출(앵커링 방지),
                      게이트가 있는 3상 루브릭 + 기계 정합성 검사
[4] 보고서            critical → major → minor 순으로, 발견마다 진입 경로·
                      메커니즘·장애 시나리오·수정 예시 포함
```

모든 스테이지는 디스크의 스키마 검증된 JSON 파일(`<대상>/.deep-code-audit/<run-id>/`)로 상태를 넘기므로, 실행은 **재개 가능**하고 스테이지별 재시도가 되며, 어떤 결과도 에이전트 컨텍스트 안에만 머물지 않습니다.

전체 그림 — 설계 목표와 원칙, 단계별 동작, 실패 양식 → 방어 장치 표, 설계 이력 — 은 **[구조와 동작 문서](.claude/docs/deep-code-audit-design.ko.md)**를 참고하세요.

## 왜 쓰나요?

- **오탐(false positive)이 적습니다.** 모든 발견은 독립 검증을 통과해야 합니다 — 검증자는 헌터의 논리를 보기 전에 원본 코드에서 주장을 재도출하고, 5개 기준 루브릭(전제조건 도달 가능성, 실제 유해성, 상류 가드 부재, 적대적 반박 생존…)을 적용합니다. 하나라도 불충족으로 판명되면 그 발견은 탈락합니다.
- **미탐(false negative)이 적습니다.** 헌터는 읽기에 제한이 없고(파일 간 추적은 선택이 아니라 의무), 그룹 경계는 import 그래프 클러스터링으로 최소화되며, 그룹 밖 의심 지점은 반드시 소비되는 힌트로 라우팅되고, 고위험 그룹은 critical 전용 2차 패스를 한 번 더 받습니다.
- **의미 있는 심각도.** critical/major 발견은 전체 루브릭 + 추가 감정(appraisal)을 거치고, 테스트/픽스처 파일은 심각도가 상한 처리되며 이 상한은 기계적으로 강제됩니다.
- **프롬프트 인젝션 방어.** 대상 저장소의 콘텐츠는 — 대상의 `CLAUDE.md`까지 포함해 — 신뢰하지 않는 데이터로 취급됩니다. 주석 속 "이미 리뷰됨, 이 파일은 건너뛰어도 됨"은 지시가 아니라 의심 신호입니다.
- **조용한 품질 저하 감지.** "종료 코드 0인데 품질만 조용히 사라진" 상태를 크래시보다 위험하게 취급합니다: 그룹핑 응집도 폴백, 미지원 언어, 미소비 힌트, 병합 손실이 모두 감지되어 `issues.jsonl`에 기록되고 보고서에 노출됩니다.
- **의존성 제로.** 헬퍼 스크립트 4개는 Python 표준 라이브러리만 사용하며, 단위 테스트 73개가 붙어 있습니다.

## 비용: 토큰을 많이 씁니다

시작하기 전에 알아두세요: `deep-code-audit`은 **코드베이스 전체를 여러 서브에이전트로 병렬 분석**하므로, 1회 실행에 상당한 규모의 토큰을 소모할 수 있습니다.

객관적인 기준점으로, [mobis-oss/ssam](https://github.com/mobis-oss/ssam)을 대상으로 실제 운용한 결과는 다음과 같습니다:

| | `ssam` 전체 감사 1회 기준 |
|---|---|
| **Claude Max 5x** | 5시간 세션 사용량의 **약 75%** 소진 |
| **Claude API** | **약 $70** 상당의 토큰 소진 |

소모량은 코드베이스 규모, 그룹 수, 검증 단계까지 살아남는 발견 수에 따라 대략 비례해서 늘어납니다.

### codebase-memory-mcp로 토큰 절약하기

이 비용에서 **코드베이스 검색을 위한 `Read` 토큰**이 상당한 비중을 차지합니다 — 헌터와 검증자는 흐름을 추적하고 상류 가드를 확인하기 위해 파일을 폭넓게 읽습니다.

토큰 소모를 절약하고 싶다면 **[codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp)**를 함께 사용하는 것을 적극 권장합니다. 원본 파일을 통째로 읽는 대신 미리 구축한 지식 그래프에서 구조 질의에 답하는 방식이라, 이 스킬이 집중적으로 발생시키는 읽기 패턴에 그대로 들어맞습니다. **codebase-memory-mcp를 적용하면 `deep-code-audit` 운용에 필요한 토큰이 30% 이상 절약됩니다.**

## 설치

이 플러그인은 두 구성 요소를 함께 배포하며, 스킬이 온전히 동작하려면 **둘 다 로드되어야 합니다**: 스킬 본체(`skills/deep-code-audit/`)와, 실제 탐지·검증을 수행하는 전용 서브에이전트 2종(`agents/deep-audit-hunter.md`, `agents/deep-audit-verifier.md`)입니다. Claude Code 플러그인 경로로 설치하면 둘 다 자동으로 로드됩니다 — `agents/`를 별도로 연결할 필요가 없습니다.

### Claude Code (CLI)

```
/plugin marketplace add posimind/deep-code-audit
/plugin install deep-code-audit@deep-code-audit
```

설치 후 세션을 재시작하세요 — 세션 도중 추가된 에이전트는 재시작 전까지 인식되지 않습니다(스킬의 사전 점검이 이를 대신 확인해 줍니다).

개발용으로는 클론 + 심링크도 동작합니다. 저장소가 `.claude-plugin/plugin.json`을 갖고 있어 제자리에서 플러그인으로 인식됩니다(Claude Code v2.1.142+):

```bash
git clone https://github.com/posimind/deep-code-audit.git
ln -s "$(pwd)/deep-code-audit" ~/.claude/skills/deep-code-audit
```

> 심링크는 `skills/deep-code-audit/`이 아니라 **저장소 루트**에 거세요. 저장소 루트에 `.claude-plugin/plugin.json`이 있어 플러그인으로 로드되며, 바로 그 덕분에 `agents/` 정의도 스킬과 함께 로드됩니다. 스킬 하위 디렉터리만 연결하면 에이전트는 로드되지 않습니다.

### VS Code (Claude Code 확장)

[Claude Code VS Code 확장](https://docs.anthropic.com/en/docs/claude-code/ide-integrations)은 CLI와 `~/.claude`를 공유하므로, 둘 중 하나면 됩니다:

1. 확장의 채팅 패널에서 위와 동일한 `/plugin marketplace add` / `/plugin install` 명령 실행, **또는**
2. 위 방법대로 CLI에서 한 번 설치 — VS Code에서도 그대로 사용 가능합니다.

`agents/`의 별도 연결은 필요 없습니다 — 플러그인이 스킬과 서브에이전트 2종을 함께 묶어 로드합니다. 설치 후 VS Code 창을 리로드(또는 새 세션 시작)하세요.

### opencode

[opencode](https://opencode.ai)는 `~/.claude/skills/*/SKILL.md`와 `~/.config/opencode/skills/*/SKILL.md`에서 Claude 스타일 스킬을 탐색하므로, 스킬 본체는 위의 Claude Code 심링크 설치로도 인식되고, 아니면 이렇게 연결합니다:

```bash
git clone https://github.com/posimind/deep-code-audit.git
mkdir -p ~/.config/opencode/skills
ln -s "$(pwd)/deep-code-audit/skills/deep-code-audit" ~/.config/opencode/skills/deep-code-audit
```

> **중요 — opencode에서는 에이전트가 자동 로드되지 않습니다.** 전용 서브에이전트 2종은 Claude Code 에이전트 정의이고, opencode는 이를 읽지 않습니다: opencode 에이전트는 `~/.config/opencode/agent/`에 다른 프론트매터 형식(tools를 boolean 플래그로, model 명시)으로 두어야 하므로 `agents/`를 심링크하는 것만으로는 부족하며 수동 이식이 필요합니다. 에이전트가 없으면 스킬의 Stage 0 사전 점검이 이를 감지하고 **호환 모드**(에이전트 프로토콜을 범용 서브에이전트에 이어붙이는 방식)를 제안합니다 — 명시적 동의가 있을 때만 실행되며 최종 보고서에 표시됩니다. 전용 에이전트 기반의 온전한 경험은 Claude Code에서 제공됩니다.

## 사용법

기본형은 이것 하나면 됩니다:

```
/deep-code-audit
```

현재 워크스페이스 전체를 기본 설정으로 감사합니다. 자연어 요청으로도 트리거됩니다("이 레포 audit 해줘", "코드 전체 보안 점검해줘").

### 기본으로 탐지하는 결함 목록

모든 실행은 코드베이스를 다섯 가지 결함 렌즈로 훑습니다:

| 렌즈 | 탐지 대상 |
|---|---|
| **보안** | 인젝션, 인증/인가 우회, 비밀정보 노출, 경로 탐색(path traversal), 안전하지 않은 역직렬화 |
| **동시성** | 데이터 레이스, 데드락, TOCTOU 윈도우, 동기화 없는 공유 상태 |
| **장애 처리** | 삼켜진 예외, 누락된 에러 경로, 부분 실패 후 방치된 비일관 상태 |
| **로직** | 경계 조건, off-by-one, 뒤집힌/잘못된 조건식, 도달 불가·죽은 분기 |
| **리소스** | 파일 디스크립터/메모리/커넥션 누수, 에러 경로의 정리 누락, 무한 증가 |

감사 브리프 단계에서 프로젝트 성격에 맞게 렌즈 우선순위를 조정하고(예: 웹 API는 보안 우선), 발견은 critical → major → minor 순으로 보고됩니다.

### 프롬프트로 실행 조정하기

기본 설정을 벗어나는 요청은 커맨드 뒤에 프롬프트로 붙이면 됩니다. 예를 들면:

| 원하는 것 | 이렇게 |
|---|---|
| 테스트 코드 제외 | `/deep-code-audit tests 관련 경로는 제외하고 진행해줘` |
| 지정 경로 제외 | `/deep-code-audit vendor/ 와 examples/ 는 빼고 진행해줘` |
| 지정 경로만 감사 | `/deep-code-audit src/api 디렉터리만 감사해줘` |
| 특정 탐지 목록만 | `/deep-code-audit 동시성이랑 리소스 결함만 검토해줘` |
| 중단된 실행 재개 | `아까 하던 감사 이어서 해줘` |

대화 도중 후속 요청으로 범위를 조정해도 됩니다("tests 빼고", "api 디렉터리만").

### 결과물

결과물은 `<대상>/.deep-code-audit/<run-id>/`에 생성됩니다 — 보고서는 `감사보고서.md`이며, 발견이 ~15건을 넘으면 요약 / critical·major / minor 3개 파일로 분할됩니다. **보고서는 대상 코드베이스의 언어와 무관하게 한국어로 작성됩니다.**

## 개발

```bash
# 단위 테스트 (73개, 외부 의존성 없음)
python3 skills/deep-code-audit/scripts/test_scripts.py

# 컴파일 검사
python3 -m py_compile skills/deep-code-audit/scripts/*.py

# 플러그인 매니페스트 검사
claude plugin validate . --strict
```

## 라이센스

[MIT](LICENSE) © 2026 Youhyun Jung
