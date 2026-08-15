"""runtime 单元测试：暂停状态持久化 + 单实例锁。"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.runtime import RuntimeState, SingleInstanceLock


def test_pause_persists_across_restart(tmp_path):
    path = tmp_path / "runtime_state.json"
    state = RuntimeState(path)
    state.pause(30, "captcha")
    assert state.is_paused() is True

    # 模拟重启：重新加载文件
    state2 = RuntimeState(path)
    assert state2.is_paused() is True
    assert state2.pause_reason == "captcha"


def test_pause_expired_clears(tmp_path):
    path = tmp_path / "runtime_state.json"
    state = RuntimeState(path)
    state.paused_until = (datetime.now() - timedelta(minutes=1)).isoformat()
    state._save()
    assert state.is_paused() is False


def test_pause_invalid_timestamp_not_paused(tmp_path):
    path = tmp_path / "runtime_state.json"
    path.write_text('{"paused_until": "not-a-time", "pause_reason": "x"}', encoding="utf-8")
    state = RuntimeState(path)
    assert state.is_paused() is False


def test_lock_acquire_and_release(tmp_path):
    path = tmp_path / "run.lock"
    lock = SingleInstanceLock(path)
    assert lock.acquire() is True
    assert path.exists()

    # 同一进程（模拟同机第二个实例）应被拒绝
    lock2 = SingleInstanceLock(path)
    assert lock2.acquire() is False

    lock.release()
    assert not path.exists()


def test_stale_lock_auto_taken(tmp_path):
    path = tmp_path / "run.lock"
    # 写入一个不可能存活的 PID
    path.write_text("999999999", encoding="utf-8")
    lock = SingleInstanceLock(path)
    assert lock.acquire() is True
    # 接管后锁文件写入的是当前进程 PID
    assert int(path.read_text(encoding="utf-8")) == os.getpid()
    lock.release()
