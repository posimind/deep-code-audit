#!/usr/bin/env python3
"""deep-code-audit 스크립트 4종 단위 테스트 (표준 라이브러리만).

실행: python3 scripts/test_scripts.py
스키마 예시(references/schemas.md)를 픽스처로 삼아 결정적 동작을 검증한다.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build_report  # noqa: E402
import group_by_lines  # noqa: E402
import select_targets  # noqa: E402
import validate_output as vo  # noqa: E402


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def wj(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def rj(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def run_capture(fn, *a):
    """cmd_* 를 stdout 캡처와 함께 호출, 출력의 JSON 마지막 줄을 파싱해 반환."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*a)
    return json.loads(buf.getvalue().strip().splitlines()[-1])


# ---------------------------------------------------------------------------

class TestSelectTargets(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_classification(self):
        write(os.path.join(self.root, "src/app.py"), "import os\nx = 1\n")
        write(os.path.join(self.root, "config/app.yaml"), "a: 1\nb: 2\n")
        write(os.path.join(self.root, "tests/test_app.py"), "def test(): pass\n")
        write(os.path.join(self.root, "node_modules/dep/index.js"), "x\n")
        write(os.path.join(self.root, "package-lock.json"), "{}\n")
        write(os.path.join(self.root, "gen/api_pb2.py"), "x\n")
        write(os.path.join(self.root, ".deep-code-audit/20260101-000000/x.json"), "{}\n")
        # 바이너리(널 바이트)
        with open(os.path.join(self.root, "img.png"), "wb") as fh:
            fh.write(b"\x89PNG\x00\x00data")

        res = select_targets.walk(self.root, [], [], 5000)
        classes = {f["path"]: f["class"] for f in res["files"]}
        self.assertEqual(classes.get("src/app.py"), "core")
        self.assertEqual(classes.get("config/app.yaml"), "core")
        self.assertEqual(classes.get("tests/test_app.py"), "low")
        # 배제 대상은 files 에 없어야 한다.
        self.assertNotIn("node_modules/dep/index.js", classes)
        self.assertNotIn("package-lock.json", classes)
        self.assertNotIn("gen/api_pb2.py", classes)
        self.assertNotIn("img.png", classes)
        # 자기 산출물 배제 필수.
        self.assertFalse(any(".deep-code-audit" in p for p in classes))

    def test_size_guard_and_overrides(self):
        write(os.path.join(self.root, "data/big.json"), "\n".join(["{}"] * 20) + "\n")
        write(os.path.join(self.root, "src/small.py"), "x = 1\n")
        res = select_targets.walk(self.root, [], [], 10)  # 임계 10라인
        paths = {f["path"] for f in res["files"]}
        self.assertNotIn("data/big.json", paths)  # 크기 가드로 배제
        self.assertTrue(any("data/big.json" in e["path"] for e in res["excluded_files"]))
        # include 오버라이드로 강제 core 복원.
        res2 = select_targets.walk(self.root, [], ["data/big.json"], 10)
        paths2 = {f["path"] for f in res2["files"]}
        self.assertIn("data/big.json", paths2)


# ---------------------------------------------------------------------------

class TestGroupByLines(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _files(self, spec):
        """spec: {path: (lines, class, content)} → files 리스트 + 파일 기록."""
        files = []
        for path, (lines, cls, content) in spec.items():
            write(os.path.join(self.root, path), content)
            files.append({"path": path, "lines": lines, "class": cls})
        return files

    def test_import_cohesion(self):
        spec = {
            "pkg/a.py": (100, "core", "from pkg import b\n"),
            "pkg/b.py": (100, "core", "x = 1\n"),
            "other/c.py": (100, "core", "y = 2\n"),
        }
        files = self._files(spec)
        edges = group_by_lines.build_edges(files, self.root)
        self.assertIn(("pkg/a.py", "pkg/b.py"), edges)
        groups = group_by_lines.assemble(files, edges, 10000, {}, self.root, "rid")
        # 예산이 넉넉하면 전부 한 그룹.
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["files"]), 3)

    def test_over_budget_split_and_seams(self):
        # a↔b 강결합(같은 dir), 예산이 작아 분할되어야 함.
        spec = {
            "core/a.py": (600, "core", "from core import b\n"),
            "core/b.py": (600, "core", "from core import a\n"),
            "util/c.py": (600, "core", "z = 3\n"),
        }
        files = self._files(spec)
        edges = group_by_lines.build_edges(files, self.root)
        groups = group_by_lines.assemble(files, edges, 800, {}, self.root, "rid")
        self.assertGreaterEqual(len(groups), 2)
        total = sum(len(g["files"]) for g in groups)
        self.assertEqual(total, 3)
        for g in groups:
            self.assertLessEqual(g["total_lines"], 800)

    def test_high_risk_flag(self):
        spec = {"api/x.py": (100, "core", "x=1\n"), "lib/y.py": (100, "core", "y=1\n")}
        files = self._files(spec)
        edges = group_by_lines.build_edges(files, self.root)
        brief = {"high_risk_areas": [{"path": "api/", "reason": "진입점"}]}
        groups = group_by_lines.assemble(files, edges, 10000, brief, self.root, "rid")
        g = groups[0]
        self.assertTrue(g["high_risk"])  # api/ 포함 그룹은 고위험

    def test_low_after_core(self):
        spec = {
            "src/a.py": (100, "core", "x=1\n"),
            "tests/t.py": (100, "low", "y=1\n"),
        }
        files = self._files(spec)
        edges = group_by_lines.build_edges(files, self.root)
        groups = group_by_lines.assemble(files, edges, 10000, {}, self.root, "rid")
        classes = {f["path"]: f["class"] for g in groups for f in g["files"]}
        self.assertEqual(classes["tests/t.py"], "low")


# ---------------------------------------------------------------------------

