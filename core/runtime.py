"""运行时状态：风控暂停持久化 + 单实例锁。

- runtime_state.json 记录 paused_until / pause_reason，
  保证程序重启、崩溃、Windows 重启后都不会绕过原定的暂停时间。
- run.lock 用文件锁防止同时运行两个实例（如启动项 + 手动各启动一份），
  并写入 PID，残留锁（进程已死）自动视为失效，不会卡死后续启动。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


class RuntimeState:
    """runtime_state.json 的读写封装。"""

    def __init__(self, path: Path):
        self.path = path
        self.paused_until: str | None = None
        self.pause_reason: str | None = None
        if path.exists():
            self._load()

    def is_paused(self) -> bool:
        """当前是否处于风控暂停期。"""
        if not self.paused_until:
            return False
        try:
            until = datetime.fromisoformat(self.paused_until)
        except ValueError:
            logger.warning("paused_until 时间格式非法: %r，视为未暂停", self.paused_until)
            return False
        if datetime.now() >= until:
            logger.info("暂停期已结束（%s），恢复运行", self.paused_until)
            self.clear()
            return False
        return True

    def pause(self, minutes: int, reason: str) -> None:
        """进入暂停：立即写盘，崩溃重启也仍然生效。"""
        until = datetime.now() + timedelta(minutes=minutes)
        self.paused_until = until.isoformat(timespec="seconds")
        self.pause_reason = reason
        self._save()
        logger.warning("进入风控暂停：%s，直到 %s", reason, self.paused_until)

    def clear(self) -> None:
        self.paused_until = None
        self.pause_reason = None
        self._save()

    def _save(self) -> None:
        payload = {
            "paused_until": self.paused_until,
            "pause_reason": self.pause_reason,
        }
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("runtime_state.json 读取失败: %s", e)
            return
        self.paused_until = raw.get("paused_until")
        self.pause_reason = raw.get("pause_reason")


class SingleInstanceLock:
    """基于文件的单实例锁。

    锁文件里写当前进程 PID；启动时若锁已存在：
      - 该 PID 的进程还活着 → 已有实例在跑，拒绝启动；
      - 进程已死 → 残留锁，自动接管（覆盖 PID）。
    退出时释放锁。Windows 下无法可靠检测进程名，仅以 PID 存活为准。
    """

    def __init__(self, path: Path):
        self.path = path
        self._acquired = False

    def acquire(self) -> bool:
        """尝试获取锁。返回 False 表示已有实例在运行。"""
        if self.path.exists():
            try:
                pid = int(self.path.read_text(encoding="utf-8").strip())
            except (ValueError, OSError):
                pid = -1
            if pid > 0 and _pid_alive(pid):
                logger.error("检测到已有实例在运行（PID %d），本次启动退出", pid)
                return False
            logger.warning("发现残留锁（PID %d 已不存在），自动接管", pid)
        self.path.write_text(str(os.getpid()), encoding="utf-8")
        self._acquired = True
        return True

    def release(self) -> None:
        if self._acquired:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                logger.warning("释放锁文件失败: %s", self.path)
            self._acquired = False


def _pid_alive(pid: int) -> bool:
    """判断进程是否存活。Windows: 打开进程句柄；POSIX: kill(pid, 0)。"""
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        # ERROR_INVALID_PARAMETER(87) 表示 PID 不存在；其他错误保守视为存活
        return ctypes.windll.kernel32.GetLastError() != 87
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
