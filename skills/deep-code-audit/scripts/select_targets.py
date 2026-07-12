#!/usr/bin/env python3
"""select_targets.py — 감사 대상 파일을 core / low / exclude 로 3분류한다.

판단은 모델(감사 브리프)에게, 결정적 패턴 분류만 이 스크립트가 한다. 분류 요약
(excluded / low / 크기 가드 배제)은 오케스트레이터가 브리프 작성 시점에 검토하고,
프로젝트 성격과 어긋나면 --include / --exclude 오버라이드로 교정해 1회 재실행한다.

출력(JSON, stdout 또는 --out):
  {
    "target_root": "...",
    "size_guard_lines": 5000,
    "files":         [{"path": "rel", "lines": N, "class": "core"|"low"}, ...],
    "excluded":      ["vendor/", "dist/", ...],          # 배제 요약 (디렉터리/패턴)
    "excluded_files":[{"path": "rel", "reason": "..."}],  # 개별 배제 (크기 가드 등)
    "low":           ["rel", ...]                         # low 목록 (인모델 검토용)
  }

분류 규칙 (development-plan §2 Stage 1):
  core    : 일반 소스 + 텍스트 설정·인프라 파일(YAML/TOML/Dockerfile/CI/SQL 등)
  low     : 테스트·픽스처 (심각도 상한 minor)
  exclude : 벤더·빌드 산출물·생성 코드 + 도구/VCS 디렉터리(.git/, .deep-code-audit/)
            + 락·데이터·바이너리 + 비소스 텍스트 5000라인 초과(크기 가드)
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys

# --- 확장자 사전 -----------------------------------------------------------

# 소스 확장자: 크기 가드 면제 대상(코드가 길 수 있으므로 라인 수로 배제하지 않음).
SOURCE_EXTS = {
    ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".rs",
    ".java", ".kt", ".kts", ".scala", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp",
    ".hh", ".cs", ".rb", ".php", ".swift", ".m", ".mm", ".dart", ".lua", ".r",
    ".pl", ".pm", ".ex", ".exs", ".erl", ".clj", ".cljs", ".hs", ".ml", ".fs",
    ".vue", ".svelte", ".sh", ".bash", ".zsh", ".ps1", ".sql", ".gradle",
    ".groovy", ".proto", ".tf", ".hcl",
}

# 텍스트 설정·인프라 확장자: core 로 스캔하되 크기 가드 적용 대상.
CONFIG_EXTS = {
    ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".properties", ".env",
    ".json", ".json5", ".jsonc", ".xml", ".gradle", ".cmake", ".mk",
    ".dockerignore", ".editorconfig",
}

# 확장자 없이 이름만으로 core 인 인프라 파일.
CONFIG_BASENAMES = {
    "dockerfile", "makefile", "cmakelists.txt", "jenkinsfile", "vagrantfile",
    "procfile", "gemfile", "rakefile", "podfile", ".gitlab-ci.yml",
    "docker-compose.yml", "docker-compose.yaml",
}

# 바이너리·데이터·아카이브 확장자 → 무조건 exclude.
BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp", ".tif",
    ".tiff", ".pdf", ".zip", ".gz", ".tar", ".tgz", ".bz2", ".xz", ".7z", ".rar",
    ".jar", ".war", ".ear", ".class", ".o", ".a", ".so", ".dylib", ".dll", ".exe",
    ".bin", ".wasm", ".pyc", ".pyo", ".mo", ".woff", ".woff2", ".ttf", ".otf",
    ".eot", ".mp3", ".mp4", ".wav", ".avi", ".mov", ".mkv", ".webm", ".flac",
    ".db", ".sqlite", ".sqlite3", ".dat", ".parquet", ".avro", ".pkl", ".npy",
    ".npz", ".h5", ".hdf5", ".onnx", ".pt", ".pth", ".ckpt", ".model",
    ".xlsx", ".xls", ".docx", ".doc", ".pptx", ".ppt",
}

# 락·데이터 파일명 → exclude (락은 실코드가 아니고 그룹 예산을 삼킨다).
LOCK_BASENAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "cargo.lock", "go.sum",
    "poetry.lock", "pipfile.lock", "composer.lock", "gemfile.lock", "flake.lock",
    "packages.lock.json", "podfile.lock",
}

# 디렉터리 경로 조각 → 그 하위 전체 exclude.
EXCLUDE_DIR_PARTS = {
    ".git", ".hg", ".svn", ".deep-code-audit", "node_modules", "vendor",
    "bower_components", "dist", "build", "out", "target", ".next", ".nuxt",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".tox", ".venv", "venv",
    "env", ".gradle", ".idea", ".vscode", "coverage", ".terraform",
    "site-packages", "eggs", ".eggs",
}

# 생성 코드 표기 glob → exclude.
GENERATED_GLOBS = [
    "*.min.js", "*.min.css", "*_pb2.py", "*_pb2_grpc.py", "*.pb.go", "*.g.dart",
    "*.generated.*", "*.designer.cs", "*.d.ts", "*-lock.yaml",
]

# 테스트·픽스처 → low. 경로 조각 / 파일명 패턴.
LOW_DIR_PARTS = {"test", "tests", "__tests__", "spec", "specs", "fixtures",
                 "testdata", "__mocks__", "e2e", "integration_tests"}
LOW_NAME_GLOBS = ["*_test.*", "*_test", "test_*", "*.test.*", "*.spec.*",
                  "*_spec.*", "conftest.py"]


def _rel_parts(rel: str):
    return rel.replace("\\", "/").split("/")


def count_lines(abspath: str) -> int:
    """텍스트 파일의 라인 수. 바이너리(널 바이트)면 -1 반환."""
    try:
        with open(abspath, "rb") as fh:
            chunk = fh.read(8192)
            if b"\x00" in chunk:
                return -1
            n = chunk.count(b"\n")
            while True:
                block = fh.read(1 << 20)
                if not block:
                    break
                n += block.count(b"\n")
        # 마지막 줄이 개행으로 끝나지 않는 경우 보정.
        try:
            with open(abspath, "rb") as fh:
                fh.seek(-1, os.SEEK_END)
                if fh.read(1) not in (b"\n", b""):
                    n += 1
        except OSError:
            n += 1
        return n
    except (OSError, ValueError):
        return -1


def _matches_any(name: str, globs) -> bool:
    low = name.lower()
    return any(fnmatch.fnmatch(low, g) for g in globs)


def classify(rel: str, extra_exclude, extra_include):
    """경로 하나를 'core' | 'low' | 'exclude' 로 분류. (분류, 사유) 반환."""
    parts = _rel_parts(rel)
    name = parts[-1]
    lname = name.lower()
    _, ext = os.path.splitext(lname)

    # 사용자 include 오버라이드가 최우선 — 강제로 core 취급.
    forced_core = any(fnmatch.fnmatch(rel, pat) for pat in extra_include)

    # 사용자 exclude 오버라이드.
    if any(fnmatch.fnmatch(rel, pat) for pat in extra_exclude):
        return "exclude", "user-exclude 패턴"

    # 도구/VCS/벤더/빌드 디렉터리.
    for part in parts[:-1]:
        if part.lower() in EXCLUDE_DIR_PARTS:
            return "exclude", f"디렉터리 배제: {part}/"

    if not forced_core:
        # 락·바이너리·생성 코드.
        if lname in LOCK_BASENAMES:
            return "exclude", "락 파일"
        if ext in BINARY_EXTS:
            return "exclude", f"바이너리/데이터 확장자 {ext}"
        if _matches_any(name, GENERATED_GLOBS):
            return "exclude", "생성 코드 패턴"

    # low: 테스트·픽스처.
    if not forced_core:
        for part in parts[:-1]:
            if part.lower() in LOW_DIR_PARTS:
                return "low", f"테스트 디렉터리: {part}/"
        if _matches_any(name, LOW_NAME_GLOBS):
            return "low", "테스트 파일명 패턴"

    # core 판정: 소스/설정 확장자 또는 인프라 basename.
    if forced_core:
        return "core", "user-include 강제"
    if ext in SOURCE_EXTS:
        return "core", f"소스 확장자 {ext}"
    if ext in CONFIG_EXTS or lname in CONFIG_BASENAMES:
        return "core", "텍스트 설정/인프라"
    # 확장자 없는 스크립트류(shebang) 등은 core 후보지만, 미지 확장자는 보수적으로 배제.
    if ext == "":
        return "core", "확장자 없음(스크립트 추정)"
    return "exclude", f"미지 확장자 {ext}"


def is_source(rel: str) -> bool:
    _, ext = os.path.splitext(rel.lower())
    return ext in SOURCE_EXTS


def walk(target_root: str, extra_exclude, extra_include, size_guard: int):
    core_low = []
    excluded_dirs = set()
    excluded_files = []
    low = []

    for dirpath, dirnames, filenames in os.walk(target_root):
        # 배제 디렉터리는 진입 자체를 생략(성능 + 자기 배제).
        pruned = []
        for d in list(dirnames):
            if d.lower() in EXCLUDE_DIR_PARTS:
                rel_dir = os.path.relpath(os.path.join(dirpath, d), target_root)
                excluded_dirs.add(rel_dir.replace("\\", "/") + "/")
            else:
                pruned.append(d)
        dirnames[:] = pruned

        for fn in filenames:
            abspath = os.path.join(dirpath, fn)
            if os.path.islink(abspath):
                continue
            rel = os.path.relpath(abspath, target_root).replace("\\", "/")
            cls, reason = classify(rel, extra_exclude, extra_include)
            if cls == "exclude":
                # 디렉터리 단위 배제는 요약에만, 파일 단위 배제만 상세 기록.
                excluded_files.append({"path": rel, "reason": reason})
                continue

            lines = count_lines(abspath)
            if lines < 0:
                excluded_files.append({"path": rel, "reason": "바이너리 감지(널 바이트)"})
                continue

            # 크기 가드: 비소스 텍스트가 임계 초과면 생성물로 간주.
            # 단 사용자 --include 강제 포함 파일은 가드 면제(오배제 교정 경로).
            forced = any(fnmatch.fnmatch(rel, pat) for pat in extra_include)
            if not forced and not is_source(rel) and lines > size_guard:
                excluded_files.append(
                    {"path": rel,
                     "reason": f"size-guard: 비소스 텍스트 {lines}라인 > {size_guard}"})
                continue

            core_low.append({"path": rel, "lines": lines, "class": cls})
            if cls == "low":
                low.append(rel)

    core_low.sort(key=lambda f: f["path"])
    excluded_files.sort(key=lambda f: f["path"])
    low.sort()
    return {
        "target_root": os.path.abspath(target_root),
        "size_guard_lines": size_guard,
        "files": core_low,
        "excluded": sorted(excluded_dirs),
        "excluded_files": excluded_files,
        "low": low,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="감사 대상 파일 core/low/exclude 분류")
    ap.add_argument("target_root", help="감사 대상 저장소 루트")
    ap.add_argument("--out", help="출력 JSON 경로 (미지정 시 stdout)")
    ap.add_argument("--size-guard", type=int, default=5000,
                    help="비소스 텍스트 파일 배제 라인 임계 (기본 5000)")
    ap.add_argument("--include", action="append", default=[],
                    help="강제 core 포함 glob (반복 가능). 상대 경로 기준")
    ap.add_argument("--exclude", action="append", default=[],
                    help="강제 배제 glob (반복 가능). 상대 경로 기준")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.target_root):
        ap.error(f"디렉터리가 아님: {args.target_root}")

    result = walk(args.target_root, args.exclude, args.include, args.size_guard)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