class TestRustGrouping(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _files(self, spec):
        files = []
        for path, (lines, cls, content) in spec.items():
            write(os.path.join(self.root, path), content)
            files.append({"path": path, "lines": lines, "class": cls})
        return files

    def test_rust_import_cohesion(self):
        write(os.path.join(self.root, "Cargo.toml"),
              '[package]\nname = "app"\n')
        spec = {
            "src/main.rs": (100, "core",
                            "mod util;\nuse crate::util::helper;\nfn main() {}\n"),
            "src/util.rs": (100, "core", "pub fn helper() {}\n"),
            "other/readme.py": (100, "core", "x = 1\n"),
        }
        files = self._files(spec)
        edges = group_by_lines.build_edges(files, self.root)
        self.assertIn(("src/main.rs", "src/util.rs"), edges)
        groups = group_by_lines.assemble(files, edges, 10000, {}, self.root, "rid")
        gid_of = {f["path"]: g["group_id"] for g in groups for f in g["files"]}
        self.assertEqual(gid_of["src/main.rs"], gid_of["src/util.rs"])

    def test_rust_cross_crate(self):
        # 하이픈 크레이트명 정규화 + 파일 해석 실패 시 lib.rs 폴백까지 확인.
        write(os.path.join(self.root, "lib-foo/Cargo.toml"),
              '[package]\nname = "lib-foo"\n')
        write(os.path.join(self.root, "app/Cargo.toml"),
              '[package]\nname = "app"\n')
        spec = {
            "lib-foo/src/lib.rs": (100, "core", "pub mod parse;\n"),
            "lib-foo/src/parse.rs": (100, "core", "pub fn parse() {}\n"),
            "app/src/main.rs": (100, "core",
                                "use lib_foo::parse::parse;\n"
                                "use lib_foo::unknown_item;\nfn main() {}\n"),
        }
        files = self._files(spec)
        edges = group_by_lines.build_edges(files, self.root)
        # 파일 단위 해석: app → lib-foo/src/parse.rs
        self.assertIn(("app/src/main.rs", "lib-foo/src/parse.rs"), edges)
        # 미해석 item → 크레이트 대표 파일(lib.rs) 폴백
        self.assertIn(("app/src/main.rs", "lib-foo/src/lib.rs"), edges)
        # mod 선언: lib.rs → parse.rs
        self.assertIn(("lib-foo/src/lib.rs", "lib-foo/src/parse.rs"), edges)

    def test_rust_super_and_mod_rs(self):
        write(os.path.join(self.root, "Cargo.toml"), '[package]\nname = "app"\n')
        spec = {
            "src/main.rs": (50, "core", "mod net;\nmod io;\nfn main() {}\n"),
            "src/net/mod.rs": (50, "core",
                               "pub mod tcp;\nuse super::io::write_all;\n"),
            "src/net/tcp.rs": (50, "core",
                               "use self::super::super::io::write_all;\n"
                               "use crate::net::mod_helper;\n"),
            "src/io.rs": (50, "core", "pub fn write_all() {}\n"),
        }
        files = self._files(spec)
        edges = group_by_lines.build_edges(files, self.root)
        self.assertIn(("src/main.rs", "src/net/mod.rs"), edges)  # mod net;
        self.assertIn(("src/io.rs", "src/main.rs"), edges)  # mod io;
        self.assertIn(("src/net/mod.rs", "src/net/tcp.rs"), edges)  # pub mod tcp;
        self.assertIn(("src/io.rs", "src/net/mod.rs"), edges)  # super::io
        # use 브레이스 그룹
        write(os.path.join(self.root, "src/net/tcp.rs"),
              "use crate::{io, net};\n")
        edges2 = group_by_lines.build_edges(files, self.root)
        self.assertIn(("src/io.rs", "src/net/tcp.rs"), edges2)

    def test_rust_seams(self):
        write(os.path.join(self.root, "Cargo.toml"), '[package]\nname = "app"\n')
        spec = {
            "src/main.rs": (600, "core", "mod a;\nmod b;\nfn main() {}\n"),
            "src/a.rs": (600, "core", "use crate::b::helper;\n"),
            "src/b.rs": (600, "core", "pub fn helper() {}\n"),
        }
        files = self._files(spec)
        edges = group_by_lines.build_edges(files, self.root)
        self.assertGreaterEqual(len(edges), 3)
        groups = group_by_lines.assemble(files, edges, 800, {}, self.root, "rid")
        self.assertGreaterEqual(len(groups), 2)
        # 절단 간선이 존재하고 양쪽 그룹에 seam_hints 로 기록된다.
        with_seams = [g for g in groups if g["seam_hints"]]
        self.assertGreaterEqual(len(with_seams), 2)


class TestGoJvmGrouping(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _files(self, spec):
        files = []
        for path, (lines, cls, content) in spec.items():
            write(os.path.join(self.root, path), content)
            files.append({"path": path, "lines": lines, "class": cls})
        return files

    def test_go_import_cohesion(self):
        write(os.path.join(self.root, "go.mod"), "module example.com/m\n\ngo 1.22\n")
        spec = {
            "main.go": (100, "core",
                        'package main\n\nimport (\n\t"fmt"\n'
                        '\tu "example.com/m/util"\n)\n\nfunc main() {}\n'),
            "util/helper.go": (100, "core", "package util\n\nfunc Helper() {}\n"),
            "util/extra.go": (100, "core", "package util\n\nfunc Extra() {}\n"),
            "single.go": (50, "core",
                          'package main\n\nimport _ "example.com/m/util"\n'),
        }
        files = self._files(spec)
        edges = group_by_lines.build_edges(files, self.root)
        # 블록 import(alias 포함) → 패키지 디렉터리 내 전 파일
        self.assertIn(("main.go", "util/helper.go"), edges)
        self.assertIn(("main.go", "util/extra.go"), edges)
        # 단건 import(blank alias)
        self.assertIn(("single.go", "util/helper.go"), edges)
        # 표준 라이브러리("fmt")는 간선 없음
        self.assertFalse(any("fmt" in a or "fmt" in b for a, b in edges))

    def test_go_multi_module(self):
        write(os.path.join(self.root, "go.mod"), "module example.com/m\n")
        write(os.path.join(self.root, "sub/go.mod"), "module example.com/m/sub\n")
        spec = {
            "app.go": (100, "core",
                       'package main\nimport "example.com/m/sub/pkg"\n'),
            "sub/pkg/p.go": (100, "core", "package pkg\n"),
        }
        files = self._files(spec)
        edges = group_by_lines.build_edges(files, self.root)
        # 최장 모듈경로(example.com/m/sub) 매칭으로 sub/pkg 에 해석돼야 한다.
        self.assertIn(("app.go", "sub/pkg/p.go"), edges)

    def test_jvm_import_cohesion(self):
        spec = {
            "app/src/com/ex/app/Main.java": (
                100, "core",
                "package com.ex.app;\n\nimport com.ex.util.Text;\n"
                "public class Main {}\n"),
            "app/src/com/ex/util/Text.java": (
                100, "core", "package com.ex.util;\npublic class Text {}\n"),
            # Kotlin → Java import (혼용 통합 인덱스) + 패키지≠디렉터리 케이스
            "kt/Anywhere.kt": (
                100, "core",
                "package com.ex.feature\n\nimport com.ex.util.Text\n"
                "import com.ex.app.Main.Companion\nval x = 1\n"),
        }
        files = self._files(spec)
        edges = group_by_lines.build_edges(files, self.root)
        self.assertIn(
            ("app/src/com/ex/app/Main.java", "app/src/com/ex/util/Text.java"),
            edges)
        self.assertIn(
            ("app/src/com/ex/util/Text.java", "kt/Anywhere.kt"), edges)
        # 세그먼트 축약: com.ex.app.Main.Companion → com.ex.app.Main
        self.assertIn(
            ("app/src/com/ex/app/Main.java", "kt/Anywhere.kt"), edges)

    def test_jvm_wildcard_and_static(self):
        spec = {
            "src/A.java": (100, "core",
                           "package p.a;\nimport p.b.*;\n"
                           "import static p.c.Util.helper;\npublic class A {}\n"),
            "src/B1.java": (100, "core", "package p.b;\npublic class B1 {}\n"),
            "src/B2.kt": (100, "core", "package p.b\nval y = 2\n"),
            "src/Util.java": (100, "core", "package p.c;\npublic class Util {}\n"),
        }
        files = self._files(spec)
        edges = group_by_lines.build_edges(files, self.root)
        self.assertIn(("src/A.java", "src/B1.java"), edges)  # 와일드카드
        self.assertIn(("src/A.java", "src/B2.kt"), edges)  # 와일드카드 (Kotlin 포함)
        self.assertIn(("src/A.java", "src/Util.java"), edges)  # static 축약

    def test_cohesion_flag(self):
        # 파서 없는 소스 확장자(.swift)만 → line-balance-only + 경고.
        write(os.path.join(self.root, "a.swift"), "import Foundation\n")
        write(os.path.join(self.root, "b.swift"), "let x = 1\n")
        run = os.path.join(self.root, ".run")
        wj(os.path.join(run, "targets.json"), {
            "target_root": self.root,
            "files": [
                {"path": "a.swift", "lines": 1, "class": "core"},
                {"path": "b.swift", "lines": 1, "class": "core"},
            ],
        })
        out_path = os.path.join(run, "groups.json")
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "group_by_lines.py"), "build",
             "--targets", os.path.join(run, "targets.json"),
             "--run-id", "rid", "--out", out_path],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("WARNING", proc.stderr)
        gj = rj(out_path)
        self.assertEqual(gj["cohesion"], "line-balance-only")
        self.assertEqual(gj["unparsed_source_exts"], {".swift": 2})

        # 대조군: 파서 있는 언어(.py) → import-graph, 경고 없음.
        write(os.path.join(self.root, "pkg/a.py"), "from pkg import b\n")
        write(os.path.join(self.root, "pkg/b.py"), "x = 1\n")
        wj(os.path.join(run, "targets2.json"), {
            "target_root": self.root,
            "files": [
                {"path": "pkg/a.py", "lines": 1, "class": "core"},
                {"path": "pkg/b.py", "lines": 1, "class": "core"},
            ],
        })
        out2 = os.path.join(run, "groups2.json")
        proc2 = subprocess.run(
            [sys.executable, os.path.join(HERE, "group_by_lines.py"), "build",
             "--targets", os.path.join(run, "targets2.json"),
             "--run-id", "rid", "--out", out2],
            capture_output=True, text=True)
        self.assertEqual(proc2.returncode, 0)
        self.assertNotIn("WARNING", proc2.stderr)
        gj2 = rj(out2)
        self.assertEqual(gj2["cohesion"], "import-graph")
        self.assertEqual(gj2["unparsed_source_exts"], {})


