"""L1: JavaGuide 抓取 — 源回退 / MANIFEST / LICENSE 校验 / 目录清理。

依据 specs/02-in-progress/question_bank/java-guide-import.md 的 AC1。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import app.question_bank.fetcher as fetcher_module
from app.question_bank.fetcher import fetch_java_guide


def _install_fake_git(monkeypatch, *, license_text="Apache License Version 2.0", fail_urls=()):
    """装桩 _run_git：clone 时真实造目录（LICENSE/.git），rev-parse 返回固定 commit。"""
    calls: list[list[str]] = []

    def fake_git(args: list[str], cwd: Path) -> str:
        calls.append(list(args))
        if args[:2] == ["clone", "--depth"]:
            url, target = args[3], Path(args[4])
            if url in fail_urls:
                raise subprocess.CalledProcessError(1, ["git", *args], stderr="boom")
            target.mkdir(parents=True)
            if license_text is not None:
                (target / "LICENSE").write_text(license_text, encoding="utf-8")
            (target / ".git").mkdir()
            return ""
        if args == ["rev-parse", "HEAD"]:
            return "c0ffee42"
        raise AssertionError(f"unexpected git args: {args}")

    monkeypatch.setattr(fetcher_module, "_run_git", fake_git)
    return calls


def _clone_urls(calls: list[list[str]]) -> list[str]:
    return [args[3] for args in calls if args[:2] == ["clone", "--depth"]]


def test_fetch_prefers_gitee_and_writes_manifest(tmp_path, monkeypatch):
    _install_fake_git(monkeypatch)
    vendor = tmp_path / "java_guide"
    report = fetch_java_guide(vendor)
    assert report.vendor_dir == vendor
    assert report.url == fetcher_module.PRIMARY_URL  # 首选源成功不回退
    assert report.commit == "c0ffee42"
    manifest = json.loads((vendor / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["source"] == "JavaGuide"
    assert manifest["url"] == fetcher_module.PRIMARY_URL
    assert manifest["commit"] == "c0ffee42"
    assert "fetched_at" in manifest
    assert not (vendor / ".git").exists()  # 克隆元数据不落盘
    assert (vendor / "LICENSE").exists()


def test_fallback_to_github_when_gitee_fails(tmp_path, monkeypatch):
    _install_fake_git(monkeypatch, fail_urls=(fetcher_module.PRIMARY_URL,))
    vendor = tmp_path / "java_guide"
    report = fetch_java_guide(vendor)
    assert report.url == fetcher_module.FALLBACK_URL
    assert (vendor / "MANIFEST.json").exists()


def test_both_sources_fail_cleans_dir(tmp_path, monkeypatch):
    calls = _install_fake_git(
        monkeypatch, fail_urls=(fetcher_module.PRIMARY_URL, fetcher_module.FALLBACK_URL)
    )
    vendor = tmp_path / "java_guide"
    with pytest.raises(RuntimeError, match="克隆失败"):
        fetch_java_guide(vendor)
    assert _clone_urls(calls) == [fetcher_module.PRIMARY_URL, fetcher_module.FALLBACK_URL]
    assert not vendor.exists()


def test_license_without_marker_rejected_and_cleaned(tmp_path, monkeypatch):
    _install_fake_git(monkeypatch, license_text="MIT License")
    vendor = tmp_path / "java_guide"
    with pytest.raises(RuntimeError, match="LICENSE 校验失败"):
        fetch_java_guide(vendor)
    assert not vendor.exists()


def test_missing_license_file_rejected_and_cleaned(tmp_path, monkeypatch):
    _install_fake_git(monkeypatch, license_text=None)
    vendor = tmp_path / "java_guide"
    with pytest.raises(RuntimeError, match="LICENSE 文件缺失"):
        fetch_java_guide(vendor)
    assert not vendor.exists()


def test_existing_dir_refused(tmp_path, monkeypatch):
    _install_fake_git(monkeypatch)
    vendor = tmp_path / "java_guide"
    vendor.mkdir(parents=True)
    with pytest.raises(RuntimeError, match="已存在"):
        fetch_java_guide(vendor)
