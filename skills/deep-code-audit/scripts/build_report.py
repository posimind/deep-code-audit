#!/usr/bin/env python3
"""build_report.py — verified 산출을 병합해 한국어 감사 보고서를 생성한다.

결정적 병합·렌더링만 한다. confirmed 발견을 critical→major→minor 로 정렬하고, defects
원본과 id 로 조인해 렌더한다. 기준 독자는 코드베이스에 낯선 검토자다: 제목=claim,
파일 역할(coverage 조인)·영향(impact)·도달 경로(entry_path)를 싣고, 검증 증거
(rebuttal·guard_scan·appraisal)는 접이식 검증 노트로, 요약부에는 용어 범례와 발견
색인 표를 넣는다. 15건 초과 시 3파일로 분할한다.

입력: <run-dir>/{groups.json, state.json, verified/*.json, defects/*.json}
출력: <out-dir>/감사보고서.md  또는  00_요약.md / 01_critical_major.md / 02_minor.md
"""
from __future__ import annotations

import argparse
import glob
import json
import os

SEV_ORDER = {"critical": 0, "major": 1, "minor": 2}
SEV_KO = {"critical": "🔴 Critical", "major": "🟠 Major", "minor": "🟡 Minor"}
SEV_EMOJI = {"critical": "🔴", "major": "🟠", "minor": "🟡"}
CAT_KO = {"security": "보안", "concurrency": "동시성", "fault": "결함 처리",
          "logic": "로직", "resource": "리소스"}
CRITERION_MARK = {"does_this": "①", "reachable": "②", "harmful": "③",
                  "no_guard": "④", "survives_rebuttal": "⑤"}
SPLIT_THRESHOLD = 15
# 보고서 전용 파일명 — 재생성 시 이름 기준으로 선(先)정리한다(분할 ↔ 단일 전환에서
# 이전 포맷이 잔존하면 어느 쪽이 최종본인지 독자가 알 수 없다).
REPORT_NAMES = ("감사보고서.md", "00_요약.md", "01_critical_major.md", "02_minor.md")

# 코드 펜스 언어 태그(문법 강조용) — 매핑 없는 확장자는 태그 없이 렌더.
LANG_BY_EXT = {
    ".py": "python", ".js": "javascript", ".jsx": "jsx", ".ts": "typescript",
    ".tsx": "tsx", ".mjs": "javascript", ".cjs": "javascript",
    ".rs": "rust", ".go": "go", ".java": "java", ".kt": "kotlin",
    ".kts": "kotlin", ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp",
    ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp", ".rb": "ruby", ".php": "php",
    ".swift": "swift", ".sh": "bash", ".bash": "bash", ".sql": "sql",
    ".yml": "yaml", ".yaml": "yaml", ".json": "json", ".tf": "hcl",
}


