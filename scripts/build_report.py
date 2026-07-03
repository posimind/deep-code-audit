#!/usr/bin/env python3
"""build_report.py — verified 산출을 병합해 한국어 감사 보고서를 생성한다.

결정적 병합·렌더링만 한다. confirmed 발견을 critical→major→minor 로 정렬하고, defects
원본과 id 로 조인해 스닙·메커니즘을 채운다. 15건 초과 시 3파일로 분할한다.

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
CAT_KO = {"security": "보안", "concurrency": "동시성", "fault": "결함 처리",
          "logic": "로직", "resource": "리소스"}
SPLIT_THRESHOLD = 15


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def collect(run_dir):
    """verified 의 confirmed 를 defects 원본과 조인해 렌더 레코드로."""
    defects_dir = os.path.join(run_dir, "defects")
    verified_dir = os.path.join(run_dir, "verified")

    findings_by_id = {}
    for p in sorted(glob.glob(os.path.join(defects_dir, "*.json"))):
        base = os.path.basename(p)
        if base.endswith(".sweep.json") or base.endswith(".second.json"):
            continue
        for f in load_json(p).get("findings", []):
            findings_by_id[f["id"]] = f

    confirmed, fp_count, groups_seen = [], 0, set()
    for p in sorted(glob.glob(os.path.join(verified_dir, "*.json"))):
        base = os.path.basename(p)
        if "batch-" in base:
            continue
        obj = load_json(p)
        groups_seen.add(str(obj.get("group_id")))
        for r in obj.get("results", []):
            if r["verdict"] != "confirmed":
                fp_count += 1
                continue
            src = findings_by_id.get(r["id"], {})
            confirmed.append({
                "id": r["id"],
                "severity": r.get("severity_final", src.get("severity", "minor")),
                "category": src.get("category", "logic"),
                "location": src.get("location", {}),
                "score": r.get("score", 0),
                "rubric": r.get("rubric", "full"),
                "snippet": src.get("snippet", ""),
                "mechanism": src.get("rationale", ""),
                "failure_scenario": r.get("failure_scenario", ""),
                "fix_sample": r.get("fix_sample", ""),
                "fix_direction": r.get("fix_direction", ""),
                "appraisal": r.get("appraisal", []),
            })
    confirmed.sort(key=lambda x: (SEV_ORDER.get(x["severity"], 3),
                                  x["location"].get("file", ""),
                                  x["location"].get("start", 0)))
    return confirmed, fp_count


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
    denom = 5 if rec["rubric"] == "full" else 2
    return f"{rec['score']}/{denom}"


def render_finding(idx, rec):
    L = []
    L.append(f"### {idx}. {loc_str(rec['location'])}")
    L.append("")
    L.append(f"- **심각도**: {SEV_KO.get(rec['severity'], rec['severity'])}  ·  "
             f"**분류**: {CAT_KO.get(rec['category'], rec['category'])}  ·  "
             f"**검증 점수**: {score_str(rec)}  ·  **ID**: `{rec['id']}`")
    L.append("")
    if rec["snippet"]:
        L.append("**결함 코드**")
        L.append("```")
        L.append(rec["snippet"])
        L.append("```")
        L.append("")
    if rec["mechanism"]:
        L.append(f"**메커니즘**  \n{rec['mechanism']}")
        L.append("")
    if rec["failure_scenario"]:
        L.append(f"**실패 시나리오**  \n{rec['failure_scenario']}")
        L.append("")
    if rec["appraisal"]:
        L.append("**감정 보강**")
        for a in rec["appraisal"]:
            L.append(f"- {a.get('item', '')}: {a.get('evidence', '')}")
        L.append("")
    if rec["fix_sample"]:
        L.append("**개선 코드 샘플**")
        L.append("```")
        L.append(rec["fix_sample"])
        L.append("```")
        L.append("")
    if rec["fix_direction"]:
        L.append(f"**개선 방향**  \n{rec['fix_direction']}")
        L.append("")
    return "\n".join(L)


def counts_by_sev(confirmed):
    c = {"critical": 0, "major": 0, "minor": 0}
    for r in confirmed:
        c[r["severity"]] = c.get(r["severity"], 0) + 1
    return c


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
    if split:
        L.append("## 상세")
        L.append("")
        L.append("- critical/major 상세: [01_critical_major.md](01_critical_major.md)")
        L.append("- minor 상세: [02_minor.md](02_minor.md)")
        L.append("")
    return "\n".join(L)


def render_findings_section(title, recs):
    L = [f"# {title}", ""]
    if not recs:
        L.append("_해당 심각도의 확정 발견 없음._")
        L.append("")
        return "\n".join(L)
    for i, rec in enumerate(recs, 1):
        L.append(render_finding(i, rec))
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
    confirmed, fp_count = collect(run_dir)
    written = []

    if len(confirmed) > SPLIT_THRESHOLD:
        written.append(write(out_dir, "00_요약.md",
                             render_summary(run_dir, confirmed, fp_count, split=True)))
        cm = [r for r in confirmed if r["severity"] in ("critical", "major")]
        mn = [r for r in confirmed if r["severity"] == "minor"]
        written.append(write(out_dir, "01_critical_major.md",
                             render_findings_section("Critical / Major 상세", cm)))
        written.append(write(out_dir, "02_minor.md",
                             render_findings_section("Minor 상세", mn)))
    else:
        parts = [render_summary(run_dir, confirmed, fp_count, split=False)]
        parts.append("---")
        parts.append(render_findings_section("발견 상세", confirmed))
        written.append(write(out_dir, "감사보고서.md", "\n\n".join(parts)))

    print(json.dumps({"written": [os.path.relpath(p, out_dir) for p in written],
                      "confirmed": len(confirmed), "false_positive": fp_count},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
