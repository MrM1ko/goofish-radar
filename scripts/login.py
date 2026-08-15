"""手动重新登录脚本（设计文档 scripts/login.py）。

用法：
    python scripts/login.py

流程：打开有头浏览器 → 扫码登录 → 登录成功后保存会话到
data/storage_state.json，之后 run.py 可直接复用，无需再扫码。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from browser.session import Session  # noqa: E402
from core.config import DATA_DIR  # noqa: E402


def main() -> int:
    session = Session(storage_path=DATA_DIR / "storage_state.json", headless=False)
    session.start()
    try:
        if session._login_via_qrcode():
            print(f"登录成功，会话已保存到 {session.storage_path}")
            return 0
        print("登录超时，请重试")
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
