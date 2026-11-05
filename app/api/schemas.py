"""API 层请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

BusinessType = Literal["chat", "interview", "resume", "review"]
DifficultyType = Literal["entry", "medium", "hard"]


class SessionCreate(BaseModel):
    business: BusinessType
    # user_id 已废弃：归属一律取登录 token 身份（多余字段被 pydantic 忽略，旧前端兼容）
    target_session_id: str | None = None  # review 会话必填：要复盘的面试会话
    difficulty: DifficultyType | None = None  # 仅 interview 生效：一轮面试一个难度
    position: str | None = None  # 仅 interview 生效：目标岗位（题库无匹配时子图回退默认）


class LoginIn(BaseModel):
    """登录：密码策略宽松（老账号兼容——不能因加固锁死既有用户）。"""

    username: str = Field(min_length=3, max_length=20)
    password: str = Field(min_length=6)
    captcha_id: str | None = None  # 触发验证码时提供；未触发可省略
    captcha_code: str | None = None


class RegisterIn(BaseModel):
    """注册：验证码必填 + 密码复杂度（8-20 位且同时含字母和数字）。"""

    username: str = Field(min_length=3, max_length=20)
    password: str = Field(min_length=8, max_length=20)
    captcha_id: str
    captcha_code: str

    @field_validator("password")
    @classmethod
    def _password_complexity(cls, value: str) -> str:
        if not any(char.isalpha() for char in value) or not any(char.isdigit() for char in value):
            raise ValueError("密码需 8-20 位且同时包含字母和数字")
        return value


class CaptchaOut(BaseModel):
    captcha_id: str
    image: str  # SVG 字符串，前端转 data URL 展示


class AuthOut(BaseModel):
    token: str
    username: str


class LogoutOut(BaseModel):
    ok: bool


class SessionOut(BaseModel):
    session_id: str
    business: BusinessType
    user_id: str
    target_session_id: str | None = None
    difficulty: DifficultyType | None = None
    position: str | None = None
    created_at: datetime


class MessageIn(BaseModel):
    content: str = Field(min_length=1)
    jd_text: str | None = None  # 仅 resume 会话生效：目标岗位 JD 对照
    stream: bool = False  # true 时返回 text/event-stream（终稿分块推送）


class MessageOut(BaseModel):
    session_id: str
    reply: str


class UploadOut(BaseModel):
    session_id: str
    filename: str
    text: str


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class HistoryOut(BaseModel):
    session_id: str
    messages: list[HistoryMessage]


class SessionListOut(BaseModel):
    sessions: list[SessionOut]


class HistoryRecordOut(BaseModel):
    """历史回顾页记录：会话元数据 + 派生完成态/预览（零新增存储）。"""

    session_id: str
    business: BusinessType
    created_at: datetime
    difficulty: DifficultyType | None = None
    position: str | None = None
    target_session_id: str | None = None
    completed: bool
    preview: str  # 报告开头截断 120 字；未完成为空


class HistoryListOut(BaseModel):
    records: list[HistoryRecordOut]


class SessionDeleteOut(BaseModel):
    deleted: bool


# ---------- 练习计划 ----------

DrillDifficultyType = Literal["entry", "medium", "hard", "mixed"]


class DrillPlanIn(BaseModel):
    """练习计划保存：position 空/"全部" = 全岗位；daily_count 1-20。"""

    position: str | None = None
    difficulty: DrillDifficultyType
    daily_count: int = Field(default=5, ge=1, le=20)


class DrillPlanOut(BaseModel):
    position: str | None = None
    difficulty: str
    daily_count: int


class DrillQuestionOut(BaseModel):
    """未答题（已答题不给题面——进度由 done/total 表达，参考要点在判分响应）。"""

    question_id: str
    position: str
    category: str
    difficulty: str
    question: str


class DrillTodayOut(BaseModel):
    plan_date: str
    total: int
    done: int
    completed: bool
    shortage: bool
    remaining: list[DrillQuestionOut]


class DrillAnswerIn(BaseModel):
    question_id: str
    answer: str


class DrillAnswerOut(BaseModel):
    score: dict  # {correctness, depth, expression} 1-5
    feedback: str
    strengths: list[str]
    weaknesses: list[str]
    reference_points: list[str]
    completed: bool
    progress: dict  # {done, total}


class DrillProgressOut(BaseModel):
    plan: DrillPlanOut | None = None
    today: dict  # {total, done, completed, shortage}
    total_attempts: int
    total_completed_sets: int
    streak: int


class DrillReclassifyIn(BaseModel):
    """错题纠正：提交题单中的题目 ID，AI 重新识别岗位与类别。"""

    question_id: str


class DrillCategoryOut(BaseModel):
    position: str
    category: str


class DrillReclassifyOut(BaseModel):
    question_id: str
    position: str
    category: str
    changed: bool
    previous: DrillCategoryOut
