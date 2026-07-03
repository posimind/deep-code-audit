# 한국어 보고서 템플릿 (Stage 4)

`build_report.py` 가 `verified/*.json` 을 `defects/*.json` 과 id 로 조인해 렌더링하는
**결정적** 서식이다. 이 문서는 그 산출 형식의 명세이자, 필요 시 스크립트 없이 수동
작성할 때의 기준이다. **전 항목 한국어**(감사 대상 코드베이스의 언어와 무관).

## 정렬·분할 규칙

- 확정(confirmed) 발견만 싣는다. false positive 는 집계 수치로만 노출.
- 정렬: **critical → major → minor**, 동순위는 파일 경로 → 시작 라인 순.
- **15건 이하** → 단일 파일 `감사보고서.md` (요약 + 발견 상세).
- **15건 초과** → 3분할:
  - `00_요약.md` — 감사 범위·통계·상세 파일 링크
  - `01_critical_major.md` — critical/major 상세
  - `02_minor.md` — minor 상세 (다수여도 격리되어 critical/major 대응을 방해하지 않음)

## 요약부 구성 (`00_요약.md` 또는 단일 파일 상단)

1. 대상 루트, run-id, 프로젝트 유형·목적, 감사 렌즈 우선순위, 그룹 수·라인 예산
2. 심각도별 확정 건수 표 + 합계
3. 오탐 배제 건수
4. **검증 불능 그룹**(있으면): `state.json` 의 `failed` 그룹을 `단계/그룹` 으로 명시 —
   감사 공백이므로 수동 확인 권장
5. 감사 범위 제외 목록(배제 디렉터리·패턴 + 크기 가드 등 개별 배제) — 범위 투명성

## 발견 상세 서식 (발견별 필수 요소)

각 발견은 다음을 모두 담는다:

```
### N. `파일:라인범위` · `심볼`

- **심각도**: 🔴/🟠/🟡 …  ·  **분류**: 보안/동시성/…  ·  **검증 점수**: N/5(또는 N/2)  ·  **ID**: `gX-NNN`

**결함 코드**
```
<offending snippet>
```

**메커니즘**
<헌터 rationale — 왜 결함인가>

**실패 시나리오**
<verify 산출 failure_scenario — 어떤 입력/상태 → 어떤 잘못된 결과. 재현 관점 제공>

**감정 보강** (critical/major, 있을 때)
- <항목>: <근거>

**개선 코드 샘플**
```
<fix_sample>
```

**개선 방향**
<fix_direction>
```

## 필드 매핑 (스크립트 조인 규칙)

| 보고서 항목 | 출처 |
|-------------|------|
| 위치·심볼 | `defects` finding `location` |
| 심각도 | `verified` `severity_final` (없으면 finding `severity`) |
| 분류 | `defects` finding `category` |
| 검증 점수 | `verified` `score` / (full=5, light=2) |
| 결함 코드 | `defects` finding `snippet` |
| 메커니즘 | `defects` finding `rationale` |
| 실패 시나리오 | `verified` `failure_scenario` |
| 감정 보강 | `verified` `appraisal` |
| 개선 코드/방향 | `verified` `fix_sample` / `fix_direction` |