# ---------------------------------------------------------------------------

def base_defects(gid=3):
    return {
        "group_id": gid,
        "findings": [{
            "id": f"g{gid}-001", "pass": "primary", "category": "security",
            "severity": "critical", "confidence": "medium",
            "location": {"file": "api/search.py", "start": 42, "end": 48,
                         "symbol": "handle_search"},
            "claim": "HTTP 파라미터가 검증 없이 SQL 에 연결",
            "rationale": "q 유입 후 f-string 직결",
            "snippet": "cursor.execute(...)", "evidence_files": ["db/conn.py"],
        }],
        "coverage": [{"path": "api/search.py", "role": "핸들러", "top_risk": "SQLi"}],
        "cross_refs": [{"file": "db/conn.py", "line": 15, "category": "concurrency",
                        "hint": "전역 커넥션 락 없음"}],
        "issues": [],
    }


def groups_fixture(root):
    return {
        "run_id": "20260702-143000", "target_root": root, "line_budget": 10000,
        "brief": {"project_type": "web", "purpose": "결제 API",
                  "lens_priority": ["security"], "high_risk_areas": []},
        "excluded": ["vendor/"], "excluded_files": [],
        "groups": [
            {"group_id": 3, "high_risk": True, "seam_hints": [],
             "total_lines": 300, "files": [
                 {"path": "api/search.py", "lines": 200, "class": "core"},
                 {"path": "api/filters.py", "lines": 100, "class": "core"}]},
            {"group_id": 5, "high_risk": False, "seam_hints": [],
             "total_lines": 100, "files": [
                 {"path": "db/conn.py", "lines": 100, "class": "core"}]},
        ],
    }


class TestValidateDefects(unittest.TestCase):
    def setUp(self):
        self.run = tempfile.mkdtemp()
        wj(os.path.join(self.run, "groups.json"), groups_fixture(self.run))
        vo.cmd_init_state(_ns(run_dir=self.run, groups_file=None))

    def tearDown(self):
        shutil.rmtree(self.run, ignore_errors=True)

    def _validate(self, stage, gid, obj, **kw):
        path = os.path.join(self.run, "defects", f"{gid}.json")
        wj(path, obj)
        ns = _ns(stage=stage, group=str(gid), run_dir=self.run, file=None,
                 groups_file=None, no_coverage=kw.get("no_coverage", False))
        return vo.cmd_validate(ns)

    def test_valid_passes_and_marks_state(self):
        d = base_defects(3)
        # filters.py 도 coverage 에 있어야 미커버 재시도를 피함.
        d["coverage"].append({"path": "api/filters.py", "role": "상수",
                              "top_risk": "특이점 없음"})
        self._validate("hunt", 3, d)
        st = rj(os.path.join(self.run, "state.json"))
        self.assertEqual(st["stages"]["hunt"]["3"], "done")

    def test_coverage_gap_fails(self):
        d = base_defects(3)  # filters.py 누락
        with self.assertRaises(SystemExit) as cm:
            self._validate("hunt", 3, d)
        self.assertEqual(cm.exception.code, 3)

    def test_bad_type_fails(self):
        d = base_defects(3)
        d["coverage"].append({"path": "api/filters.py", "role": "상수",
                              "top_risk": "없음"})
        d["findings"][0]["location"]["end"] = "48"  # 문자열
        with self.assertRaises(SystemExit) as cm:
            self._validate("hunt", 3, d)
        self.assertEqual(cm.exception.code, 2)

    def test_scope_violation_fails(self):
        d = base_defects(3)
        d["coverage"].append({"path": "api/filters.py", "role": "상수", "top_risk": "없음"})
        d["findings"][0]["location"]["file"] = "db/conn.py"  # 그룹 밖
        with self.assertRaises(SystemExit):
            self._validate("hunt", 3, d)

    def test_intra_overlap_same_category_warns_without_removal(self):
        d = base_defects(3)
        d["coverage"].append({"path": "api/filters.py", "role": "상수",
                              "top_risk": "없음"})
        # 기존 발견(42-48, security)과 범위가 겹치는 동일 category 발견.
        d["findings"].append({
            "id": "g3-002", "pass": "primary", "category": "security",
            "severity": "major", "confidence": "medium",
            "location": {"file": "api/search.py", "start": 45, "end": 50,
                         "symbol": "handle_search"},
            "claim": "c", "rationale": "r", "snippet": "s", "evidence_files": []})
        self._validate("hunt", 3, d)  # 경고일 뿐 실패 아님
        out = rj(os.path.join(self.run, "defects", "3.json"))
        self.assertEqual(len(out["findings"]), 2)  # 자동 제거 금지
        with open(os.path.join(self.run, "issues.jsonl"), encoding="utf-8") as fh:
            issues = [json.loads(ln) for ln in fh if ln.strip()]
        warn = [it for it in issues if "자기중복" in it["context"]]
        self.assertEqual(len(warn), 1)
        self.assertIn("g3-001~g3-002", warn[0]["symptom"])

    def test_intra_overlap_different_category_no_warn(self):
        d = base_defects(3)
        d["coverage"].append({"path": "api/filters.py", "role": "상수",
                              "top_risk": "없음"})
        # 같은 범위라도 category 가 다르면 별개 발견 — 경고 없음.
        d["findings"].append({
            "id": "g3-002", "pass": "primary", "category": "resource",
            "severity": "major", "confidence": "medium",
            "location": {"file": "api/search.py", "start": 42, "end": 48,
                         "symbol": "handle_search"},
            "claim": "c", "rationale": "r", "snippet": "s", "evidence_files": []})
        self._validate("hunt", 3, d)
        issues_path = os.path.join(self.run, "issues.jsonl")
        if os.path.exists(issues_path):
            with open(issues_path, encoding="utf-8") as fh:
                issues = [json.loads(ln) for ln in fh if ln.strip()]
            self.assertFalse([it for it in issues if "자기중복" in it["context"]])

    def test_low_downgrade(self):
        # db/conn.py 를 low 로 바꾼 그룹에서 critical → minor 강등.
        gj = groups_fixture(self.run)
        gj["groups"][1]["files"][0]["class"] = "low"
        wj(os.path.join(self.run, "groups.json"), gj)
        d = {"group_id": 5, "findings": [{
            "id": "g5-001", "pass": "primary", "category": "logic",
            "severity": "critical", "confidence": "low",
            "location": {"file": "db/conn.py", "start": 1, "end": 2, "symbol": "f"},
            "claim": "c", "rationale": "r", "snippet": "s", "evidence_files": []}],
            "coverage": [], "cross_refs": [], "issues": []}
        self._validate("hunt", 5, d, no_coverage=True)
        out = rj(os.path.join(self.run, "defects", "5.json"))
        self.assertEqual(out["findings"][0]["severity"], "minor")


