"""交付打包脚本：生成排除敏感信息与缓存的 zip。

用法：
    python demo/scripts/package_delivery.py [--output dist/FinEventAgent-v1.0.0.zip]
"""
from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXCLUDE_PATTERNS = {
    ".env",
    "__pycache__",
    "*.pyc",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    ".idea",
    ".vscode",
    "demo/output",
    "demo/test_results",
    "demo/test_results_after",
    "demo/test_results_l1_l4",
    "demo/test_results_l1l4",
    "demo/test_results_l1l4_v2",
    "demo/sse_out*.txt",
    "demo/server*.log",
    "demo/server*.err",
    "*.zip",
    ".DS_Store",
    "HANDOFF.md",
    "docs",
    "产品经理（AI方向） · 笔试题目.md",
    "产品经理（AI方向） · 笔试交付要求说明.md",
}


def should_exclude(path: Path) -> bool:
    rel = path.relative_to(ROOT).as_posix()
    for pat in EXCLUDE_PATTERNS:
        if "*" in pat:
            import fnmatch
            if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(path.name, pat):
                return True
        elif rel == pat or rel.startswith(pat + "/") or path.name == pat:
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dist/FinEventAgent-v1.1.0.zip")
    args = parser.parse_args()

    out_path = ROOT / args.output
    out_path.parent.mkdir(exist_ok=True)

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in ROOT.rglob("*"):
            if path.is_dir() or should_exclude(path):
                continue
            arcname = path.relative_to(ROOT).as_posix()
            zf.write(path, arcname)
            print(f"  + {arcname}")

    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"\n打包完成：{out_path}（{size_mb:.1f} MB）")


if __name__ == "__main__":
    main()
