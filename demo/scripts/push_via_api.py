"""Push local HEAD to GitHub via Git Data API (when git://https push is blocked)."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

TOKEN = os.environ.get("GH_TOKEN", "")
OWNER = "744381820-lab"
REPO = "FinEventAgent"
API = f"https://api.github.com/repos/{OWNER}/{REPO}"
ROOT = Path(__file__).resolve().parents[2]


def api(method: str, url: str, data=None, retries: int = 6):
    body = None if data is None else json.dumps(data).encode("utf-8")
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", f"Bearer {TOKEN}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            # don't retry hard client errors except 409/429
            if e.code in {409, 429, 500, 502, 503, 504} and attempt < retries:
                time.sleep(min(2 ** attempt, 20))
                last_err = RuntimeError(f"HTTP {e.code} {method} {url}: {err[:800]}")
                continue
            raise RuntimeError(f"HTTP {e.code} {method} {url}: {err[:800]}") from e
        except Exception as e:  # network blips
            last_err = e
            if attempt < retries:
                time.sleep(min(2 ** attempt, 20))
                continue
            raise
    raise RuntimeError(str(last_err))


def main() -> None:
    if not TOKEN:
        raise SystemExit("GH_TOKEN missing")

    out = subprocess.check_output(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
    )
    files = [p.decode("utf-8") for p in out.split(b"\0") if p]
    print(f"files={len(files)}")

    tree_items = []
    for i, rel in enumerate(files, 1):
        if rel == ".env" or rel.endswith("/.env"):
            print("skip", rel)
            continue
        content = (ROOT / rel).read_bytes()
        blob = api(
            "POST",
            f"{API}/git/blobs",
            {
                "content": base64.b64encode(content).decode("ascii"),
                "encoding": "base64",
            },
        )
        tree_items.append(
            {
                "path": rel.replace("\\", "/"),
                "mode": "100644",
                "type": "blob",
                "sha": blob["sha"],
            }
        )
        if i % 10 == 0 or i == len(files):
            print(f"  blobs {i}/{len(files)}")

    tree = api("POST", f"{API}/git/trees", {"tree": tree_items})
    print("tree", tree["sha"])

    # 默认 orphan：覆盖历史上可能误传的笔试/过程文件
    orphan = os.getenv("ORPHAN_PUSH", "1") == "1"
    parents = []
    if not orphan:
        try:
            ref = api("GET", f"{API}/git/ref/heads/main")
            parents = [ref["object"]["sha"]]
            print("parent", parents[0])
        except RuntimeError as e:
            if "404" not in str(e):
                raise
    else:
        print("orphan commit (history rewrite)")

    commit = api(
        "POST",
        f"{API}/git/commits",
        {
            "message": "release: FinEventAgent public clean tree (no exam/process docs)",
            "tree": tree["sha"],
            "parents": parents,
        },
    )
    print("commit", commit["sha"])

    try:
        api("GET", f"{API}/git/ref/heads/main")
        api("PATCH", f"{API}/git/refs/heads/main", {"sha": commit["sha"], "force": True})
        print("force-updated refs/heads/main")
    except RuntimeError as e:
        if "404" not in str(e):
            raise
        api("POST", f"{API}/git/refs", {"ref": "refs/heads/main", "sha": commit["sha"]})
        print("created refs/heads/main")

    try:
        api("POST", f"{API}/git/refs", {"ref": "refs/tags/v1.1.0", "sha": commit["sha"]})
        print("tag v1.1.0 created")
    except RuntimeError as e:
        if "422" in str(e) or "Reference already exists" in str(e):
            api("PATCH", f"{API}/git/refs/tags/v1.1.0", {"sha": commit["sha"], "force": True})
            print("tag v1.1.0 updated")
        else:
            raise

    # Release
    try:
        api("GET", f"{API}/releases/tags/v1.1.0")
        print("release already exists")
    except RuntimeError as e:
        if "404" not in str(e):
            raise
        api(
            "POST",
            f"{API}/releases",
            {
                "tag_name": "v1.1.0",
                "name": "v1.1.0",
                "body": (
                    "## FinEventAgent v1.1.0\n\n"
                    "- 主控调度 + L1-L4 分层 Agent\n"
                    "- SSE 全量分析 / 局部重算\n"
                    "- Kimi 式对话流 + 舆情图表 + 进门 MCP 数据卡\n"
                    "- 统一配置：demo/config/analysis.json\n\n"
                    "快速开始见 README。"
                ),
            },
        )
        print("release created")

    print("OK", f"https://github.com/{OWNER}/{REPO}")


if __name__ == "__main__":
    main()