class TestValidateVerified(unittest.TestCase):
    def setUp(self):
        self.run = tempfile.mkdtemp()
        wj(os.path.join(self.run, "groups.json"), groups_fixture(self.run))
        vo.cmd_init_state(_ns(run_dir=self.run, groups_file=None))

    def tearDown(self):
        shutil.rmtree(self.run, ignore_errors=True)

    def _v(self, obj):
        path = os.path.join(self.run, "verified", "3.json")
        wj(path, obj)
        return vo.cmd_validate(_ns(stage="verify", group="3", run_dir=self.run,
                                   file=None, groups_file=None, no_coverage=False))

    def test_confirmed_valid(self):
        obj = {"group_id": 3, "results": [{
            "id": "g3-001", "verdict": "confirmed", "rubric": "full", "score": 5,
            "rederivation": "재도출함",
            "criteria": {"does_this": "met", "reachable": "met", "harmful": "met",
                         "no_guard": "met", "survives_rebuttal": "met"},
            "entry_path": "main → /search → handle_search:42",
            "guard_scan": ["app.py 미들웨어 체인", "호출부 2곳"],
            "rebuttal": "ORM 이스케이프 반론 — raw cursor 직결이라 실패",
            "severity_final": "critical",
            "failure_scenario": "q=' OR '1'='1 로 전체 유출",
            "fix_sample": "bind", "fix_direction": "파라미터화", "appraisal": []}],
            "issues": []}
        self._v(obj)
        st = rj(os.path.join(self.run, "state.json"))
        self.assertEqual(st["stages"]["verify"]["3"], "done")

    def test_gate_unmet_confirmed_fails(self):
        obj = {"group_id": 3, "results": [{
            "id": "g3-002", "verdict": "confirmed", "rubric": "full", "score": 3,
            "rederivation": "x",
            "entry_path": "main → handler:10",
            "criteria": {"does_this": "met", "reachable": "met", "harmful": "met",
                         "no_guard": "unmet", "survives_rebuttal": "unmet"},
            "severity_final": "major", "failure_scenario": "s"}], "issues": []}
        with self.assertRaises(SystemExit):
            self._v(obj)

    def test_confirmed_needs_scenario(self):
        obj = {"group_id": 3, "results": [{
            "id": "g3-003", "verdict": "confirmed", "rubric": "full", "score": 5,
            "rederivation": "x",
            "entry_path": "main → h:1", "guard_scan": ["호출부"],
            "rebuttal": "반론 — 실패 이유",
            "criteria": {"does_this": "met", "reachable": "met", "harmful": "met",
                         "no_guard": "met", "survives_rebuttal": "met"},
            "severity_final": "critical", "failure_scenario": "  "}], "issues": []}
        with self.assertRaises(SystemExit):
            self._v(obj)

    def test_light_escalation_needs_full(self):
        obj = {"group_id": 3, "results": [{
            "id": "g3-004", "verdict": "confirmed", "rubric": "light", "score": 2,
            "rederivation": "x",
            "criteria": {"does_this": "met", "harmful": "met", "no_guard": "met"},
            "severity_final": "major", "failure_scenario": "s"}], "issues": []}
        with self.assertRaises(SystemExit):
            self._v(obj)

    def test_score_mismatch_fails(self):
        obj = {"group_id": 3, "results": [{
            "id": "g3-005", "verdict": "false_positive", "rubric": "full", "score": 5,
            "rederivation": "x",
            "guard_scan": ["호출부"], "rebuttal": "반론 — 실패",
            "criteria": {"does_this": "met", "reachable": "unmet", "harmful": "met",
                         "no_guard": "met", "survives_rebuttal": "met"},
            "severity_final": "minor",
            "reject_reason": "기준 ② reachable unmet — cfg.py:3 플래그 상시 off"}],
            "issues": []}
        with self.assertRaises(SystemExit):
            self._v(obj)  # score 5 인데 met 4

    def test_duplicate_reject_full_met_passes(self):
        # 중복 보고 기각: unmet 없이 met≥3 이어도 reject_reason + appraisal 이면 유효
        # (검증자 프로토콜의 중복 처리 규칙 — 기준을 unmet 으로 조작하지 않고 기각).
        obj = {"group_id": 3, "results": [{
            "id": "g3-006", "verdict": "false_positive", "rubric": "full", "score": 5,
            "rederivation": "재도출 — g3-001 과 동일 근본 원인",
            "entry_path": "main → /search → handle_search:42",
            "guard_scan": ["호출부"], "rebuttal": "반론 — 실패",
            "criteria": {"does_this": "met", "reachable": "met", "harmful": "met",
                         "no_guard": "met", "survives_rebuttal": "met"},
            "severity_final": "critical",
            "reject_reason": "중복: g3-001 과 동일 결함",
            "appraisal": ["두 클레임 모두 42-48 f-string SQL 직결 — 동일 근본 원인 확인"]}],
            "issues": []}
        self._v(obj)
        st = rj(os.path.join(self.run, "state.json"))
        self.assertEqual(st["stages"]["verify"]["3"], "done")

    # --- 규칙 6~9 + 게이트 기각 명시 (정탐 정밀도 개선) ---------------------

    def _full_confirmed(self, **over):
        base = {
            "id": "g3-010", "verdict": "confirmed", "rubric": "full", "score": 5,
            "rederivation": "재도출",
            "criteria": {"does_this": "met", "reachable": "met", "harmful": "met",
                         "no_guard": "met", "survives_rebuttal": "met"},
            "entry_path": "main → h:1", "guard_scan": ["미들웨어", "호출부"],
            "rebuttal": "최강 반론 — 실패 이유",
            "severity_final": "major", "failure_scenario": "시나리오",
            "appraisal": []}
        base.update(over)
        return {"group_id": 3, "results": [base], "issues": []}

    def test_impact_missing_warns_but_passes(self):
        # confirmed critical/major 에 impact 없음 → 불합격 아님, issues.jsonl 경고.
        obj = self._full_confirmed()
        self._v(obj)
        st = rj(os.path.join(self.run, "state.json"))
        self.assertEqual(st["stages"]["verify"]["3"], "done")
        issues = open(os.path.join(self.run, "issues.jsonl"),
                      encoding="utf-8").read()
        self.assertIn("impact 누락", issues)

    def test_impact_present_no_warn(self):
        obj = self._full_confirmed(impact="로그인 없이 고객 정보가 유출된다")
        self._v(obj)
        ipath = os.path.join(self.run, "issues.jsonl")
        if os.path.exists(ipath):
            self.assertNotIn("impact 누락", open(ipath, encoding="utf-8").read())

    def test_rule6_reachable_met_needs_entry_path(self):
        obj = self._full_confirmed()
        del obj["results"][0]["entry_path"]
        with self.assertRaises(SystemExit):
            self._v(obj)

    def test_rule7_no_guard_met_needs_guard_scan(self):
        obj = self._full_confirmed(guard_scan=[])
        with self.assertRaises(SystemExit):
            self._v(obj)

    def test_rule8_rebuttal_met_needs_rebuttal(self):
        obj = self._full_confirmed(rebuttal="   ")
        with self.assertRaises(SystemExit):
            self._v(obj)

    def test_evidence_not_required_when_unknown(self):
        # ②④ 가 unknown 이면 entry_path/guard_scan 불요 (met 일 때만 조건부 필수).
        obj = self._full_confirmed(
            score=3,
            criteria={"does_this": "met", "reachable": "unknown", "harmful": "met",
                      "no_guard": "unknown", "survives_rebuttal": "met"},
            failure_scenario="설정 X가 기본값일 때 전제 하에 유출")
        r = obj["results"][0]
        del r["entry_path"], r["guard_scan"]
        self._v(obj)  # 통과해야 함

    def test_light_exempt_from_evidence_fields(self):
        obj = {"group_id": 3, "results": [{
            "id": "g3-011", "verdict": "confirmed", "rubric": "light", "score": 2,
            "rederivation": "재도출",
            "criteria": {"does_this": "met", "harmful": "met", "no_guard": "unknown"},
            "severity_final": "minor", "failure_scenario": "s"}], "issues": []}
        self._v(obj)  # light 는 규칙 6~8 면제

    def test_light_no_guard_met_score_counts_all(self):
        # light 에서 no_guard=met 기록 시 score 는 기재 기준 전체 met 개수(3).
        obj = {"group_id": 3, "results": [{
            "id": "g3-014", "verdict": "confirmed", "rubric": "light", "score": 3,
            "rederivation": "재도출",
            "criteria": {"does_this": "met", "harmful": "met", "no_guard": "met"},
            "severity_final": "minor", "failure_scenario": "s"}], "issues": []}
        self._v(obj)  # guard_scan 불요(light 면제), score=3 이 정합

    def test_rule9_threshold_reject_needs_appraisal(self):
        fp = {
            "id": "g3-012", "verdict": "false_positive", "rubric": "full", "score": 2,
            "rederivation": "재도출",
            "criteria": {"does_this": "met", "reachable": "unknown", "harmful": "met",
                         "no_guard": "unknown", "survives_rebuttal": "unknown"},
            "severity_final": "major", "reject_reason": "임계 미달(met 2<3)"}
        obj = {"group_id": 3, "results": [dict(fp)], "issues": []}
        with self.assertRaises(SystemExit):
            self._v(obj)  # appraisal 없이 임계 기각 불가
        fp["appraisal"] = [{"item": "② 호출 경로 추적", "evidence": "라우터 등록 못 찾음"}]
        obj2 = {"group_id": 3, "results": [fp], "issues": []}
        self._v(obj2)  # 해소 시도 이력이 있으면 통과

    def test_gate_reject_must_name_unmet_criterion(self):
        fp = {
            "id": "g3-013", "verdict": "false_positive", "rubric": "full", "score": 3,
            "rederivation": "재도출",
            "entry_path": "main → h:1",
            "criteria": {"does_this": "met", "reachable": "met", "harmful": "met",
                         "no_guard": "unmet", "survives_rebuttal": "unknown"},
            "severity_final": "major",
            "reject_reason": "방어가 이미 있음"}  # 어느 기준인지 미명시
        obj = {"group_id": 3, "results": [dict(fp)], "issues": []}
        with self.assertRaises(SystemExit):
            self._v(obj)
        fp["reject_reason"] = "기준 ④ unmet — middleware/auth.py:30 정규화 확인"
        obj2 = {"group_id": 3, "results": [fp], "issues": []}
        self._v(obj2)  # ①~⑤ 표기로 명시하면 통과 (키 이름도 허용)


