#!/usr/bin/env python3
"""group_by_lines.py — 라인 수 균형 + import 그래프 응집으로 파일을 그룹으로 묶는다.

큰 그룹·적은 에이전트 원칙: 그룹 경계(seam)는 cross-file 결함 미탐의 주요 원인이므로
예산(기본 10,000라인)을 크게 잡아 경계 수를 줄인다. 예산을 넘는 연결 요소는 결합도가
가장 높은 코어이므로 라인 수 임의 절단이 아니라 **절단 import 간선 최소화**(동률이면
디렉터리 경계)로 분할하고, 절단된 간선은 양쪽 그룹에 `seam_hints`로 기록해 헌터
프롬프트에 주입한다("경계를 없앨 수 없으면 경계를 조명한다").

입력: select_targets.py 산출 targets.json + brief.json
출력: groups.json (스키마 references/schemas.md §1)

서브커맨드:
  build     targets + brief → groups.json
  subgroup  groups.json 의 한 그룹을 절반 예산으로 이분할 (실패 그룹 재시도용)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import select_targets  # noqa: E402  (SOURCE_EXTS 공유 — 저하 감지용)

# --- import 파싱 -----------------------------------------------------------

PY_IMPORT_RE = re.compile(r"^\s*import\s+([\w\.]+)", re.M)
PY_FROM_RE = re.compile(r"^\s*from\s+([\.\w]+)\s+import\s+(.+)$", re.M)
JS_FROM_RE = re.compile(r"""(?:import|export)\s[^;'"]*?from\s*['"]([^'"]+)['"]""")
JS_REQUIRE_RE = re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)""")
JS_BARE_IMPORT_RE = re.compile(r"""^\s*import\s*['"]([^'"]+)['"]""", re.M)
C_INCLUDE_RE = re.compile(r'^\s*#\s*include\s*"([^"]+)"', re.M)
RS_USE_RE = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?use\s+"
    r"([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)(?:\s*::\s*\{([^}]*)\})?",
    re.M)
RS_MOD_RE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+(\w+)\s*;", re.M)
GO_MODULE_RE = re.compile(r"^\s*module\s+(\S+)", re.M)
GO_IMPORT_SINGLE_RE = re.compile(r'^\s*import\s+(?:[\w.]+\s+)?"([^"]+)"', re.M)
GO_IMPORT_BLOCK_RE = re.compile(r"^\s*import\s*\(([^)]*)\)", re.M | re.S)
GO_QUOTED_RE = re.compile(r'"([^"]+)"')
JVM_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]*\w)", re.M)
JVM_IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]*\w)(\.\*)?", re.M)

JS_EXTS = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"]
C_EXTS = [".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh"]
JVM_EXTS = [".java", ".kt", ".kts"]

# import 파서가 있는 확장자 — 이 밖의 소스 파일은 간선 없이 라인 밸런싱으로 폴백되며,
# cmd_build 가 cohesion/unparsed_source_exts 로 그 사실을 드러낸다(조용한 저하 방지).
PARSED_EXTS = ({".py"} | set(JS_EXTS) | set(C_EXTS) | set(JVM_EXTS)
               | {".rs", ".go"})


def read_text(abspath: str) -> str:
    try:
        with open(abspath, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except OSError:
        return ""


def py_module_index(files):
    """dotted 모듈명 → rel 경로."""
    idx = {}
    for f in files:
        if not f.endswith(".py"):
            continue
        key = f[:-3].replace("/", ".")
        if key.endswith(".__init__"):
            key = key[: -len(".__init__")]
        idx[key] = f
    return idx


def resolve_py(importer, text, mod_index):
    targets = set()
    pkg = os.path.dirname(importer).replace("/", ".")  # importer 의 패키지 경로

    for m in PY_IMPORT_RE.finditer(text):
        mod = m.group(1)
        for cand in (mod, mod.rsplit(".", 1)[0] if "." in mod else None):
            if cand and cand in mod_index:
                targets.add(mod_index[cand])

    for m in PY_FROM_RE.finditer(text):
        mod = m.group(1)
        names = [n.strip().split(" as ")[0].strip()
                 for n in m.group(2).replace("(", " ").replace(")", " ").split(",")]
        if mod.startswith("."):
            dots = len(mod) - len(mod.lstrip("."))
            rest = mod.lstrip(".")
            base_parts = pkg.split(".") if pkg else []
            up = base_parts[: len(base_parts) - (dots - 1)] if dots >= 1 else base_parts
            base = ".".join([p for p in up if p])
            mod_abs = ".".join([p for p in [base, rest] if p])
        else:
            mod_abs = mod
        cands = [mod_abs]
        for nm in names:
            if nm and nm != "*":
                cands.append(f"{mod_abs}.{nm}")
        for cand in cands:
            if cand in mod_index:
                targets.add(mod_index[cand])
    targets.discard(importer)
    return targets


def resolve_js(importer, text, file_set):
    targets = set()
    specs = (JS_FROM_RE.findall(text) + JS_REQUIRE_RE.findall(text)
             + JS_BARE_IMPORT_RE.findall(text))
    base_dir = os.path.dirname(importer)
    for spec in specs:
        if not (spec.startswith(".") or spec.startswith("/")):
            continue  # 외부 패키지
        raw = os.path.normpath(os.path.join(base_dir, spec)).replace("\\", "/")
        cands = []
        _, ext = os.path.splitext(raw)
        if ext in JS_EXTS:
            cands.append(raw)
        else:
            for e in JS_EXTS:
                cands.append(raw + e)
            for e in JS_EXTS:
                cands.append(f"{raw}/index{e}")
        for c in cands:
            if c in file_set and c != importer:
                targets.add(c)
    return targets


def resolve_c(importer, text, file_set):
    targets = set()
    base_dir = os.path.dirname(importer)
    for spec in C_INCLUDE_RE.findall(text):
        for base in (base_dir, ""):
            cand = os.path.normpath(os.path.join(base, spec)).replace("\\", "/")
            if cand in file_set and cand != importer:
                targets.add(cand)
    return targets


# --- Rust ------------------------------------------------------------------

def cargo_package_name(text):
    """Cargo.toml 의 [package] name. 다른 섹션([[bin]] 등)의 name 은 무시."""
    in_pkg = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            in_pkg = (s == "[package]")
            continue
        if in_pkg:
            m = re.match(r'name\s*=\s*"([^"]+)"', s)
            if m:
                return m.group(1)
    return None


def rust_crate_index(paths, target_root):
    """크레이트 탐색: {crate_dir: 대표파일(lib.rs 우선)}, {정규화 크레이트명: crate_dir}.

    크레이트 루트는 src/lib.rs|src/main.rs 존재로 식별. 크레이트명은
    <crate_dir>/Cargo.toml 의 [package] name (없으면 디렉터리명), 하이픈은 Rust
    코드에서 언더스코어로 참조되므로 정규화한다.
    """
    if not any(p.endswith(".rs") for p in paths):
        return {}, {}
    crates = {}
    for p in paths:
        for entry in ("src/lib.rs", "src/main.rs"):
            if p == entry or p.endswith("/" + entry):
                crate_dir = p[: -len(entry)].rstrip("/")
                cur = crates.get(crate_dir)
                if cur is None or p.endswith("lib.rs"):
                    crates[crate_dir] = p
    name_map = {}
    for crate_dir in crates:
        cargo = os.path.join(target_root, crate_dir, "Cargo.toml")
        name = cargo_package_name(read_text(cargo)) if os.path.exists(cargo) else None
        if not name:
            name = os.path.basename(crate_dir) or None
        if name:
            name_map[name.replace("-", "_")] = crate_dir
    return crates, name_map


def rust_src_dir(crate_dir):
    return f"{crate_dir}/src" if crate_dir else "src"


def rust_importer_dirs(importer):
    """(self_dir: 자식 모듈 디렉터리, parent_dir: 형제 모듈 디렉터리) — 2018 에디션 규칙."""
    d = os.path.dirname(importer)
    base = os.path.basename(importer)
    if base == "mod.rs":
        return d, os.path.dirname(d)
    if base in ("lib.rs", "main.rs"):
        return d, d  # 크레이트 루트 — super 없음, src 로 고정
    stem = base[: -len(".rs")]
    return (f"{d}/{stem}" if d else stem), d


def resolve_rust(importer, text, file_set, crates, name_map):
    """use/mod 선언 → 파일 간선. 완전 해석이 아니라 응집 형성 목적의 근사.

    미해석 use 는 조용히 버린다(간선 일부 유실 허용). 크로스 크레이트는 파일 해석
    실패 시 크레이트 대표 파일(lib.rs/main.rs)로 폴백해 크레이트 간 결합을 기록한다.
    """
    targets = set()
    my_crate = None
    for cdir in crates:
        if cdir == "" or importer.startswith(cdir + "/"):
            if my_crate is None or len(cdir) > len(my_crate):
                my_crate = cdir
    self_dir, parent_dir = rust_importer_dirs(importer)

    def try_candidates(base, segs):
        """긴 후보부터 base/s1/../sk.rs | .../mod.rs 매칭 (마지막 세그먼트가 item 가능)."""
        for k in range(len(segs), 0, -1):
            stem = "/".join(([base] if base else []) + list(segs[:k]))
            for cand in (stem + ".rs", stem + "/mod.rs"):
                if cand in file_set and cand != importer:
                    targets.add(cand)
                    return True
        return False

    for m in RS_USE_RE.finditer(text):
        prefix = [s for s in m.group(1).split("::") if s]
        leafs = []
        if m.group(2):
            for item in m.group(2).split(","):
                first = item.strip().split(" as ")[0].strip().split("::")[0].strip()
                if first and first != "self" and re.fullmatch(r"[A-Za-z_]\w*", first):
                    leafs.append(first)
        seg_lists = [prefix + [lf] for lf in leafs] if leafs else [prefix]
        for segs in seg_lists:
            if not segs:
                continue
            head, rest = segs[0], segs[1:]
            if head == "crate":
                if my_crate is not None:
                    try_candidates(rust_src_dir(my_crate), rest)
            elif head == "self":
                try_candidates(self_dir, rest)
            elif head == "super":
                base = parent_dir
                while rest and rest[0] == "super":
                    base = os.path.dirname(base) or base
                    rest = rest[1:]
                try_candidates(base, rest)
            elif head in name_map:
                cdir = name_map[head]
                if not try_candidates(rust_src_dir(cdir), rest) and cdir != my_crate:
                    entry = crates.get(cdir)
                    if entry and entry in file_set and entry != importer:
                        targets.add(entry)
            # else: std/외부 크레이트 — 무시

    for m in RS_MOD_RE.finditer(text):
        name = m.group(1)
        for cand in (f"{self_dir}/{name}.rs", f"{self_dir}/{name}/mod.rs"):
            if cand in file_set and cand != importer:
                targets.add(cand)
                break
    targets.discard(importer)
    return targets


# --- Go ----------------------------------------------------------------------

def go_module_index(paths, target_root):
    """모듈경로 → 모듈루트 rel dir. .go 파일의 조상 디렉터리에서 go.mod 를 찾는다."""
    if not any(p.endswith(".go") for p in paths):
        return {}
    idx, seen = {}, set()
    for p in paths:
        if not p.endswith(".go"):
            continue
        d = os.path.dirname(p)
        while True:
            if d not in seen:
                seen.add(d)
                gm = os.path.join(target_root, d, "go.mod") if d else \
                    os.path.join(target_root, "go.mod")
                m = GO_MODULE_RE.search(read_text(gm)) if os.path.exists(gm) else None
                if m:
                    idx[m.group(1)] = d
            if not d:
                break
            d = os.path.dirname(d)
    return idx


def resolve_go(importer, text, go_modules, dir_files):
    """import 경로 → 최장 모듈 프리픽스 매칭 → 패키지(디렉터리) 내 전 .go 파일 간선.

    Go 는 패키지(디렉터리) 단위 import 라 파일 단위 해석이 없다. 표준 라이브러리·외부
    모듈은 어떤 모듈 프리픽스에도 안 걸려 자연히 무시된다.
    """
    targets = set()
    specs = list(GO_IMPORT_SINGLE_RE.findall(text))
    for block in GO_IMPORT_BLOCK_RE.findall(text):
        specs.extend(GO_QUOTED_RE.findall(block))
    for spec in specs:
        best = None
        for mod, root in go_modules.items():
            if spec == mod or spec.startswith(mod + "/"):
                if best is None or len(mod) > len(best[0]):
                    best = (mod, root)
        if not best:
            continue
        sub = spec[len(best[0]):].lstrip("/")
        root = best[1]
        pkg_dir = "/".join([p for p in (root, sub) if p]).rstrip("/")
        for f in dir_files.get(pkg_dir, ()):
            if f != importer and f.endswith(".go"):
                targets.add(f)
    return targets


# --- Java/Kotlin ---------------------------------------------------------------

def jvm_package_index(paths, target_root):
    """package 선언 기준 통합 인덱스: {pkg: [files]}, {pkg.stem: file}, {file: text}.

    Java·Kotlin 혼용 프로젝트에서 서로 import 하므로 한 인덱스로 묶는다. 디렉터리가
    아니라 선언 기준이라 Kotlin 의 패키지≠디렉터리 케이스도 처리된다.
    """
    pkg_files, fqn_map, texts = {}, {}, {}
    for p in paths:
        if os.path.splitext(p.lower())[1] not in JVM_EXTS:
            continue
        text = read_text(os.path.join(target_root, p))
        texts[p] = text
        m = JVM_PACKAGE_RE.search(text)
        pkg = m.group(1) if m else ""
        pkg_files.setdefault(pkg, []).append(p)
        stem = os.path.splitext(os.path.basename(p))[0]
        fqn_map[f"{pkg}.{stem}" if pkg else stem] = p
    return pkg_files, fqn_map, texts


def resolve_jvm(importer, text, pkg_files, fqn_map):
    """import FQN → pkg.stem 정확 매칭, 실패 시 세그먼트 축약(static/중첩 클래스).
    와일드카드는 패키지 전체로 간선. 미해석(외부 라이브러리)은 무시."""
    targets = set()
    for m in JVM_IMPORT_RE.finditer(text):
        name, wild = m.group(1), m.group(2)
        if wild:
            for f in pkg_files.get(name, ()):
                if f != importer:
                    targets.add(f)
            continue
        cand = name
        while cand:
            hit = fqn_map.get(cand)
            if hit:
                if hit != importer:
                    targets.add(hit)
                break
            if "." not in cand:
                break
            cand = cand.rsplit(".", 1)[0]
    return targets


def build_edges(files, target_root):
    """core+low 파일 간 import 무향 간선 집합. 파서 없는 언어는 간선 없음(디렉터리 폴백)."""
    paths = [f["path"] for f in files]
    file_set = set(paths)
    mod_index = py_module_index(paths)
    rs_crates, rs_names = rust_crate_index(paths, target_root)
    go_modules = go_module_index(paths, target_root)
    jvm_pkgs, jvm_fqns, jvm_texts = jvm_package_index(paths, target_root)
    dir_files = {}
    for p in paths:
        dir_files.setdefault(os.path.dirname(p), []).append(p)
    edges = set()
    for path in paths:
        abspath = os.path.join(target_root, path)
        _, ext = os.path.splitext(path.lower())
        if ext == ".py":
            text = read_text(abspath)
            tgts = resolve_py(path, text, mod_index)
        elif ext in JS_EXTS:
            text = read_text(abspath)
            tgts = resolve_js(path, text, file_set)
        elif ext in C_EXTS:
            text = read_text(abspath)
            tgts = resolve_c(path, text, file_set)
        elif ext == ".rs":
            text = read_text(abspath)
            tgts = resolve_rust(path, text, file_set, rs_crates, rs_names)
        elif ext == ".go":
            text = read_text(abspath)
            tgts = resolve_go(path, text, go_modules, dir_files)
        elif ext in JVM_EXTS:
            tgts = resolve_jvm(path, jvm_texts.get(path, ""), jvm_pkgs, jvm_fqns)
        else:
            tgts = set()
        for t in tgts:
            a, b = sorted((path, t))
            edges.add((a, b))
    return edges


# --- 그래프 유틸 -----------------------------------------------------------

class UnionFind:
    def __init__(self, items):
        self.parent = {x: x for x in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def connected_components(paths, edges):
    uf = UnionFind(paths)
    pset = set(paths)
    for a, b in edges:
        if a in pset and b in pset:
            uf.union(a, b)
    comps = {}
    for p in paths:
        comps.setdefault(uf.find(p), []).append(p)
    # 결정성: 각 컴포넌트를 대표 경로로 정렬.
    out = [sorted(v) for v in comps.values()]
    out.sort(key=lambda c: c[0])
    return out


def top_dir(path):
    return path.split("/", 1)[0]


def split_component(comp, lines_of, budget):
    """예산 초과 연결 요소를 디렉터리 버킷 → 라인 균형 bin-pack 으로 절단 최소 분할."""
    # 디렉터리(부모 경로) 단위 버킷 — 형제 파일은 함께 유지.
    buckets = {}
    for p in comp:
        buckets.setdefault(os.path.dirname(p), []).append(p)

    units = []  # (paths, lines)
    for _, files in sorted(buckets.items()):
        blines = sum(lines_of[f] for f in files)
        if blines <= budget:
            units.append((sorted(files), blines))
        else:
            # 단일 디렉터리가 예산 초과 — 최후의 수단으로 라인 균형 분할.
            cur, cur_lines = [], 0
            for f in sorted(files, key=lambda x: (-lines_of[x], x)):
                if cur and cur_lines + lines_of[f] > budget:
                    units.append((sorted(cur), cur_lines))
                    cur, cur_lines = [], 0
                cur.append(f)
                cur_lines += lines_of[f]
            if cur:
                units.append((sorted(cur), cur_lines))

    # 버킷 units 를 예산 이하 조각으로 greedy 결합(같은 top-dir 우선).
    units.sort(key=lambda u: (top_dir(u[0][0]), u[0][0]))
    pieces = []
    cur, cur_lines = [], 0
    for paths, plines in units:
        if cur and cur_lines + plines > budget:
            pieces.append(sorted(cur))
            cur, cur_lines = [], 0
        cur.extend(paths)
        cur_lines += plines
    if cur:
        pieces.append(sorted(cur))
    return [sorted(pc) for pc in pieces]


def make_units(paths, edges, lines_of, budget):
    """paths 를 예산 이하 units 리스트로. 컴포넌트 단위, 초과 컴포넌트는 분할."""
    comps = connected_components(paths, edges)
    units = []
    for comp in comps:
        clines = sum(lines_of[f] for f in comp)
        if clines <= budget:
            units.append(sorted(comp))
        else:
            units.extend(split_component(comp, lines_of, budget))
    return units


def pack_units(units, lines_of, budget):
    """units(각 ≤budget)를 그룹으로 greedy bin-pack. 같은 top-dir 우선 인접."""
    ordered = sorted(units, key=lambda u: (top_dir(u[0]), u[0]))
    groups = []
    cur, cur_lines = [], 0
    for unit in ordered:
        ulines = sum(lines_of[f] for f in unit)
        if cur and cur_lines + ulines > budget:
            groups.append(cur)
            cur, cur_lines = [], 0
        cur.extend(unit)
        cur_lines += ulines
    if cur:
        groups.append(cur)
    return [sorted(g) for g in groups]


def high_risk_match(files, areas):
    for area in areas:
        a = area["path"].rstrip("/")
        for f in files:
            if f == a or f.startswith(a + "/"):
                return True
    return False


def compute_seams(group_of, edges):
    """final 그룹 배정에서 절단된 간선 → 그룹별 seam_hints."""
    seams = {}
    for a, b in sorted(edges):
        ga, gb = group_of.get(a), group_of.get(b)
        if ga is None or gb is None or ga == gb:
            continue
        seams.setdefault(ga, []).append({"file": a, "peer": b, "peer_group": gb})
        seams.setdefault(gb, []).append({"file": b, "peer": a, "peer_group": ga})
    return seams


def assemble(files, edges, budget, brief, target_root, run_id, gid_start=1,
             label=str):
    """core 그룹핑 후 low 후순위 배정 → 그룹 리스트 반환."""
    lines_of = {f["path"]: f["lines"] for f in files}
    class_of = {f["path"]: f["class"] for f in files}
    core = [f["path"] for f in files if class_of[f["path"]] == "core"]
    low = [f["path"] for f in files if class_of[f["path"]] == "low"]

    core_edges = {(a, b) for a, b in edges if a in set(core) and b in set(core)}
    core_groups = pack_units(make_units(core, core_edges, lines_of, budget),
                             lines_of, budget)

    # low 후순위: 잔여 예산 우선 배정, 넘치면 새 그룹.
    group_files = [list(g) for g in core_groups]
    group_lines = [sum(lines_of[f] for f in g) for g in group_files]
    low_edges = {(a, b) for a, b in edges if a in set(low) and b in set(low)}
    low_units = make_units(low, low_edges, lines_of, budget)
    leftover = []
    for unit in sorted(low_units, key=lambda u: (top_dir(u[0]), u[0])):
        ulines = sum(lines_of[f] for f in unit)
        placed = False
        # 같은 top-dir 그룹 우선, 그다음 잔여 있는 아무 그룹.
        order = sorted(range(len(group_files)),
                       key=lambda i: (0 if group_files[i] and
                                      top_dir(group_files[i][0]) == top_dir(unit[0])
                                      else 1, group_lines[i]))
        for i in order:
            if group_lines[i] + ulines <= budget:
                group_files[i].extend(unit)
                group_lines[i] += ulines
                placed = True
                break
        if not placed:
            leftover.append(unit)
    for g in pack_units(leftover, lines_of, budget):
        group_files.append(g)
        group_lines.append(sum(lines_of[f] for f in g))

    # 그룹 ID 부여 + seam 계산.
    labels = [label(gid_start + i) for i in range(len(group_files))]
    group_of = {}
    for lab, gfiles in zip(labels, group_files):
        for f in gfiles:
            group_of[f] = lab
    seams = compute_seams(group_of, edges)

    groups = []
    for lab, gfiles in zip(labels, group_files):
        gfiles = sorted(gfiles)
        groups.append({
            "group_id": lab,
            "files": [{"path": f, "lines": lines_of[f], "class": class_of[f]}
                      for f in gfiles],
            "total_lines": sum(lines_of[f] for f in gfiles),
            "high_risk": high_risk_match(gfiles, brief.get("high_risk_areas", [])),
            "seam_hints": sorted(seams.get(lab, []),
                                 key=lambda s: (s["file"], s["peer"])),
        })
    return groups


# --- 서브커맨드 ------------------------------------------------------------

def cmd_build(args):
    with open(args.targets, encoding="utf-8") as fh:
        targets = json.load(fh)
    brief = {}
    if args.brief and os.path.exists(args.brief):
        with open(args.brief, encoding="utf-8") as fh:
            brief = json.load(fh)

    target_root = targets["target_root"]
    files = targets["files"]
    edges = build_edges(files, target_root)
    groups = assemble(files, edges, args.budget, brief, target_root, args.run_id)

    # 응집 저하 감지 — 파서 없는 언어에서 간선 0 으로 조용히 폴백되는 것을 드러낸다.
    src_exts = [os.path.splitext(f["path"].lower())[1] for f in files]
    src_exts = [e for e in src_exts if e in select_targets.SOURCE_EXTS]
    unparsed = {}
    for e in src_exts:
        if e not in PARSED_EXTS:
            unparsed[e] = unparsed.get(e, 0) + 1
    unparsed = dict(sorted(unparsed.items()))
    cohesion = "import-graph" if edges else "line-balance-only"
    if not edges and len(src_exts) >= 2:
        sys.stderr.write(
            "[group_by_lines] WARNING: import 간선 0 — 응집 그룹핑이 라인 밸런싱으로 "
            f"폴백됨 (미지원 소스 확장자: {json.dumps(unparsed, ensure_ascii=False)})\n")

    out = {
        "run_id": args.run_id,
        "target_root": target_root,
        "line_budget": args.budget,
        "brief": brief,
        "cohesion": cohesion,
        "unparsed_source_exts": unparsed,
        "excluded": targets.get("excluded", []),
        "excluded_files": targets.get("excluded_files", []),
        "groups": groups,
    }
    _write(args.out, out)
    if not args.out:
        return 0
    sys.stderr.write(
        f"[group_by_lines] {len(groups)} groups, budget={args.budget}, "
        f"cohesion={cohesion}\n")
    return 0


def cmd_subgroup(args):
    """실패 그룹을 절반 예산으로 이분할해 groups.json 에 <gid>a/<gid>b 추가."""
    with open(args.groups_file, encoding="utf-8") as fh:
        gj = json.load(fh)
    target = None
    for g in gj["groups"]:
        if str(g["group_id"]) == str(args.group):
            target = g
            break
    if target is None:
        sys.stderr.write(f"[subgroup] group {args.group} not found\n")
        return 1

    files = target["files"]
    target_root = gj["target_root"]
    edges = build_edges(files, target_root)
    letters = "abcdefghijklmnopqrstuvwxyz"
    base = str(args.group)
    subs = assemble(files, edges, args.budget, gj.get("brief", {}),
                    target_root, gj.get("run_id", ""),
                    gid_start=0, label=lambda i: f"{base}{letters[i]}")
    # 기존 그룹 뒤에 추가(원본은 state.json 에서 failed 로 관리).
    existing = {str(g["group_id"]) for g in gj["groups"]}
    added = []
    for s in subs:
        if str(s["group_id"]) in existing:
            continue
        gj["groups"].append(s)
        added.append(s["group_id"])
    _write(args.groups_file, gj)
    sys.stderr.write(f"[subgroup] added {added}\n")
    print(json.dumps({"added": added}, ensure_ascii=False))
    return 0


def _write(path, obj):
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    if path:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="라인수+import 응집 그룹핑")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="targets+brief → groups.json")
    b.add_argument("--targets", required=True)
    b.add_argument("--brief", default=None)
    b.add_argument("--budget", type=int, default=10000)
    b.add_argument("--run-id", required=True)
    b.add_argument("--out", default=None)
    b.set_defaults(func=cmd_build)

    s = sub.add_parser("subgroup", help="failed 그룹 절반 예산 이분할")
    s.add_argument("--groups-file", required=True)
    s.add_argument("--group", required=True)
    s.add_argument("--budget", type=int, required=True)
    s.set_defaults(func=cmd_subgroup)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