def lang_for(path):
    return LANG_BY_EXT.get(os.path.splitext(path or "")[1].lower(), "")


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def collect(run_dir):
    """verified 의 confirmed 를 defects 원본과 조인해 렌더 레코드로.

    반환 3번째 값 file_roles 는 헌터 coverage 의 파일별 한 줄 역할 요약
    (path → role) — 코드베이스에 낯선 독자를 위한 파일 맥락 조인에 쓴다.
    """
    defects_dir = os.path.join(run_dir, "defects")
    verified_dir = os.path.join(run_dir, "verified")

    findings_by_id, file_roles = {}, {}
    for p in sorted(glob.glob(os.path.join(defects_dir, "*.json"))):
        base = os.path.basename(p)
        if base.endswith(".sweep.json") or base.endswith(".second.json"):
            continue
        obj = load_json(p)
        for f in obj.get("findings", []):
            findings_by_id[f["id"]] = f
        for c in obj.get("coverage", []):
            if c.get("path") and c.get("role"):
                file_roles.setdefault(c["path"], c["role"])

    confirmed, fp_count, groups_seen, seen_ids = [], 0, set(), set()
    for p in sorted(glob.glob(os.path.join(verified_dir, "*.json"))):
        base = os.path.basename(p)
        # batch-*: 병합 이전 조각. arb-*: 3c 교차그룹 중재 산출 — 권위 판정은 항상
        # 그룹 verified/<gid>.json 에 있다(apply 면 set-verdict 가 이식했고, uphold 면
        # 그룹 파일이 이미 옳다). 함께 집계하면 동일 발견이 이중집계된다.
        if "batch-" in base or base.startswith("arb-"):
            continue
        obj = load_json(p)
        groups_seen.add(str(obj.get("group_id")))
        for r in obj.get("results", []):
            # 심층 방어 — 발견 ID 는 g<gid>- 접두로 전역 유일하므로, 어떤 경로로든
            # 같은 ID 가 다시 보이면 이미 집계된 결과다(정렬상 그룹 파일이 먼저 온다).
            if r["id"] in seen_ids:
                continue
            seen_ids.add(r["id"])
            if r["verdict"] != "confirmed":
                fp_count += 1
                continue
            src = findings_by_id.get(r["id"], {})
            confirmed.append({
                "id": r["id"],
                "severity": r.get("severity_final", src.get("severity", "minor")),
                "category": src.get("category", "logic"),
                "location": src.get("location", {}),
                "claim": src.get("claim", ""),
                "score": r.get("score", 0),
                "rubric": r.get("rubric", "full"),
                "criteria": r.get("criteria", {}),
                "snippet": src.get("snippet", ""),
                "mechanism": src.get("rationale", ""),
                "impact": r.get("impact", ""),
                "entry_path": r.get("entry_path", ""),
                "guard_scan": r.get("guard_scan", []),
                "rebuttal": r.get("rebuttal", ""),
                "failure_scenario": r.get("failure_scenario", ""),
                "fix_sample": r.get("fix_sample", ""),
                "fix_direction": r.get("fix_direction", ""),
                "appraisal": r.get("appraisal", []),
            })
    confirmed.sort(key=lambda x: (SEV_ORDER.get(x["severity"], 3),
                                  x["location"].get("file", ""),
                                  x["location"].get("start", 0)))
    return confirmed, fp_count, file_roles


def residue_hints(run_dir):
    """sweep 병합 후 미소진 힌트(hints/residue.json — route-hints --residue-check)."""
    p = os.path.join(run_dir, "hints", "residue.json")
    if not os.path.exists(p):
        return []
    return load_json(p).get("hints", [])


def failed_groups(run_dir):
    st_path = os.path.join(run_dir, "state.json")
    if not os.path.exists(st_path):
        return []
    st = load_json(st_path)
    out = []
    for stage, val in st.get("stages", {}).items():
        if isinstance(val, dict):
            for gid, status in val.items():
                if status == "failed":
                    out.append(f"{stage}/{gid}")
    return sorted(set(out))


def loc_str(loc):
    f = loc.get("file", "?")
    s, e = loc.get("start"), loc.get("end")
    sym = loc.get("symbol")
    span = f"{s}" if s == e else f"{s}–{e}"
    tail = f" · `{sym}`" if sym else ""
    return f"`{f}:{span}`{tail}"


def score_str(rec):
    # 분모 = 기재된 기준 수 (full 5, light 2~3 — no_guard 기재 여부에 따라).
    crit = rec.get("criteria") or {}
    denom = len(crit) if crit else (5 if rec["rubric"] == "full" else 2)
    return f"{rec['score']}/{denom}"


def unknown_criteria(rec):
    return [k for k in CRITERION_MARK
            if rec.get("criteria", {}).get(k) == "unknown"]


