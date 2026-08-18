"""手动重新登录脚本（设计文档 scripts/login.py）。

用法：
    python scripts/login.py

流程：
  - 若 account.json 的 login 段配置了账号密码（enabled=true），
    优先使用账号密码自动登录，失败自动回退扫码登录；
  - 否则直接扫码登录。
登录成功后保存会话到 data/storage_state.json，之后 run.py 可直接复用。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from browser.session import Session  # noqa: E402
from core.settings import DATA_DIR, load_config  # noqa: E402


def main() -> int:
    cfg = load_config()
    session = Session(
        storage_path=DATA_DIR / "storage_state.json",
        headless=False,
        login=cfg.login,
    )
    session.start()
    try:
        if cfg.login.enabled:
            print(f"使用账号密码登录（{cfg.login.username[:3]}****）…")
        if session.re_login():
            print(f"登录成功，会话已保存到 {session.storage_path}")
            return 0
        print("登录失败/超时，请重试")
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
