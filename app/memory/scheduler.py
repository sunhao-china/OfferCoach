"""T1/T2 定时任务（APScheduler 进程内）。

T1 每 10 分钟：idle 会话兜底总结（service.sweep_t1）。
T2 每天 03:00：遗忘 + 晋升扫描（service.sweep_t2）。
任务异常不抛出（记忆层 fail-open），由 service 记录。
"""

from __future__ import annotations

from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler

JOB_T1 = "memory_t1"
JOB_T2 = "memory_t2"


class MemoryScheduler:
    def __init__(
        self,
        service: Any,
        *,
        t1_interval_minutes: int = 10,
        t2_hour: int = 3,
        t2_minute: int = 0,
    ) -> None:
        self._service = service
        self._scheduler = BackgroundScheduler()
        self._scheduler.add_job(
            self._run_t1,
            "interval",
            minutes=t1_interval_minutes,
            id=JOB_T1,
            coalesce=True,
            max_instances=1,
        )
        self._scheduler.add_job(
            self._run_t2,
            "cron",
            hour=t2_hour,
            minute=t2_minute,
            id=JOB_T2,
            coalesce=True,
            max_instances=1,
        )

    def _run_t1(self) -> None:
        try:
            self._service.sweep_t1()
        except Exception as exc:
            self._service.record_error(f"T1: {exc}")

    def _run_t2(self) -> None:
        try:
            self._service.sweep_t2()
        except Exception as exc:
            self._service.record_error(f"T2: {exc}")

    def start(self) -> None:
        self._scheduler.start()

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)

    def trigger_t1(self) -> None:
        """手工触发 T1（服务器冒烟验证用）。"""
        self._scheduler.get_job(JOB_T1).func()

    def trigger_t2(self) -> None:
        """手工触发 T2（服务器冒烟验证用）。"""
        self._scheduler.get_job(JOB_T2).func()
