"""L3: 简历文件上传端点 + 带 JD 的 resume 消息。

依据 specs/02-in-progress/api/resume-upload-jd.md 的 AC2、AC3（API 侧）。
"""

from __future__ import annotations

from tests.api.test_web_api import (
    ANALYZE_JSON,
    EXTRACT_JSON,
    _create,
    _model,
    _send,
    auth_headers,
    make_client,
)
from tests.conftest import StubLLM
from tests.unit.test_parsers import _bare_docx, _minimal_pdf

MIME = "application/octet-stream"


def test_upload_docx_extracts_chinese_text(tmp_path, registry):
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    session_id = _create(client, "resume")
    data = _bare_docx("张三", "3 年后端开发，Java 技术栈")
    response = client.post(
        f"/api/sessions/{session_id}/files",
        files={"file": ("张三的简历.docx", data, MIME)},
        headers=auth_headers(client),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["filename"] == "张三的简历.docx"
    assert "张三" in body["text"]
    assert "3 年后端开发" in body["text"]


def test_upload_pdf_extracts_text(tmp_path, registry):
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    session_id = _create(client, "resume")
    response = client.post(
        f"/api/sessions/{session_id}/files",
        files={"file": ("resume.pdf", _minimal_pdf("Hello OfferCoach"), MIME)},
        headers=auth_headers(client),
    )
    assert response.status_code == 200
    assert "Hello OfferCoach" in response.json()["text"]


def test_upload_unknown_session_404(tmp_path, registry):
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    response = client.post(
        "/api/sessions/nope/files",
        files={"file": ("resume.docx", _bare_docx("张三"), MIME)},
        headers=auth_headers(client),
    )
    assert response.status_code == 404


def test_upload_bad_extension_400(tmp_path, registry):
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    session_id = _create(client, "resume")
    response = client.post(
        f"/api/sessions/{session_id}/files",
        files={"file": ("resume.txt", b"plain text", MIME)},
        headers=auth_headers(client),
    )
    assert response.status_code == 400
    assert "仅支持" in response.json()["detail"]


def test_upload_too_large_413(tmp_path, registry):
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    session_id = _create(client, "resume")
    response = client.post(
        f"/api/sessions/{session_id}/files",
        files={"file": ("big.docx", b"x" * (5 * 1024 * 1024 + 1), MIME)},
        headers=auth_headers(client),
    )
    assert response.status_code == 413


def test_upload_corrupt_docx_400(tmp_path, registry):
    client = make_client(tmp_path, registry, llm_classify=StubLLM([]), llm_strong=_model())
    session_id = _create(client, "resume")
    response = client.post(
        f"/api/sessions/{session_id}/files",
        files={"file": ("broken.docx", b"not a zip file", MIME)},
        headers=auth_headers(client),
    )
    assert response.status_code == 400
    assert "解析失败" in response.json()["detail"]


def test_resume_message_with_jd_text(tmp_path, registry):
    """AC3 API 侧：resume 会话消息可附 jd_text，管道照常产出报告。"""
    classify = StubLLM([])
    llm = _model(
        EXTRACT_JSON,
        ANALYZE_JSON,
        "报告：与 JD 匹配度较高。",
        EXTRACT_JSON,
        ANALYZE_JSON,
        "报告二：通用建议。",
    )
    client = make_client(tmp_path, registry, llm_classify=classify, llm_strong=llm)
    session_id = _create(client, "resume")
    response = client.post(
        f"/api/sessions/{session_id}/messages",
        json={"content": "李四，2 年 Java 后端", "jd_text": "JD：要求高并发经验"},
        headers=auth_headers(client),
    )
    assert response.status_code == 200
    assert "报告" in response.json()["reply"]
    assert classify.calls == []
    # 无 JD 的同会话第二轮不受影响（jd_text 仅单条消息生效）
    assert "报告二" in _send(client, session_id, "再来一次分析")
