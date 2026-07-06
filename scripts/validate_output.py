#!/usr/bin/env python3
"""validate_output.py — 단계 인계의 결정적 작업 일체.

판단은 모델에게, 기계적 작업만 스크립트에게. 이 스크립트는 다음만 한다(전부 결정적):
  validate       스키마 검증 + low 심각도 강등 + coverage 대조 + issues 병합 + state=done
  route-hints    cross_refs 수집 → 소유 그룹 라우팅 → 커버 대조 → hints/<gid>.json
  extract-claims defects → claims/<gid>.json (rationale 제거)
  merge          sweep/second/verify 산출 병합 (ID 유일성·기존 발견 보존 검사)
  init-state     groups.json → state.json (전부 pending)
  set-state      단계/그룹 상태를 retrying|failed|done 로 갱신 (오케스트레이터용)
  log-issue      issues.jsonl append (오케스트레이터용)

스키마 명세: references/schemas.md. 검증 실패는 nonzero exit + stderr 메시지 →
오케스트레이터가 오류를 첨부해 1회 재시도한다.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import sys

CATEGORIES = {"security", "concurrency", "fault", "logic", "resource"}
SEVERITIES = {"critical", "major", "minor"}
CONFIDENCES = {"low", "medium", "high"}
PASSES = {"primary", "sweep", "second_pass"}
TRISTATE = {"met", "unmet", "unknown"}
VERDICTS = {"confirmed", "false_positive"}
RUBRICS = {"full", "light"}
FULL_CRITERIA = ("does_this", "reachable", "harmful", "no_guard", "survives_rebuttal")
CRITERION_MARK = {"does_this": "①", "reachable": "②", "harmful": "③",
                  "no_guard": "④", "survives_rebuttal": "⑤"}
ENV_KEYS = ("os_targets", "arch_targets", "concurrency_model", "runtime", "exposure")


class ValidationError(Exception):
    pass


def _req(cond, msg):
    if not cond:
        raise ValidationError(msg)


def _is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _now():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


# --- 로드/세이브 -----------------------------------------------------------

def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False, indent=2))


def group_index(groups_file):
    """groups.json → (gid→core/low 파일집합, path→class, path→gid)."""
    gj = load_json(groups_file)
    files_of, class_of, gid_of = {}, {}, {}
    for g in gj["groups"]:
        gid = str(g["group_id"])
        files_of[gid] = set()
        for f in g["files"]:
            files_of[gid].add(f["path"])
            class_of[f["path"]] = f["class"]
            gid_of[f["path"]] = gid
    return gj, files_of, class_of, gid_of


# --- issues.jsonl ----------------------------------------------------------

def append_issue(run_dir, stage, group_id, actor, symptom, context, action,
                 outcome=""):
    line = {"ts": _now(), "stage": stage, "group_id": group_id, "actor": actor,
            "symptom": symptom, "context": context, "action": action,
            "outcome": outcome}
    path = os.path.join(run_dir, "issues.jsonl")
    os.makedirs(run_dir, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")


def merge_subagent_issues(run_dir, obj, stage, group_id, actor):
    for it in obj.get("issues", []) or []:
        append_issue(run_dir, stage, group_id, actor,
                     it.get("symptom", ""), it.get("context", ""),
                     it.get("action", ""), it.get("outcome", ""))


# --- state.json ------------------------------------------------------------

def state_path(run_dir):
    return os.path.join(run_dir, "state.json")


def load_state(run_dir):
    p = state_path(run_dir)
    if os.path.exists(p):
        return load_json(p)
    return None


def set_stage_status(run_dir, stage, group, status):
    st = load_state(run_dir)
    _req(st is not None, "state.json 없음 — init-state 먼저 실행")
    stages = st["stages"]
    if group is None:
        stages[stage] = status
    else:
        stages.setdefault(stage, {})
        stages[stage][str(group)] = status
    save_json(state_path(run_dir), st)


# --- defects 검증 ----------------------------------------------------------

def validate_finding(f, i, files_set, require_scope):
    tag = f"findings[{i}]"
    _req(isinstance(f.get("id"), str) and f["id"], f"{tag}.id 문자열 필요")
    _req(f.get("pass") in PASSES, f"{tag}.pass ∈ {sorted(PASSES)} 필요")
    _req(f.get("category") in CATEGORIES, f"{tag}.category ∈ {sorted(CATEGORIES)}")
    _req(f.get("severity") in SEVERITIES, f"{tag}.severity ∈ {sorted(SEVERITIES)}")
    _req(f.get("confidence") in CONFIDENCES, f"{tag}.confidence ∈ {sorted(CONFIDENCES)}")
    loc = f.get("location")
    _req(isinstance(loc, dict), f"{tag}.location 객체 필요")
    _req(isinstance(loc.get("file"), str) and loc["file"], f"{tag}.location.file 필요")
    _req(_is_int(loc.get("start")) and _is_int(loc.get("end")),
         f"{tag}.location.start/end 정수 필요")
    _req(loc["start"] >= 1 and loc["end"] >= loc["start"],
         f"{tag}.location start≤end, ≥1 필요")
    _req(isinstance(f.get("claim"), str) and f["claim"].strip(),
         f"{tag}.claim 비어있지 않은 문자열 필요")
    _req(isinstance(f.get("rationale"), str), f"{tag}.rationale 문자열 필요")
    _req(isinstance(f.get("snippet"), str), f"{tag}.snippet 문자열 필요")
    _req(isinstance(f.get("evidence_files", []), list), f"{tag}.evidence_files 리스트 필요")
    if require_scope and files_set is not None:
        _req(loc["file"] in files_set,
             f"{tag}.location.file '{loc['file']}' 이 그룹 파일 아님(보고 범위 위반)")


def validate_defects(obj, files_set, require_coverage, require_scope):
    _req("group_id" in obj, "group_id 필요")
    _req(isinstance(obj.get("findings"), list), "findings 리스트 필요")
    ids = set()
    for i, f in enumerate(obj["findings"]):
        validate_finding(f, i, files_set, require_scope)
        _req(f["id"] not in ids, f"finding id 중복: {f['id']}")
        ids.add(f["id"])
    for j, c in enumerate(obj.get("cross_refs", []) or []):
        tag = f"cross_refs[{j}]"
        _req(isinstance(c.get("file"), str) and c["file"], f"{tag}.file 필요")
        _req(_is_int(c.get("line")), f"{tag}.line 정수 필요")
        _req(c.get("category") in CATEGORIES,
             f"{tag}.category ∈ {sorted(CATEGORIES)} 필요(커버 판정에 사용)")
        _req(isinstance(c.get("hint"), str) and c["hint"], f"{tag}.hint 필요")
    cov = obj.get("coverage", [])
    _req(isinstance(cov, list), "coverage 리스트 필요")
    for k, cv in enumerate(cov):
        tag = f"coverage[{k}]"
        _req(isinstance(cv.get("path"), str) and cv["path"], f"{tag}.path 필요")
        _req(isinstance(cv.get("role"), str), f"{tag}.role 필요")
        _req(isinstance(cv.get("top_risk"), str), f"{tag}.top_risk 필요")


def apply_low_downgrade(obj, class_of, run_dir, stage, gid):
    changed = []
    for f in obj["findings"]:
        cls = class_of.get(f["location"]["file"])
        if cls == "low" and f["severity"] != "minor":
            append_issue(run_dir, stage, gid, "script",
                         f"low 분류 파일 {f['location']['file']} 의 {f['id']} 심각도 "
                         f"{f['severity']} → minor 기계 강등",
                         "validate 시 low 심각도 상한 적용", "severity=minor 로 수정",
                         "강등 완료")
            f["severity"] = "minor"
            changed.append(f["id"])
    return changed


def coverage_gap(obj, core_files):
    covered = {c["path"] for c in obj.get("coverage", [])}
    missing = sorted(f for f in core_files if f not in covered)
    return missing


# --- verified 검증 ---------------------------------------------------------

def validate_result(r, i):
    tag = f"results[{i}]"
    _req(isinstance(r.get("id"), str) and r["id"], f"{tag}.id 필요")
    _req(r.get("verdict") in VERDICTS, f"{tag}.verdict ∈ {sorted(VERDICTS)} 필요")
    _req(r.get("rubric") in RUBRICS, f"{tag}.rubric ∈ {sorted(RUBRICS)} 필요")
    _req(_is_int(r.get("score")), f"{tag}.score 정수 필요")
    _req(isinstance(r.get("rederivation"), str) and r["rederivation"].strip(),
         f"{tag}.rederivation 필요(anti-anchoring 준수 증거)")
    crit = r.get("criteria")
    _req(isinstance(crit, dict), f"{tag}.criteria 객체 필요")
    for key, val in crit.items():
        _req(val in TRISTATE, f"{tag}.criteria.{key} ∈ {sorted(TRISTATE)} 필요")
    _req(r.get("severity_final") in SEVERITIES,
         f"{tag}.severity_final ∈ {sorted(SEVERITIES)} 필요")

    if r["rubric"] == "full":
        for c in FULL_CRITERIA:
            _req(c in crit, f"{tag}.criteria.{c} 필요(full 룰브릭 5기준)")
    else:  # light
        for c in ("does_this", "harmful"):
            _req(c in crit, f"{tag}.criteria.{c} 필요(light 룰브릭 ①③)")

    met = sum(1 for v in crit.values() if v == "met")
    has_unmet = any(v == "unmet" for v in crit.values())
    _req(r["score"] == met, f"{tag}.score({r['score']}) ≠ met 개수({met})")

    # 격상 시 풀 룰브릭 의무.
    _req(not (r["rubric"] == "light" and r["severity_final"] in {"critical", "major"}),
         f"{tag}: light 룰브릭인데 severity_final={r['severity_final']} — "
         f"격상하려면 full 재채점 필요")

    # 규칙 6~8: full 룰브릭의 met 판정 증거 필드(light 면제).
    if r["rubric"] == "full":
        if crit.get("reachable") == "met":
            _req(isinstance(r.get("entry_path"), str) and r["entry_path"].strip(),
                 f"{tag}: reachable=met 인데 entry_path 없음(규칙 6) — "
                 f"진입점→결함 라인 호출 경로를 기록해야 met")
        if crit.get("no_guard") == "met":
            gs = r.get("guard_scan")
            _req(isinstance(gs, list) and gs
                 and all(isinstance(x, str) and x.strip() for x in gs),
                 f"{tag}: no_guard=met 인데 guard_scan 없음(규칙 7) — "
                 f"실제 확인한 방어 표면 목록을 기록해야 met")
        if crit.get("survives_rebuttal") == "met":
            _req(isinstance(r.get("rebuttal"), str) and r["rebuttal"].strip(),
                 f"{tag}: survives_rebuttal=met 인데 rebuttal 없음(규칙 8) — "
                 f"최강 반론과 그 실패 이유를 기록해야 met")

    # 판정 일관성.
    if r["verdict"] == "confirmed":
        _req(not has_unmet,
             f"{tag}: unmet 존재(게이트) 인데 confirmed — false_positive 여야 함")
        if r["rubric"] == "full":
            _req(met >= 3, f"{tag}: full met {met}<3 인데 confirmed(임계 위반)")
        else:
            _req(crit.get("does_this") == "met" and crit.get("harmful") == "met",
                 f"{tag}: light ①③ 미충족인데 confirmed")
            _req(crit.get("no_guard") != "unmet",
                 f"{tag}: light 명백 방어 unmet(게이트) 인데 confirmed")
        _req(isinstance(r.get("failure_scenario"), str) and r["failure_scenario"].strip(),
             f"{tag}: confirmed 는 failure_scenario 필수")
    else:  # false_positive
        _req(isinstance(r.get("reject_reason"), str) and r["reject_reason"].strip(),
             f"{tag}: false_positive 는 reject_reason 필수")
        if has_unmet:
            # 규칙 4 확장: 게이트 기각은 어느 기준이 unmet 인지 명시(unmet 오판=미탐 방지).
            rr = r["reject_reason"]
            unmet_keys = [k for k, v in crit.items() if v == "unmet"]
            _req(any(k in rr or CRITERION_MARK.get(k, "") in rr for k in unmet_keys),
                 f"{tag}: 게이트 기각인데 reject_reason 이 unmet 기준을 명시하지 않음 — "
                 f"unmet 기준({unmet_keys})의 키 이름 또는 ①~⑤ 표기와 반증 근거를 인용")
        elif r["rubric"] == "full":
            # 규칙 9: 임계 미달 기각 전 unknown 해소 시도 이력 필수.
            ap = r.get("appraisal")
            _req(isinstance(ap, list) and len(ap) > 0,
                 f"{tag}: 임계 미달 기각(unmet 없는 full false_positive)인데 appraisal "
                 f"비어 있음(규칙 9) — 기각 전 unknown 기준의 해소 시도 이력을 남겨야 함")


def validate_verified(obj):
    _req("group_id" in obj, "group_id 필요")
    _req(isinstance(obj.get("results"), list), "results 리스트 필요")
    ids = set()
    for i, r in enumerate(obj["results"]):
        validate_result(r, i)
        _req(r["id"] not in ids, f"result id 중복: {r['id']}")
        ids.add(r["id"])


# --- 커맨드: validate ------------------------------------------------------

STAGE_FILE = {
    "hunt": ("defects", "{gid}.json"),
    "sweep": ("defects", "{gid}.sweep.json"),
    "second": ("defects", "{gid}.second.json"),
    "verify": ("verified", "{gid}.json"),
}


def cmd_validate(args):
    run_dir = args.run_dir
    gid = str(args.group)
    stage = args.stage
    if args.file:
        target = args.file
    else:
        sub, pat = STAGE_FILE[stage]
        target = os.path.join(run_dir, sub, pat.format(gid=gid))

    groups_file = args.groups_file or os.path.join(run_dir, "groups.json")
    files_set = core_files = None
    class_of = {}
    if os.path.exists(groups_file):
        _, files_of, class_of, _ = group_index(groups_file)
        # subgroup(4a) 검증 시 해당 gid 우선, 없으면 원본 gid 파일집합.
        files_set = files_of.get(gid)
        if files_set is not None:
            gj = load_json(groups_file)
            for g in gj["groups"]:
                if str(g["group_id"]) == gid:
                    core_files = {f["path"] for f in g["files"] if f["class"] == "core"}
                    break

    try:
        obj = load_json(target)
    except (OSError, json.JSONDecodeError) as e:
        _fail(run_dir, stage, gid, f"JSON 파싱 실패: {e}", target,
              actor=_actor(stage), code=2)

    try:
        if stage == "verify":
            validate_verified(obj)
        else:
            require_cov = (stage == "hunt") and not args.no_coverage
            validate_defects(obj, files_set, require_cov,
                             require_scope=(files_set is not None))
            # low 심각도 강등(결정적 안전망).
            if class_of:
                apply_low_downgrade(obj, class_of, run_dir, stage, gid)
            # coverage 대조.
            if require_cov and core_files is not None:
                missing = coverage_gap(obj, core_files)
                if missing:
                    append_issue(run_dir, stage, gid, "script",
                                 f"미커버 core 파일 {len(missing)}건: {missing}",
                                 "coverage 대조", "미커버 목록 첨부해 재시도 요청",
                                 "재시도 대기")
                    _fail(run_dir, stage, gid,
                          "미커버 core 파일이 있습니다(정독 누락 의심). 아래 파일을 "
                          "정독하고 coverage 에 파일별 증거를 추가해 다시 제출하세요:\n  - "
                          + "\n  - ".join(missing), target, actor=_actor(stage),
                          code=3, already_logged=True)
    except ValidationError as e:
        _fail(run_dir, stage, gid, str(e), target, actor=_actor(stage), code=2)

    # 변경분(low 강등) 반영 저장.
    if stage != "verify":
        save_json(target, obj)

    # 서브에이전트 issues 병합 → issues.jsonl.
    merge_subagent_issues(run_dir, obj, stage, gid, _actor(stage))

    # state 갱신.
    if load_state(run_dir) is not None:
        set_stage_status(run_dir, stage, gid, "done")
    print(f"[validate] {stage} g{gid} OK ({os.path.basename(target)})")
    return 0


def _actor(stage):
    return "verifier" if stage == "verify" else "hunter"


def _fail(run_dir, stage, gid, msg, target, actor, code, already_logged=False):
    if not already_logged:
        append_issue(run_dir, stage, gid, "script",
                     f"스키마/일관성 불합격: {msg}",
                     f"산출 {os.path.relpath(target, run_dir) if target else '?'}",
                     "오류 메시지 첨부해 동일 에이전트에 재시도 요청", "재시도 대기")
    sys.stderr.write(f"[validate:FAIL] {stage} g{gid}\n{msg}\n")
    raise SystemExit(code)


# --- 커맨드: route-hints ---------------------------------------------------

def _covered(cref, findings):
    for f in findings:
        loc = f["location"]
        if (loc["file"] == cref["file"]
                and loc["start"] <= cref["line"] <= loc["end"]
                and f["category"] == cref["category"]):
            return True
    return False


def cmd_route_hints(args):
    run_dir = args.run_dir
    groups_file = args.groups_file or os.path.join(run_dir, "groups.json")
    _, _, _, gid_of = group_index(groups_file)

    defects_dir = os.path.join(run_dir, "defects")
    findings_by_gid = {}
    all_crefs = []
    for path in sorted(glob.glob(os.path.join(defects_dir, "*.json"))):
        base = os.path.basename(path)
        if base.endswith(".sweep.json") or base.endswith(".second.json"):
            continue
        obj = load_json(path)
        src_gid = str(obj["group_id"])
        findings_by_gid[src_gid] = obj.get("findings", [])
        for c in obj.get("cross_refs", []) or []:
            all_crefs.append((src_gid, c))

    routed = {}
    stats = {"total": len(all_crefs), "discarded_out_of_scope": 0,
             "covered": 0, "routed": 0}
    for src_gid, c in all_crefs:
        owner = gid_of.get(c["file"])
        if owner is None:
            stats["discarded_out_of_scope"] += 1
            continue
        if _covered(c, findings_by_gid.get(owner, [])):
            stats["covered"] += 1
            continue
        routed.setdefault(owner, []).append({
            "file": c["file"], "line": c["line"], "category": c["category"],
            "hint": c["hint"], "from_group": src_gid})
        stats["routed"] += 1

    hints_dir = os.path.join(run_dir, "hints")
    for owner, hints in routed.items():
        save_json(os.path.join(hints_dir, f"{owner}.json"),
                  {"group_id": owner, "hints": hints})

    append_issue(run_dir, "sweep", None, "script",
                 f"힌트 라우팅 결과: {stats}", "route-hints",
                 f"{len(routed)}개 그룹으로 hints/<gid>.json 생성", "완료")
    print(json.dumps({"stats": stats, "groups": sorted(routed)},
                     ensure_ascii=False))
    return 0


# --- 커맨드: extract-claims ------------------------------------------------

def cmd_extract_claims(args):
    run_dir = args.run_dir
    defects_dir = os.path.join(run_dir, "defects")
    claims_dir = os.path.join(run_dir, "claims")
    gids = [str(args.group)] if args.group else [
        os.path.basename(p)[:-5]
        for p in sorted(glob.glob(os.path.join(defects_dir, "*.json")))
        if not (p.endswith(".sweep.json") or p.endswith(".second.json"))]
    made = []
    for gid in gids:
        src = os.path.join(defects_dir, f"{gid}.json")
        if not os.path.exists(src):
            continue
        obj = load_json(src)
        claims = [{"id": f["id"], "severity": f["severity"],
                   "location": f["location"], "claim": f["claim"]}
                  for f in obj.get("findings", [])]
        save_json(os.path.join(claims_dir, f"{gid}.json"),
                  {"group_id": obj["group_id"], "claims": claims})
        made.append(gid)
    print(json.dumps({"claims_for": made}, ensure_ascii=False))
    return 0


# --- 커맨드: merge ---------------------------------------------------------

def _overlap(a, b):
    la, lb = a["location"], b["location"]
    if la["file"] != lb["file"]:
        return False
    return not (la["end"] < lb["start"] or lb["end"] < la["start"])


def cmd_merge(args):
    run_dir = args.run_dir
    gid = str(args.group)
    kind = args.kind
    defects_dir = os.path.join(run_dir, "defects")
    verified_dir = os.path.join(run_dir, "verified")

    if kind in ("sweep", "second"):
        base_path = os.path.join(defects_dir, f"{gid}.json")
        add_path = os.path.join(defects_dir, f"{gid}.{kind}.json")
        base = load_json(base_path)
        base_ids = {f["id"] for f in base["findings"]}
        base_ids_before = set(base_ids)
        if not os.path.exists(add_path):
            print(f"[merge] {kind} g{gid}: 추가 파일 없음, noop")
            return 0
        add = load_json(add_path)
        added, skipped = 0, 0
        for f in add.get("findings", []):
            if f["id"] in base_ids:
                raise SystemExit(f"[merge:FAIL] ID 충돌 {f['id']} — 접두사 규약 위반")
            if kind == "second" and any(_overlap(f, e) and f["category"] == e["category"]
                                        for e in base["findings"]):
                skipped += 1
                continue
            base["findings"].append(f)
            base_ids.add(f["id"])
            added += 1
        # 기존 발견 보존(상위집합) 검사 — 병합 결과가 병합 전 모든 ID 를 포함해야 함.
        after_ids = {f["id"] for f in base["findings"]}
        missing = base_ids_before - after_ids
        _req(not missing, f"기존 발견 소실: {sorted(missing)}")
        save_json(base_path, base)
        append_issue(run_dir, kind, gid, "script",
                     f"{kind} 병합: +{added} 중복스킵 {skipped}", "merge",
                     "defects/<gid>.json 갱신", "완료")
        print(f"[merge] {kind} g{gid}: +{added}, skipped {skipped}")
        return 0

    if kind == "verify":
        batches = sorted(glob.glob(os.path.join(verified_dir, f"{gid}.batch-*.json")))
        if not batches:
            print(f"[merge] verify g{gid}: batch 파일 없음, noop")
            return 0
        results, ids = [], set()
        for bp in batches:
            bobj = load_json(bp)
            for r in bobj.get("results", []):
                if r["id"] in ids:
                    raise SystemExit(f"[merge:FAIL] verify ID 충돌 {r['id']}")
                ids.add(r["id"])
                results.append(r)
            merge_subagent_issues(run_dir, bobj, "verify", gid, "verifier")
        save_json(os.path.join(verified_dir, f"{gid}.json"),
                  {"group_id": gid, "results": results, "issues": []})
        print(f"[merge] verify g{gid}: {len(results)} results from {len(batches)} batches")
        return 0
    return 1


# --- brief.environment 형태 검증 (§1, 존재 시에만) --------------------------

def validate_environment(env):
    _req(isinstance(env, dict), "brief.environment 객체 필요")
    extra = set(env) - set(ENV_KEYS)
    _req(not extra, f"brief.environment 허용 밖 키: {sorted(extra)}")
    for k in ENV_KEYS:
        _req(k in env, f"brief.environment.{k} 필요 — 근거를 못 찾으면 "
                       f'value="unknown" 으로 남기고 evidence 에 확인 시도 위치 기록')
        item = env[k]
        _req(isinstance(item, dict), f"brief.environment.{k} 객체(value+evidence) 필요")
        v = item.get("value")
        v_ok = (isinstance(v, str) and v.strip()) or (
            isinstance(v, list) and v
            and all(isinstance(x, str) and x.strip() for x in v))
        _req(v_ok, f"brief.environment.{k}.value 문자열 또는 문자열 배열 필요")
        _req(isinstance(item.get("evidence"), str) and item["evidence"].strip(),
             f"brief.environment.{k}.evidence 필요(단정의 근거 위치, "
             f"unknown 이면 확인 시도한 위치)")


# --- 커맨드: init-state / set-state / log-issue ----------------------------

def cmd_init_state(args):
    run_dir = args.run_dir
    gj = load_json(args.groups_file or os.path.join(run_dir, "groups.json"))
    env = (gj.get("brief") or {}).get("environment")
    if env is not None:
        try:
            validate_environment(env)
        except ValidationError as e:
            sys.stderr.write(f"[init-state:FAIL] {e}\n")
            raise SystemExit(2)
    gids = [str(g["group_id"]) for g in gj["groups"]]
    hr = {str(g["group_id"]) for g in gj["groups"] if g.get("high_risk")}
    st = {
        "run_id": gj.get("run_id", ""),
        "target_root": gj.get("target_root", ""),
        "stages": {
            "grouping": "done",
            "hunt": {g: "pending" for g in gids},
            "sweep": {},
            "second": {g: "pending" for g in gids if g in hr},
            "verify": {g: "pending" for g in gids},
            "report": "pending",
        },
    }
    save_json(state_path(run_dir), st)
    print(f"[init-state] {len(gids)} groups, {len(hr)} high_risk")
    return 0


def cmd_set_state(args):
    group = None if args.group in (None, "", "-") else args.group
    set_stage_status(args.run_dir, args.stage, group, args.status)
    print(f"[set-state] {args.stage} {args.group} = {args.status}")
    return 0


def cmd_log_issue(args):
    group = None if args.group in (None, "", "-") else args.group
    append_issue(args.run_dir, args.stage, group, args.actor, args.symptom,
                 args.context, args.action, args.outcome)
    print("[log-issue] recorded")
    return 0


# --- CLI -------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="deep-code-audit 단계 인계 검증·병합")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="스키마 검증 + low 강등 + coverage + state")
    v.add_argument("--stage", required=True, choices=list(STAGE_FILE))
    v.add_argument("--group", required=True)
    v.add_argument("--run-dir", required=True)
    v.add_argument("--file", default=None, help="검증 대상 JSON(미지정 시 stage 기본 경로)")
    v.add_argument("--groups-file", default=None)
    v.add_argument("--no-coverage", action="store_true",
                   help="coverage 대조 생략(sweep/second 산출 검증 시)")
    v.set_defaults(func=cmd_validate)

    r = sub.add_parser("route-hints", help="cross_refs → hints/<gid>.json")
    r.add_argument("--run-dir", required=True)
    r.add_argument("--groups-file", default=None)
    r.set_defaults(func=cmd_route_hints)

    e = sub.add_parser("extract-claims", help="defects → claims/<gid>.json")
    e.add_argument("--run-dir", required=True)
    e.add_argument("--group", default=None)
    e.set_defaults(func=cmd_extract_claims)

    m = sub.add_parser("merge", help="sweep/second/verify 병합")
    m.add_argument("--kind", required=True, choices=["sweep", "second", "verify"])
    m.add_argument("--group", required=True)
    m.add_argument("--run-dir", required=True)
    m.set_defaults(func=cmd_merge)

    i = sub.add_parser("init-state", help="groups.json → state.json")
    i.add_argument("--run-dir", required=True)
    i.add_argument("--groups-file", default=None)
    i.set_defaults(func=cmd_init_state)

    s = sub.add_parser("set-state", help="단계/그룹 상태 갱신")
    s.add_argument("--run-dir", required=True)
    s.add_argument("--stage", required=True)
    s.add_argument("--group", default=None)
    s.add_argument("--status", required=True,
                   choices=["pending", "retrying", "done", "failed"])
    s.set_defaults(func=cmd_set_state)

    li = sub.add_parser("log-issue", help="issues.jsonl append")
    li.add_argument("--run-dir", required=True)
    li.add_argument("--stage", required=True)
    li.add_argument("--group", default=None)
    li.add_argument("--actor", required=True,
                    choices=["orchestrator", "hunter", "verifier", "script"])
    li.add_argument("--symptom", required=True)
    li.add_argument("--context", default="")
    li.add_argument("--action", default="")
    li.add_argument("--outcome", default="")
    li.set_defaults(func=cmd_log_issue)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