class TestRouteAndMergeAndClaims(unittest.TestCase):
    def setUp(self):
        self.run = tempfile.mkdtemp()
        wj(os.path.join(self.run, "groups.json"), groups_fixture(self.run))

    def tearDown(self):
        shutil.rmtree(self.run, ignore_errors=True)

    def test_route_hints(self):
        # g3 이 db/conn.py(그룹5 소유) 에 concurrency 힌트 → 그룹5 로 라우팅.
        wj(os.path.join(self.run, "defects", "3.json"), base_defects(3))
        wj(os.path.join(self.run, "defects", "5.json"),
           {"group_id": 5, "findings": [], "coverage": [], "cross_refs": [],
            "issues": []})
        vo.cmd_route_hints(_ns(run_dir=self.run, groups_file=None))
        h = rj(os.path.join(self.run, "hints", "5.json"))
        self.assertEqual(len(h["hints"]), 1)
        self.assertEqual(h["hints"][0]["from_group"], "3")

    def test_route_hints_covered_dropped(self):
        # 그룹5 에 이미 db/conn.py:15 concurrency finding 이 있으면 힌트 폐기.
        wj(os.path.join(self.run, "defects", "3.json"), base_defects(3))
        d5 = {"group_id": 5, "findings": [{
            "id": "g5-001", "pass": "primary", "category": "concurrency",
            "severity": "major", "confidence": "medium",
            "location": {"file": "db/conn.py", "start": 10, "end": 20, "symbol": "c"},
            "claim": "c", "rationale": "r", "snippet": "s", "evidence_files": []}],
            "coverage": [], "cross_refs": [], "issues": []}
        wj(os.path.join(self.run, "defects", "5.json"), d5)
        vo.cmd_route_hints(_ns(run_dir=self.run, groups_file=None))
        self.assertFalse(os.path.exists(os.path.join(self.run, "hints", "5.json")))

    def test_route_hints_already_routed_skipped(self):
        # 재실행(재개) 시 이미 라우팅된 힌트는 재소비하지 않는다(멱등).
        wj(os.path.join(self.run, "defects", "3.json"), base_defects(3))
        wj(os.path.join(self.run, "defects", "5.json"),
           {"group_id": 5, "findings": [], "coverage": [], "cross_refs": [],
            "issues": []})
        run_capture(vo.cmd_route_hints, _ns(run_dir=self.run, groups_file=None))
        out = run_capture(vo.cmd_route_hints,
                          _ns(run_dir=self.run, groups_file=None))
        self.assertEqual(out["stats"]["already_routed"], 1)
        self.assertEqual(out["stats"]["routed"], 0)
        h = rj(os.path.join(self.run, "hints", "5.json"))
        self.assertEqual(len(h["hints"]), 1)  # 1차 파일 보존

    def _route_then_sweep_residue(self):
        """1차 라우팅 → sweep 이 새 cross_ref 를 남김 → 병합 → 잔여 검사."""
        wj(os.path.join(self.run, "defects", "3.json"), base_defects(3))
        wj(os.path.join(self.run, "defects", "5.json"),
           {"group_id": 5, "findings": [], "coverage": [], "cross_refs": [],
            "issues": []})
        run_capture(vo.cmd_route_hints, _ns(run_dir=self.run, groups_file=None))
        sweep = {"group_id": 3, "findings": [], "coverage": [],
                 "cross_refs": [{"file": "db/conn.py", "line": 40,
                                 "category": "resource",
                                 "hint": "커서 미해제 의심"}], "issues": []}
        wj(os.path.join(self.run, "defects", "3.sweep.json"), sweep)
        with contextlib.redirect_stdout(io.StringIO()):
            vo.cmd_merge(_ns(kind="sweep", group="3", run_dir=self.run))
        return run_capture(
            vo.cmd_route_hints,
            _ns(run_dir=self.run, groups_file=None, residue_check=True))

    def test_residue_check(self):
        # sweep 이 남긴 cross_refs: merge 로 base 에 보존 → 라우팅 라운드가 없으므로
        # --residue-check 가 hints/residue.json 으로 표면화한다(사장 금지).
        out = self._route_then_sweep_residue()
        self.assertEqual(out["stats"]["already_routed"], 1)  # 1차 라우팅분
        res = rj(os.path.join(self.run, "hints", "residue.json"))
        self.assertEqual(len(res["hints"]), 1)
        self.assertEqual(res["hints"][0]["line"], 40)
        self.assertEqual(res["hints"][0]["owner_group"], "5")
        # 잔여 검사는 소비용 힌트 파일을 새로 만들지 않는다.
        h5 = rj(os.path.join(self.run, "hints", "5.json"))
        self.assertEqual(len(h5["hints"]), 1)
        self.assertFalse(os.path.exists(os.path.join(self.run, "hints", "3.json")))

    def test_residue_closed_for_later_routing(self):
        # 잔여 검사 뒤의 일반 라우팅 재실행(재개)은 residue 힌트를 재라우팅하지
        # 않는다 — "sweep 라운드 1회" 불변식 유지 + 1차 힌트 파일 덮어쓰기 방지.
        # 잔여 검사 재실행은 같은 잔여를 재산출한다(빈 목록으로 덮어쓰지 않음).
        self._route_then_sweep_residue()
        out = run_capture(vo.cmd_route_hints,
                          _ns(run_dir=self.run, groups_file=None))
        self.assertEqual(out["stats"]["routed"], 0)
        self.assertEqual(out["stats"]["already_routed"], 2)  # 1차분 + residue분
        h5 = rj(os.path.join(self.run, "hints", "5.json"))
        self.assertEqual(h5["hints"][0]["line"], 15)  # 1차 파일 원형 유지
        out2 = run_capture(
            vo.cmd_route_hints,
            _ns(run_dir=self.run, groups_file=None, residue_check=True))
        self.assertEqual(len(out2["residue"]), 1)
        res = rj(os.path.join(self.run, "hints", "residue.json"))
        self.assertEqual(res["hints"][0]["line"], 40)

    def test_extract_claims(self):
        wj(os.path.join(self.run, "defects", "3.json"), base_defects(3))
        vo.cmd_extract_claims(_ns(run_dir=self.run, group="3"))
        c = rj(os.path.join(self.run, "claims", "3.json"))
        self.assertEqual(c["claims"][0]["id"], "g3-001")
        self.assertNotIn("rationale", c["claims"][0])  # rationale 노출 금지

    def test_merge_second_dedupe(self):
        wj(os.path.join(self.run, "defects", "3.json"), base_defects(3))
        # 같은 위치·category 2차 발견 → 중복 스킵.
        second = {"group_id": 3, "findings": [
            {"id": "g3-s001", "pass": "second_pass", "category": "security",
             "severity": "critical", "confidence": "high",
             "location": {"file": "api/search.py", "start": 44, "end": 46, "symbol": "h"},
             "claim": "동일 SQLi", "rationale": "r", "snippet": "s",
             "evidence_files": []},
            {"id": "g3-s002", "pass": "second_pass", "category": "logic",
             "severity": "major", "confidence": "medium",
             "location": {"file": "api/search.py", "start": 44, "end": 46, "symbol": "h"},
             "claim": "다른 결함", "rationale": "r", "snippet": "s",
             "evidence_files": []}], "coverage": [], "cross_refs": [], "issues": []}
        wj(os.path.join(self.run, "defects", "3.second.json"), second)
        vo.cmd_merge(_ns(kind="second", group="3", run_dir=self.run))
        merged = rj(os.path.join(self.run, "defects", "3.json"))
        ids = {f["id"] for f in merged["findings"]}
        self.assertIn("g3-001", ids)     # 기존 보존
        self.assertNotIn("g3-s001", ids)  # 같은 category 중복 스킵
        self.assertIn("g3-s002", ids)     # 다른 category 는 별개로 보존

    def test_merge_sweep_id_collision(self):
        wj(os.path.join(self.run, "defects", "3.json"), base_defects(3))
        sweep = {"group_id": 3, "findings": [
            {"id": "g3-001", "pass": "sweep", "category": "security",
             "severity": "major", "confidence": "low",
             "location": {"file": "api/search.py", "start": 1, "end": 2, "symbol": "h"},
             "claim": "c", "rationale": "r", "snippet": "s", "evidence_files": []}],
            "coverage": [], "cross_refs": [], "issues": []}
        wj(os.path.join(self.run, "defects", "3.sweep.json"), sweep)
        with self.assertRaises(SystemExit):
            vo.cmd_merge(_ns(kind="sweep", group="3", run_dir=self.run))

    def test_merge_sweep_location_dedupe(self):
        # 2차 패스가 먼저 병합되는 순서에서 마지막에 병합되는 sweep 발견도
        # 위치 중첩+동일 category 를 걸러야 중복 보고가 없다(다른 category 는 보존).
        wj(os.path.join(self.run, "defects", "3.json"), base_defects(3))
        sweep = {"group_id": 3, "findings": [
            {"id": "g3-w001", "pass": "sweep", "category": "security",
             "severity": "major", "confidence": "medium",
             "location": {"file": "api/search.py", "start": 44, "end": 46,
                          "symbol": "h"},
             "claim": "동일 SQLi", "rationale": "r", "snippet": "s",
             "evidence_files": []},
            {"id": "g3-w002", "pass": "sweep", "category": "fault",
             "severity": "major", "confidence": "medium",
             "location": {"file": "api/search.py", "start": 44, "end": 46,
                          "symbol": "h"},
             "claim": "다른 결함", "rationale": "r", "snippet": "s",
             "evidence_files": []}], "coverage": [], "cross_refs": [],
            "issues": []}
        wj(os.path.join(self.run, "defects", "3.sweep.json"), sweep)
        with contextlib.redirect_stdout(io.StringIO()):
            vo.cmd_merge(_ns(kind="sweep", group="3", run_dir=self.run))
        merged = rj(os.path.join(self.run, "defects", "3.json"))
        ids = {f["id"] for f in merged["findings"]}
        self.assertNotIn("g3-w001", ids)  # 위치+category 중복 → 스킵
        self.assertIn("g3-w002", ids)     # 다른 category → 보존

    def test_merge_preserves_cross_refs(self):
        # sweep/2차의 cross_refs 는 base 로 보존 병합된다(중복 file+line+category 스킵).
        wj(os.path.join(self.run, "defects", "3.json"), base_defects(3))
        sweep = {"group_id": 3, "findings": [], "coverage": [],
                 "cross_refs": [
                     {"file": "db/conn.py", "line": 15, "category": "concurrency",
                      "hint": "중복 힌트"},  # base 와 동일 키 → 스킵
                     {"file": "db/conn.py", "line": 40, "category": "resource",
                      "hint": "커서 미해제 의심"}], "issues": []}
        wj(os.path.join(self.run, "defects", "3.sweep.json"), sweep)
        with contextlib.redirect_stdout(io.StringIO()):
            vo.cmd_merge(_ns(kind="sweep", group="3", run_dir=self.run))
        merged = rj(os.path.join(self.run, "defects", "3.json"))
        keys = {(c["file"], c["line"], c["category"])
                for c in merged["cross_refs"]}
        self.assertEqual(keys, {("db/conn.py", 15, "concurrency"),
                                ("db/conn.py", 40, "resource")})
        # 기존 힌트 문구 보존(중복 키는 base 우선).
        self.assertEqual(merged["cross_refs"][0]["hint"], "전역 커넥션 락 없음")

    def test_merge_verify_batches(self):
        for n in (1, 2):
            wj(os.path.join(self.run, "verified", f"3.batch-{n}.json"),
               {"group_id": 3, "results": [{
                   "id": f"g3-00{n}", "verdict": "false_positive", "rubric": "full",
                   "score": 2, "rederivation": "x",
                   "criteria": {"does_this": "met", "reachable": "unknown",
                                "harmful": "met", "no_guard": "unknown",
                                "survives_rebuttal": "unknown"},
                   "severity_final": "minor", "reject_reason": "임계 미달",
                   "appraisal": [{"item": "② 추적", "evidence": "확정 실패"}]}],
                "issues": []})
        vo.cmd_merge(_ns(kind="verify", group="3", run_dir=self.run))
        m = rj(os.path.join(self.run, "verified", "3.json"))
        self.assertEqual(len(m["results"]), 2)