def render_finding(idx, rec, file_roles=None):
    unknowns = unknown_criteria(rec)
    badge = " **[조건부]**" if unknowns else ""
    emoji = SEV_EMOJI.get(rec["severity"], "")
    loc = rec["location"]
    lang = lang_for(loc.get("file", ""))
    # 제목 = claim(무엇이 문제인가) — 위치만으로는 낯선 독자에게 정보가 없다.
    # claim 이 없는 구 런 산출은 위치로 폴백.
    title = rec.get("claim") or loc_str(loc)
    L = []
    L.append(f"### {idx}. {emoji}{badge} {title}")
    L.append("")
    L.append(f"- **위치**: {loc_str(loc)}")
    L.append(f"- **심각도**: {SEV_KO.get(rec['severity'], rec['severity'])}  ·  "
             f"**분류**: {CAT_KO.get(rec['category'], rec['category'])}  ·  "
             f"**검증 점수**: {score_str(rec)}  ·  **ID**: `{rec['id']}`")
    role = (file_roles or {}).get(loc.get("file", ""))
    if role:
        L.append(f"- **파일 역할**: {role}")
    L.append("")
    if unknowns:
        marks = ", ".join(f"{CRITERION_MARK[k]}{k}" for k in unknowns)
        L.append(f"> **[조건부]** 미확정(unknown) 기준: {marks} — 실패 시나리오에 "
                 f"명시된 전제가 참일 때 성립하는 발견이다.")
        L.append("")
    if rec.get("impact"):
        L.append(f"**영향**  \n{rec['impact']}")
        L.append("")
    if rec["snippet"]:
        L.append(f"**결함 코드** — {loc_str(loc)}")
        L.append(f"```{lang}")
        L.append(rec["snippet"])
        L.append("```")
        L.append("")
    if rec.get("entry_path"):
        L.append(f"**도달 경로**  \n`{rec['entry_path']}`")
        L.append("")
    if rec["mechanism"]:
        L.append(f"**메커니즘**  \n{rec['mechanism']}")
        L.append("")
    if rec["failure_scenario"]:
        L.append(f"**실패 시나리오**  \n{rec['failure_scenario']}")
        L.append("")
    L.extend(render_verify_note(rec))
    if rec["fix_sample"]:
        L.append("**개선 코드 샘플**")
        L.append(f"```{lang}")
        L.append(rec["fix_sample"])
        L.append("```")
        L.append("")
    if rec["fix_direction"]:
        L.append(f"**개선 방향**  \n{rec['fix_direction']}")
        L.append("")
    return "\n".join(L)


def render_verify_note(rec):
    """검증 과정 증거(반론·방어 표면·추가 확인)를 접이식 블록으로.

    본문 흐름(무엇이·왜·어떻게 고치나)과 분리해 소음을 줄이되, "이거 오탐
    아니야?"라고 묻는 검토자가 펼치면 검증자의 반박 시도 근거를 볼 수 있게 한다.
    """
    rebuttal = rec.get("rebuttal", "")
    guards = rec.get("guard_scan", [])
    appraisal = rec.get("appraisal", [])
    if not (rebuttal or guards or appraisal):
        return []
    L = []
    L.append("<details>")
    L.append("<summary><b>검증 노트</b> — 검토한 반론·확인한 방어 표면</summary>")
    L.append("")
    if rebuttal:
        L.append(f"**검토한 최강 반론과 기각 사유**  \n{rebuttal}")
        L.append("")
    if guards:
        L.append("**확인한 방어 표면** (이미 막고 있는 곳이 없는지 실제 탐색한 위치)")
        for g in guards:
            L.append(f"- {g}")
        L.append("")
    if appraisal:
        L.append("**추가 확인 이력** (미확정 기준을 좁히기 위한 추적)")
        for a in appraisal:
            # 스키마는 {item, evidence} 형태지만 validate 가 리스트 여부만 검사하므로
            # 문자열 항목도 통과한다 — 렌더에서 죽지 않게 둘 다 수용.
            if isinstance(a, dict):
                L.append(f"- {a.get('item', '')}: {a.get('evidence', '')}")
            else:
                L.append(f"- {a}")
        L.append("")
    L.append("</details>")
    L.append("")
    return L


def counts_by_sev(confirmed):
    c = {"critical": 0, "major": 0, "minor": 0}
    for r in confirmed:
        c[r["severity"]] = c.get(r["severity"], 0) + 1
    return c


