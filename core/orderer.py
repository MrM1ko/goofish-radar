"""拍单决策 + 限流 + 订单记录。

决策链（设计文档第 15 节，全部满足才执行下单）：
  过滤通过 → monitor.auto_order → price <= max_price → 无多规格
  → 无历史订单尝试 → 当日成功 < daily_limit → 距上单 >= order_interval
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

from core.config import BuyConfig, MonitorConfig
from core.models import DetailResult, FilterResult, OrderResult, Product

logger = logging.getLogger(__name__)


class Decision(Enum):
    ORDER = "order"              # 满足全部条件，执行自动拍单
    NOTIFY_ONLY = "notify_only"  # 通过过滤但不满足拍单条件，仅通知
    SKIP = "skip"                # 不需要通知（如买拍功能关闭且非新商品关注点）


@dataclass
class OrderDecision:
    decision: Decision
    reason: str = ""


@dataclass
class OrderRecord:
    item_id: str
    timestamp: str
    title: str
    price: float
    monitor: str
    status: str          # success / failed / unknown
    order_id: str | None = None
    reason: str | None = None


class OrderStore:
    """orders.json 的读写封装。"""

    def __init__(self, path: Path):
        self.path = path
        self.records: list[OrderRecord] = []
        if path.exists():
            self._load()

    # ------------------------------------------------------------- 查询

    def has_attempt(self, item_id: str) -> bool:
        """该 item_id 是否已有过任何下单尝试。

        设计文档第 16 节：success / unknown 禁止重拍；failed 也不自动重试。
        因此只要存在记录即禁止再次自动提交。
        """
        return any(r.item_id == item_id for r in self.records)

    def count_success_today(self) -> int:
        """今天（本地日期）成功的订单数，用于 daily_limit。"""
        today = datetime.now().date()
        count = 0
        for r in self.records:
            if r.status != "success":
                continue
            try:
                ts = datetime.fromisoformat(r.timestamp)
            except ValueError:
                continue
            if ts.date() == today:
                count += 1
        return count

    def last_order_time(self) -> datetime | None:
        """最近一次下单尝试的时间（任意状态），用于 order_interval。"""
        latest: datetime | None = None
        for r in self.records:
            try:
                ts = datetime.fromisoformat(r.timestamp)
            except ValueError:
                continue
            if latest is None or ts > latest:
                latest = ts
        return latest

    # ------------------------------------------------------------- 写入

    def record(self, record: OrderRecord) -> None:
        self.records.append(record)
        self._save()

    def _save(self) -> None:
        payload = [
            {
                "item_id": r.item_id,
                "timestamp": r.timestamp,
                "title": r.title,
                "price": r.price,
                "monitor": r.monitor,
                "status": r.status,
                "order_id": r.order_id,
                "reason": r.reason,
            }
            for r in self.records
        ]
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("orders.json 读取失败: %s", e)
            return
        if not isinstance(raw, list):
            return
        for item in raw:
            if not isinstance(item, dict) or "item_id" not in item:
                continue
            self.records.append(
                OrderRecord(
                    item_id=str(item["item_id"]),
                    timestamp=str(item.get("timestamp", "")),
                    title=str(item.get("title", "")),
                    price=float(item.get("price", 0)),
                    monitor=str(item.get("monitor", "")),
                    status=str(item.get("status", "unknown")),
                    order_id=item.get("order_id"),
                    reason=item.get("reason"),
                )
            )


class Orderer:
    """拍单决策与执行。

    order_fn 通过构造参数注入（真实实现是 browser.order 的函数），
    纯逻辑单测时注入假函数即可完全离线测试。
    """

    def __init__(self, store: OrderStore, buy: BuyConfig, order_fn):
        self.store = store
        self.buy = buy
        self.order_fn = order_fn

    # ------------------------------------------------------------- 决策

    def decide(
        self,
        product: Product,
        detail: DetailResult | None,
        filter_result: FilterResult,
        monitor: MonitorConfig,
    ) -> OrderDecision:
        """判断该商品是否执行自动拍单，不满足时给出理由。"""
        if not filter_result.passed:
            return OrderDecision(Decision.SKIP, "过滤未通过")
        if not monitor.auto_order:
            return OrderDecision(Decision.NOTIFY_ONLY, "该 monitor 未开启 auto_order")
        if monitor.max_price is not None and product.price > monitor.max_price:
            return OrderDecision(
                Decision.NOTIFY_ONLY,
                f"价格 {product.price} 超过阈值 {monitor.max_price}",
            )
        if detail is not None and detail.has_sku:
            return OrderDecision(Decision.NOTIFY_ONLY, "多规格商品，不自动拍")
        if self.store.has_attempt(product.item_id):
            return OrderDecision(Decision.SKIP, "该商品已有下单尝试，禁止重复拍")
        if not self.buy.enabled:
            return OrderDecision(Decision.NOTIFY_ONLY, "全局买拍功能未开启")
        if self.buy.daily_limit > 0 and self.store.count_success_today() >= self.buy.daily_limit:
            return OrderDecision(Decision.NOTIFY_ONLY, f"已达当日拍单上限 {self.buy.daily_limit}")
        last = self.store.last_order_time()
        if last is not None and self.buy.order_interval_minutes > 0:
            if datetime.now() < last + timedelta(minutes=self.buy.order_interval_minutes):
                return OrderDecision(Decision.NOTIFY_ONLY, "距上一单未满拍单间隔")
        return OrderDecision(Decision.ORDER, "全部条件满足")

    # ------------------------------------------------------------- 执行

    def execute(self, product: Product, monitor: MonitorConfig) -> OrderResult:
        """调用注入的下单函数并把结果落盘。

        把 monitor.max_price 一并传入下单函数，作为订单确认页
        "合计金额（含运费）"的最后一道兜底校验。

        结果一经产生（success/failed/unknown）就写入 orders.json，
        之后任何状态都不会再次自动拍同一商品。
        """
        logger.info("开始下单: %s (%s) ¥%s", product.title, product.item_id, product.price)
        result: OrderResult = self.order_fn(product, monitor.max_price)
        self.store.record(
            OrderRecord(
                item_id=product.item_id,
                timestamp=datetime.now().isoformat(timespec="seconds"),
                title=product.title,
                price=product.price,
                monitor=monitor.name,
                status=result.status,
                order_id=result.order_id,
                reason=result.reason,
            )
        )
        return result