class TestEnvironmentValidation(unittest.TestCase):
    """brief.environment 형태 검사 (§1, 존재 시에만 — init-state 경유)."""

    def setUp(self):
        self.run = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.run, ignore_errors=True)

    def _env(self):
        return {
            "os_targets": {"value": ["linux"], "evidence": "CI 매트릭스 ubuntu만"},
            "arch_targets": {"value": ["x86_64"], "evidence": "CI 매트릭스"},
            "concurrency_model": {"value": "단일 스레드", "evidence": "main.py 동기 루프"},
            "runtime": {"value": "Python 3.9+", "evidence": "pyproject requires-python"},
            "exposure": {"value": "unknown", "evidence": "README 배포 절 없음(확인 시도)"},
        }

    def _init(self, env):
        gj = groups_fixture(self.run)
        if env is not None:
            gj["brief"]["environment"] = env
        wj(os.path.join(self.run, "groups.json"), gj)
        return vo.cmd_init_state(_ns(run_dir=self.run, groups_file=None))

    def test_absent_environment_ok(self):
        self._init(None)  # 구 런 하위 호환 — 없으면 검사 안 함

    def test_valid_environment_ok(self):
        self._init(self._env())

    def test_missing_key_fails(self):
        env = self._env()
        del env["runtime"]
        with self.assertRaises(SystemExit) as cm:
            self._init(env)
        self.assertEqual(cm.exception.code, 2)

    def test_missing_evidence_fails(self):
        env = self._env()
        env["os_targets"] = {"value": ["linux"], "evidence": "  "}
        with self.assertRaises(SystemExit):
            self._init(env)

    def test_unknown_extra_key_fails(self):
        env = self._env()
        env["compiler"] = {"value": "gcc", "evidence": "Makefile"}
        with self.assertRaises(SystemExit):
            self._init(env)


