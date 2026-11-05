"""JavaGuide 仓库抓取：gitee 镜像优先（github 原仓库回退），License 校验 + MANIFEST 落盘。

依据 specs/02-in-progress/question_bank/java-guide-import.md 模块一契约：
- clone --depth 1 到 vendor 目录，已存在则报错退出（重取 = 手动删目录）；
- 成功后删除 .git、写 MANIFEST.json（commit SHA / 日期 / 所用源）；
- LICENSE 必须含 Apache 字样（Apache-2.0 已实证），缺失即失败并清理目录。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

PRIMARY_URL = "https://gitee.com/SnailClimb/JavaGuide"
FALLBACK_URL = "https://github.com/Snailclimb/JavaGuide"
LICENSE_MARKER = "Apache License"


@dataclass
class FetchReport:
    vendor_dir: Path
    url: str
    commit: str


def _run_git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def fetch_java_guide(vendor_dir: Path) -> FetchReport:
    """抓取 JavaGuide 到 vendor_dir（gitee → github 回退），返回所用源与 commit。"""
    if vendor_dir.exists():
        raise RuntimeError(f"{vendor_dir} 已存在：如需重取请先手动删除该目录")
    vendor_dir.parent.mkdir(parents=True, exist_ok=True)
    used_url: str | None = None
    errors: list[str] = []
    for url in (PRIMARY_URL, FALLBACK_URL):
        try:
            _run_git(["clone", "--depth", "1", url, str(vendor_dir)], vendor_dir.parent)
            used_url = url
            break
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            errors.append(f"{url}: {getattr(exc, 'stderr', None) or exc}")
    if used_url is None:
        shutil.rmtree(vendor_dir, ignore_errors=True)
        raise RuntimeError("JavaGuide 克隆失败（gitee 与 github 均不可用）:\n" + "\n".join(errors))
    license_path = vendor_dir / "LICENSE"
    if not license_path.exists():
        shutil.rmtree(vendor_dir, ignore_errors=True)
        raise RuntimeError("LICENSE 文件缺失，已清理目录")
    license_text = license_path.read_text(encoding="utf-8", errors="ignore")
    if LICENSE_MARKER not in license_text:
        shutil.rmtree(vendor_dir, ignore_errors=True)
        raise RuntimeError(f"LICENSE 校验失败（未见 {LICENSE_MARKER}），已清理目录")
    commit = _run_git(["rev-parse", "HEAD"], vendor_dir)
    shutil.rmtree(vendor_dir / ".git", ignore_errors=True)
    (vendor_dir / "MANIFEST.json").write_text(
        json.dumps(
            {
                "source": "JavaGuide",
                "url": used_url,
                "commit": commit,
                "fetched_at": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return FetchReport(vendor_dir=vendor_dir, url=used_url, commit=commit)
