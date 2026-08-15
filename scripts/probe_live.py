"""常驻探测服务（避免反复开关浏览器触发风控）。

浏览器只启动一次并保持常驻，探测命令通过 JSONL 文件交互：

  命令：data/debug/live_cmd.jsonl   每行一个 JSON 对象
  结果：data/debug/live_result.jsonl 每行一个 JSON 对象

支持的命令：
  {"op": "goto", "url": "...", "wait_ms": 4000}   打开页面并等待
  {"op": "count", "css": "..."}                   输出命中数量
  {"op": "text", "css": "..."}                    输出首个元素文本
  {"op": "dump", "css": "...", "maxlen": 2000}    输出首个元素 outerHTML
  {"op": "snapshot", "name": "xxx"}               保存整页 HTML 快照
  {"op": "quit"}                                  退出

用法：
    python scripts/probe_live.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from browser.session import Session  # noqa: E402
from core.config import DATA_DIR  # noqa: E402

DEBUG_DIR = DATA_DIR / "debug"
CMD_FILE = DEBUG_DIR / "live_cmd.jsonl"
RESULT_FILE = DEBUG_DIR / "live_result.jsonl"

POLL_SECONDS = 1.0


def handle(page, cmd: dict) -> dict:
    op = cmd.get("op")
    try:
        if op == "goto":
            page.goto(cmd["url"], wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(int(cmd.get("wait_ms", 4000)))
            return {"ok": True, "url": page.url, "title": page.title()}
        if op == "count":
            return {"ok": True, "n": page.locator(cmd["css"]).count()}
        if op == "text":
            loc = page.locator(cmd["css"]).first
            return {"ok": True, "text": loc.inner_text()[:800]}
        if op == "dump":
            maxlen = int(cmd.get("maxlen", 2000))
            loc = page.locator(cmd["css"]).first
            if loc.count() > 0:
                html = loc.evaluate(f"el => el.outerHTML.slice(0, {maxlen})")
            else:
                html = "NOT_FOUND"
            return {"ok": True, "html": html}
        if op == "snapshot":
            name = str(cmd.get("name", "live"))
            (DEBUG_DIR / f"{name}.html").write_text(page.content(), encoding="utf-8")
            return {"ok": True, "saved": name}
        if op == "read_detail":
            # 复用 detail.py 真实代码路径验证提取结果
            from browser.detail import DetailReader
            from browser.selectors import Selectors

            reader = DetailReader(page, Selectors())
            result = reader.read(cmd["url"])
            return {
                "ok": True,
                "detail": {
                    "title": result.title,
                    "price": result.price,
                    "postage": result.postage,
                    "desc_head": (result.desc or "")[:200],
                    "has_sku": result.has_sku,
                    "sku_count": result.sku_count,
                    "status": result.status,
                    "failed": result.failed,
                    "error": result.error,
                },
            }
        if op == "quit":
            return {"ok": True, "quit": True}
        return {"ok": False, "error": f"unknown op: {op!r}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def main() -> None:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    session = Session(storage_path=DATA_DIR / "storage_state.json", headless=False)
    session.start()
    page = session.page
    print("LIVE_READY", flush=True)

    processed = 0
    try:
        while True:
            if not CMD_FILE.exists():
                time.sleep(POLL_SECONDS)
                continue
            lines = CMD_FILE.read_text(encoding="utf-8").splitlines()
            new_lines = lines[processed:]
            processed = len(lines)
            for line in new_lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    cmd = json.loads(line)
                except json.JSONDecodeError:
                    continue
                result = handle(page, cmd)
                with RESULT_FILE.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(result, ensure_ascii=False) + "\n")
                if result.get("quit"):
                    print("LIVE_QUIT", flush=True)
                    return
            time.sleep(POLL_SECONDS)
    finally:
        session.close()


if __name__ == "__main__":
    main()
