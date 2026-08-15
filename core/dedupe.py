"""全局 item_id 去重。

seen.json 以 item_id 为键，记录每个已见商品的信息。
不同关键词命中同一商品时只处理一次（全局去重，而不是按关键词各自去重）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from core.models import Product, SeenRecord

logger = logging.getLogger(__name__)


class DedupeStore:
    """seen.json 的读写封装。

    线程安全由调用方（单线程主循环）保证，这里不做额外加锁。
    """

    def __init__(self, path: Path):
        self.path = path
        self._records: dict[str, SeenRecord] = {}
        if path.exists():
            self._load()

    # ------------------------------------------------------------- 查询

    def is_new(self, item_id: str) -> bool:
        """商品是否首次出现（唯一依据是 item_id）。"""
        return item_id not in self._records

    def is_empty(self) -> bool:
        """首次运行判断：seen 为空说明还没有基线。"""
        return not self._records

    def record(self, item_id: str) -> SeenRecord | None:
        return self._records.get(item_id)

    def __len__(self) -> int:
        return len(self._records)

    # ------------------------------------------------------------- 更新

    def mark_seen(self, product: Product) -> None:
        """记录一个商品为已见。

        已存在的商品只更新 last_seen / last_price 等字段，
        保留 first_seen（第一次发现时间）。
        """
        now = datetime.now().isoformat(timespec="seconds")
        record = self._records.get(product.item_id)
        if record is None:
            self._records[product.item_id] = SeenRecord.now(product)
        else:
            record.last_seen = now
            record.last_price = product.price
            record.title = product.title
            record.matched_keywords = sorted(
                set(record.matched_keywords) | set(product.matched_keywords)
            )

    def save(self) -> None:
        """原子写盘：先写临时文件再替换，避免中途崩溃损坏文件。"""
        payload = {
            item_id: {
                "first_seen": r.first_seen,
                "last_seen": r.last_seen,
                "last_price": r.last_price,
                "title": r.title,
                "matched_keywords": r.matched_keywords,
            }
            for item_id, r in self._records.items()
        }
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.path)
        logger.debug("seen.json 已保存，共 %d 条记录", len(self._records))

    # ------------------------------------------------------------- 内部

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("seen.json 读取失败，视为空基线: %s", e)
            return
        if not isinstance(raw, dict):
            logger.warning("seen.json 结构异常，视为空基线")
            return
        for item_id, data in raw.items():
            if not isinstance(data, dict):
                continue
            self._records[item_id] = SeenRecord(
                first_seen=str(data.get("first_seen", "")),
                last_seen=str(data.get("last_seen", "")),
                last_price=float(data.get("last_price", 0)),
                title=str(data.get("title", "")),
                matched_keywords=[
                    str(k) for k in data.get("matched_keywords", [])
                ],
            )