class TestBuildReport(unittest.TestCase):
    def setUp(self):
        self.run = tempfile.mkdtemp()
        wj(os.path.join(self.run, "groups.json"), groups_fixture(self.run))
        vo.cmd_init_state(_ns(run_dir=self.run, groups_file=None))

    def tearDown(self):
        shutil.rmtree(self.run, ignore_errors=True)

    def _seed(self, n):
        findings, results = [], []
        for i in range(n):
            fid = f"g3-{i:03d}"
            sev = "critical" if i % 3 == 0 else ("major" if i % 3 == 1 else "minor")
            findings.append({
                "id": fid, "pass": "primary", "category": "security",
                "severity": sev, "confidence": "high",
                "location": {"file": "api/search.py", "start": i + 1, "end": i + 1,
                             "symbol": "h"},
                "claim": "c", "rationale": "메커니즘 설명", "snippet": "bad code",
                "evidence_files": []})
            results.append({
                "id": fid, "verdict": "confirmed",
                "rubric": "light" if sev == "minor" else "full",
                "score": 3 if sev == "minor" else 5, "rederivation": "x",
                "criteria": ({"does_this": "met", "harmful": "met", "no_guard": "met"}
                             if sev == "minor" else
                             {"does_this": "met", "reachable": "met", "harmful": "met",
                              "no_guard": "met", "survives_rebuttal": "met"}),
                "entry_path": "" if sev == "minor" else "main → h:1",
                "guard_scan": [] if sev == "minor" else ["호출부"],
                "rebuttal": "" if sev == "minor" else "반론 — 실패",
                "severity_final": sev, "failure_scenario": "시나리오",
                "fix_sample": "fixed", "fix_direction": "방향"})
        wj(os.path.join(self.run, "defects", "3.json"),
           {"group_id": 3, "findings": findings, "coverage": [], "cross_refs": [],
            "issues": []})
        wj(os.path.join(self.run, "verified", "3.json"),
           {"group_id": 3, "results": results, "issues": []})

    def test_single_file(self):
        self._seed(3)
        build_report.main(["--run-dir", self.run])
        path = os.path.join(self.run, "감사보고서.md")
        self.assertTrue(os.path.exists(path))
        # 전 기준 met → 조건부 인용 블록 없음("[조건부]" 자체는 범례에 항상 등장).
        self.assertNotIn("미확정(unknown) 기준", open(path, encoding="utf-8").read())

    def test_reader_format_elements(self):
        # 낯선 독자용 요소: claim 제목, 범례, 색인 표, 언어 태그, 도달 경로, 검증 노트.
        self._seed(3)
        build_report.main(["--run-dir", self.run])
        text = open(os.path.join(self.run, "감사보고서.md"), encoding="utf-8").read()
        self.assertIn("### 1. 🔴 c", text)            # 제목 = claim(위치 아님)
        self.assertIn("이 보고서를 읽는 법", text)     # 용어 범례
        self.assertIn("## 발견 목록", text)            # 색인 표
        self.assertIn("| `g3-000` |", text)
        self.assertIn("```python", text)               # .py → 언어 태그
        self.assertIn("**도달 경로**", text)
        self.assertIn("main → h:1", text)
        self.assertIn("검증 노트", text)               # 접이식 검증 증거
        self.assertIn("반론 — 실패", text)
        self.assertIn("- 호출부", text)                # guard_scan 목록

    def test_claim_fallback_to_location(self):
        # claim 없는 구 런 산출 → 제목이 위치로 폴백.
        self._seed(1)
        dpath = os.path.join(self.run, "defects", "3.json")
        d = rj(dpath)
        d["findings"][0]["claim"] = ""
        wj(dpath, d)
        build_report.main(["--run-dir", self.run])
        text = open(os.path.join(self.run, "감사보고서.md"), encoding="utf-8").read()
        self.assertIn("### 1. 🔴 `api/search.py:1` · `h`", text)

    def test_appraisal_string_items_tolerated(self):
        # validate 는 appraisal 을 리스트 여부만 검사 → 문자열 항목이 스키마를
        # 통과할 수 있다(중복 기각 테스트가 실제 사례). 렌더가 죽으면 안 된다.
        self._seed(1)
        vpath = os.path.join(self.run, "verified", "3.json")
        v = rj(vpath)
        v["results"][0]["appraisal"] = ["문자열 형태의 확인 이력",
                                        {"item": "항목", "evidence": "근거"}]
        wj(vpath, v)
        build_report.main(["--run-dir", self.run])
        text = open(os.path.join(self.run, "감사보고서.md"), encoding="utf-8").read()
        self.assertIn("- 문자열 형태의 확인 이력", text)
        self.assertIn("- 항목: 근거", text)

    def test_impact_and_file_role(self):
        # impact/coverage role 이 있으면 렌더, 없는 발견에는 항목 자체가 생략.
        self._seed(2)  # g3-000 critical, g3-001 major
        dpath = os.path.join(self.run, "defects", "3.json")
        d = rj(dpath)
        d["coverage"] = [{"path": "api/search.py", "role": "검색 API HTTP 핸들러",
                          "top_risk": "g3-000"}]
        wj(dpath, d)
        vpath = os.path.join(self.run, "verified", "3.json")
        v = rj(vpath)
        v["results"][0]["impact"] = "로그인 없이 고객 정보가 유출된다"
        wj(vpath, v)
        build_report.main(["--run-dir", self.run])
        text = open(os.path.join(self.run, "감사보고서.md"), encoding="utf-8").read()
        self.assertIn("**파일 역할**: 검색 API HTTP 핸들러", text)
        self.assertIn("로그인 없이 고객 정보가 유출된다", text)
        self.assertEqual(text.count("**영향**"), 1)  # impact 없는 발견은 생략

    def test_conditional_badge_on_unknown(self):
        # unknown 이 남은 confirmed → [조건부] 배지 + unknown 기준 나열.
        wj(os.path.join(self.run, "defects", "3.json"),
           {"group_id": 3, "findings": [{
               "id": "g3-001", "pass": "primary", "category": "security",
               "severity": "major", "confidence": "medium",
               "location": {"file": "api/search.py", "start": 5, "end": 6,
                            "symbol": "h"},
               "claim": "c", "rationale": "메커니즘", "snippet": "bad",
               "evidence_files": []}],
            "coverage": [], "cross_refs": [], "issues": []})
        wj(os.path.join(self.run, "verified", "3.json"),
           {"group_id": 3, "results": [{
               "id": "g3-001", "verdict": "confirmed", "rubric": "full", "score": 3,
               "rederivation": "x",
               "criteria": {"does_this": "met", "reachable": "unknown",
                            "harmful": "met", "no_guard": "unknown",
                            "survives_rebuttal": "met"},
               "rebuttal": "반론 — 실패",
               "severity_final": "major",
               "failure_scenario": "WAF 없는 배포에서 유출"}], "issues": []})
        build_report.main(["--run-dir", self.run])
        text = open(os.path.join(self.run, "감사보고서.md"), encoding="utf-8").read()
        self.assertIn("[조건부]", text)
        self.assertIn("②reachable", text)
        self.assertIn("④no_guard", text)

    def test_residue_section(self):
        # 미소진 힌트가 있으면 요약부에 표면화, 없으면(파일 부재) 절 자체가 없다.
        self._seed(3)
        build_report.main(["--run-dir", self.run])
        path = os.path.join(self.run, "감사보고서.md")
        self.assertNotIn("미소진 힌트", open(path, encoding="utf-8").read())
        wj(os.path.join(self.run, "hints", "residue.json"),
           {"hints": [{"file": "db/conn.py", "line": 40, "category": "resource",
                       "hint": "커서 미해제 의심", "from_group": "3",
                       "owner_group": "5"}]})
        build_report.main(["--run-dir", self.run])
        text = open(path, encoding="utf-8").read()
        self.assertIn("미소진 힌트", text)
        self.assertIn("`db/conn.py:40`", text)
        self.assertIn("리소스", text)

    def test_split_over_threshold(self):
        self._seed(18)  # >15 → 분할
        build_report.main(["--run-dir", self.run])
        for name in ("00_요약.md", "01_critical_major.md", "02_minor.md"):
            self.assertTrue(os.path.exists(os.path.join(self.run, name)), name)
        summary = open(os.path.join(self.run, "00_요약.md"), encoding="utf-8").read()
        self.assertIn("Critical", summary)