# 심각도 3줄 정의는 agents/deep-audit-{hunter,verifier}.md 의 "Severity criteria" 절과
# 동일 문구여야 한다 — 수정 시 세 곳 동기화(에이전트 본문은 영어지만 이 3줄은
# 한국어 원문 유지 — render_legend 가 보고서에 그대로 렌더한다).
def render_legend():
    """처음 보는 검토자용 용어 안내 — 접이식(익숙한 독자의 흐름을 막지 않게)."""
    return [
        "<details>",
        "<summary><b>이 보고서를 읽는 법</b> — 처음 보는 분을 위한 용어 안내</summary>",
        "",
        "- **심각도** — 🔴 Critical: 정상 사용 흐름에서 악용·데이터 손실·크래시로 "
        "이어짐 · 🟠 Major: 특정 조건에서 심각한 오작동·데이터 오염·보안 약화 · "
        "🟡 Minor: 국소적 품질·견고성 결함, 실피해가 제한적",
        "- **검증 점수** — 모든 발견은 탐지 에이전트와 별도의 검증 에이전트가 5개 "
        "기준(① 코드가 실제로 그렇게 동작함 ② 그 코드가 실제로 실행될 수 있음 "
        "③ 결과가 실질적 피해임 ④ 상류에 이미 막는 방어가 없음 ⑤ 반론을 견딤)으로 "
        "재검증한 뒤에만 실린다. 점수는 참으로 확인된 기준 수(minor 는 경량 검증이라 "
        "분모가 2~3).",
        "- **[조건부]** — 일부 기준이 저장소만으로는 확정 불가(unknown)라, 실패 "
        "시나리오에 명시된 전제(배포 구성 등)가 참일 때 성립하는 발견.",
        "- **오탐(false positive)** — 검증에서 기각되어 이 보고서에 싣지 않은 보고. "
        "건수만 집계에 남긴다.",
        "- 각 발견의 접힌 **검증 노트**에는 검증자가 검토한 반론과 실제 확인한 방어 "
        "표면이 담겨 있다 — \"이거 오탐 아닌가?\" 싶을 때 먼저 펼쳐 보라.",
        "",
        "</details>",
        "",
    ]


def md_cell(text):
    """표 셀용 이스케이프 — 파이프·개행이 표 구조를 깨지 않게."""
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_index(confirmed, split):
    """발견 색인 표 — 상세를 읽기 전 전체를 조망하고 우선순위를 정하는 용도."""
    if not confirmed:
        return []
    L = ["## 발견 목록", ""]
    L.append("| ID | 심각도 | 분류 | 요지 | 위치 |")
    L.append("|----|--------|------|------|------|")
    for rec in confirmed:
        sev = SEV_KO.get(rec["severity"], rec["severity"])
        cat = CAT_KO.get(rec["category"], rec["category"])
        claim = md_cell(rec.get("claim") or "(요지 미기재)")
        L.append(f"| `{rec['id']}` | {sev} | {cat} | {claim} "
                 f"| {md_cell(loc_str(rec['location']))} |")
    L.append("")
    if split:
        L.append("상세 설명은 심각도별 파일에 있다: "
                 "critical/major → [01_critical_major.md](01_critical_major.md), "
                 "minor → [02_minor.md](02_minor.md). ID 로 찾으면 된다.")
        L.append("")
    return L


