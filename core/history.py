"""历史记录：history.jsonl 追加式审计日志。

每行一个 JSON 事件，追加写入（append-only），
主要用于调试和事后审计"这个商品当时发生了什么"。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class HistoryStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: str, **fields) -> None:
        """追加一条事件。

        例：append("discovered", item_id="123", price=1999, title="...")
        """
        record = {"timestamp": datetime.now().isoformat(timespec="seconds"), "event": event}
        record.update(fields)
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            # 历史记录失败不影响主流程
            logger.warning("history.jsonl 写入失败: %s", e)