class TestEndToEndCLI(unittest.TestCase):
    """스크립트를 서브프로세스 CLI 로 호출하는 스모크 테스트."""

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_select_then_group_cli(self):
        write(os.path.join(self.root, "pkg/a.py"), "from pkg import b\nx=1\n")
        write(os.path.join(self.root, "pkg/b.py"), "y=1\n")
        targets = os.path.join(self.root, "targets.json")
        r1 = subprocess.run(
            [sys.executable, os.path.join(HERE, "select_targets.py"), self.root,
             "--out", targets], capture_output=True, text=True)
        self.assertEqual(r1.returncode, 0, r1.stderr)
        groups = os.path.join(self.root, "groups.json")
        r2 = subprocess.run(
            [sys.executable, os.path.join(HERE, "group_by_lines.py"), "build",
             "--targets", targets, "--run-id", "20260101-000000", "--out", groups],
            capture_output=True, text=True)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        gj = rj(groups)
        self.assertEqual(len(gj["groups"]), 1)


# ---------------------------------------------------------------------------

class _ns:
    """argparse Namespace 대용 — cmd_* 함수 직접 호출용."""
    def __init__(self, **kw):
        self.__dict__.update(kw)
        for k in ("group", "groups_file", "file", "no_coverage", "out",
                  "outcome", "action", "context"):
            self.__dict__.setdefault(k, None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