def render_summary(run_dir, confirmed, fp_count, split):
    gj = load_json(os.path.join(run_dir, "groups.json"))
    brief = gj.get("brief", {})
    c = counts_by_sev(confirmed)
    failed = failed_groups(run_dir)
    L = []
    L.append("# 코드 감사 보고서")
    L.append("")
    L.append(f"- **대상**: `{gj.get('target_root', '?')}`")
    L.append(f"- **Run ID**: `{gj.get('run_id', '?')}`")
    if brief.get("purpose"):
        L.append(f"- **프로젝트**: {brief.get('project_type', '')} — {brief['purpose']}")
    if brief.get("lens_priority"):
        L.append(f"- **감사 렌즈 우선순위**: {' > '.join(brief['lens_priority'])}")
    L.append(f"- **그룹 수**: {len(gj.get('groups', []))}  ·  "
             f"**라인 예산**: {gj.get('line_budget', '?')}")
    L.append("")
    L.extend(render_legend())
    L.append("## 집계")
    L.append("")
    L.append("| 심각도 | 확정 건수 |")
    L.append("|--------|-----------|")
    L.append(f"| 🔴 Critical | {c['critical']} |")
    L.append(f"| 🟠 Major | {c['major']} |")
    L.append(f"| 🟡 Minor | {c['minor']} |")
    L.append(f"| **합계** | **{len(confirmed)}** |")
    L.append("")
    L.append(f"- 검증에서 배제된 오탐(false positive): {fp_count}건")
    if failed:
        L.append(f"- ⚠️ **검증 불능 그룹**(재시도·폴백까지 실패): {', '.join(failed)} "
                 f"— 해당 그룹은 감사 공백이므로 수동 확인 권장")
    L.append("")
    L.extend(render_index(confirmed, split))
    residue = residue_hints(run_dir)
    if residue:
        L.append("## 미소진 힌트 (추가 확인 권장 지점)")
        L.append("")
        L.append("보강 패스(sweep) 헌터가 조사 중 새로 남긴 타 그룹 결함 징후로, 이번 "
                 "런의 자동 조사가 소진하지 못한 힌트다. 검증을 거치지 않은 의심 "
                 "신호이므로 발견이 아닌 수동 확인 후보로 다뤄라:")
        L.append("")
        for h in residue:
            cat = CAT_KO.get(h.get("category", ""), h.get("category", "?"))
            L.append(f"- `{h.get('file', '?')}:{h.get('line', '?')}` [{cat}] "
                     f"{h.get('hint', '')} (g{h.get('from_group', '?')} 발신)")
        L.append("")
    excluded = gj.get("excluded", [])
    if excluded:
        L.append("## 감사 범위 제외")
        L.append("")
        L.append("다음은 스캔에서 제외됨(벤더·빌드 산출물·생성 코드·바이너리 등):")
        L.append("")
        for x in excluded:
            L.append(f"- `{x}`")
        exf = gj.get("excluded_files", [])
        if exf:
            L.append("")
            L.append("크기 가드 등 개별 배제:")
            for x in exf[:20]:
                L.append(f"- `{x['path']}` — {x['reason']}")
            if len(exf) > 20:
                L.append(f"- … 외 {len(exf) - 20}건")
        L.append("")
    return "\n".join(L)


def render_findings_section(title, recs, file_roles=None):
    L = [f"# {title}", ""]
    if not recs:
        L.append("_해당 심각도의 확정 발견 없음._")
        L.append("")
        return "\n".join(L)
    for i, rec in enumerate(recs, 1):
        L.append(render_finding(i, rec, file_roles))
    return "\n".join(L)


def write(out_dir, name, text):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.rstrip() + "\n")
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description="verified → 한국어 감사 보고서")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--out-dir", default=None,
                    help="보고서 출력 디렉터리(기본: run-dir)")
    args = ap.parse_args(argv)

    run_dir = args.run_dir
    out_dir = args.out_dir or run_dir
    confirmed, fp_count, file_roles = collect(run_dir)

    # 렌더를 메모리에 먼저 완성한다 — 이 단계에서 실패하면(groups.json 부재 등)
    # 기존 보고서가 그대로 남는다. stale 정리를 렌더보다 앞세우면 실패 시 직전
    # 보고서만 파괴된 채 종료하는 창이 생긴다.
    if len(confirmed) > SPLIT_THRESHOLD:
        cm = [r for r in confirmed if r["severity"] in ("critical", "major")]
        mn = [r for r in confirmed if r["severity"] == "minor"]
        outputs = [
            ("00_요약.md",
             render_summary(run_dir, confirmed, fp_count, split=True)),
            ("01_critical_major.md",
             render_findings_section("Critical / Major 상세", cm, file_roles)),
            ("02_minor.md",
             render_findings_section("Minor 상세", mn, file_roles)),
        ]
    else:
        parts = [render_summary(run_dir, confirmed, fp_count, split=False)]
        parts.append("---")
        parts.append(render_findings_section("발견 상세", confirmed, file_roles))
        outputs = [("감사보고서.md", "\n\n".join(parts))]

    for stale in REPORT_NAMES:
        try:
            os.remove(os.path.join(out_dir, stale))
        except FileNotFoundError:
            pass
    written = [write(out_dir, name, text) for name, text in outputs]

    print(json.dumps({"written": [os.path.relpath(p, out_dir) for p in written],
                      "confirmed": len(confirmed), "false_positive": fp_count},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
