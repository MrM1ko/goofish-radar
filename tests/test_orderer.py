"""orderer 单元测试：拍单决策链 + 防重复 + 限流。"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core.settings import BuyConfig, MonitorConfig
from core.models import DetailResult, FilterResult, OrderResult, Product
from core.orderer import Decision, OrderRecord, OrderStore, Orderer


def make_product(item_id="1", price=100.0) -> Product:
    return Product(item_id=item_id, title="t", price=price, url="u")


def make_monitor(**kwargs) -> MonitorConfig:
    defaults = dict(name="m", keyword="k", enabled=True, auto_order=True, max_price=200.0)
    defaults.update(kwargs)
    return MonitorConfig(**defaults)


def make_buy(**kwargs) -> BuyConfig:
    defaults = dict(enabled=True, daily_limit=3, order_interval_minutes=20)
    defaults.update(kwargs)
    return BuyConfig(**defaults)


PASSED = FilterResult(passed=True, reasons=["ok"])
REJECTED = FilterResult(passed=False, reasons=["bad"])


@pytest.fixture
def store(tmp_path):
    return OrderStore(tmp_path / "orders.json")


@pytest.fixture
def orderer(store):
    return Orderer(store, make_buy(), order_fn=lambda p, mp=None: OrderResult(status="success"))


def test_filter_rejected_skips(orderer):
    d = orderer.decide(make_product(), None, REJECTED, make_monitor())
    assert d.decision == Decision.SKIP


def test_auto_order_disabled_notifies(orderer):
    d = orderer.decide(make_product(), None, PASSED, make_monitor(auto_order=False))
    assert d.decision == Decision.NOTIFY_ONLY


def test_price_over_threshold_notifies(orderer):
    d = orderer.decide(make_product(price=500.0), None, PASSED, make_monitor(max_price=200.0))
    assert d.decision == Decision.NOTIFY_ONLY
    assert "阈值" in d.reason


def test_sku_product_notifies(orderer):
    detail = DetailResult(has_sku=True, sku_count=3)
    d = orderer.decide(make_product(), detail, PASSED, make_monitor())
    assert d.decision == Decision.NOTIFY_ONLY
    assert "多规格" in d.reason


def test_all_conditions_met_orders(orderer):
    d = orderer.decide(make_product(), DetailResult(), PASSED, make_monitor())
    assert d.decision == Decision.ORDER


def test_second_attempt_forbidden(orderer, store):
    """success/unknown/failed 三种状态都禁止重复拍（设计文档第 16 节）。"""
    product = make_product()
    for status in ("success", "unknown", "failed"):
        store.records.append(
            OrderRecord(
                item_id=product.item_id,
                timestamp=datetime.now().isoformat(),
                title="t",
                price=1.0,
                monitor="m",
                status=status,
            )
        )
        d = orderer.decide(product, DetailResult(), PASSED, make_monitor())
        assert d.decision == Decision.SKIP, f"status={status} 应禁止重拍"
        store.records.clear()


def test_daily_limit(orderer, store):
    today = datetime.now().isoformat(timespec="seconds")
    for i in range(3):
        store.records.append(
            OrderRecord(
                item_id=f"old{i}", timestamp=today, title="t", price=1.0,
                monitor="m", status="success",
            )
        )
    d = orderer.decide(make_product("new1"), DetailResult(), PASSED, make_monitor())
    assert d.decision == Decision.NOTIFY_ONLY
    assert "上限" in d.reason


def test_daily_limit_counts_today_only(orderer, store):
    """昨天/其他日期的成功订单不计入当天限制。"""
    yesterday = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    store.records.append(
        OrderRecord(
            item_id="old", timestamp=yesterday, title="t", price=1.0,
            monitor="m", status="success",
        )
    )
    d = orderer.decide(make_product("new1"), DetailResult(), PASSED, make_monitor())
    assert d.decision == Decision.ORDER


def test_order_interval(orderer, store):
    now = datetime.now().isoformat(timespec="seconds")
    store.records.append(
        OrderRecord(
            item_id="old", timestamp=now, title="t", price=1.0,
            monitor="m", status="success",
        )
    )
    d = orderer.decide(make_product("new1"), DetailResult(), PASSED, make_monitor())
    assert d.decision == Decision.NOTIFY_ONLY
    assert "间隔" in d.reason


def test_execute_records_result(orderer, store):
    product = make_product("x1")
    result = orderer.execute(product, make_monitor())
    assert result.status == "success"
    assert store.has_attempt("x1") is True
    assert store.records[-1].status == "success"


def test_execute_passes_max_price_to_order_fn(store):
    """下单函数应收到 monitor 阈值，用于订单确认页金额兜底校验。"""
    received = {}

    def fake_order_fn(product, max_price=None):
        received["max_price"] = max_price
        return OrderResult(status="success")

    orderer = Orderer(store, make_buy(), order_fn=fake_order_fn)
    orderer.execute(make_product("x3"), make_monitor(max_price=200.0))
    assert received["max_price"] == 200.0


def test_version_rule_price_limit(orderer):
    """版本规则：MacBook Air 主词，M2 版阈值 3500 / M4 版阈值 5500。"""
    from core.settings import VersionRule

    monitor = make_monitor(
        max_price=None,
        version_rules=[
            VersionRule(match_words=["m2"], max_price=3500.0, note="M2 版"),
            VersionRule(match_words=["m4"], max_price=5500.0, note="M4 版"),
        ],
    )
    # M4 版 ¥6000 超 M4 阈值 → 不拍
    p = make_product(price=6000.0)
    p.title = "MacBook Air M4 16+256"
    d = orderer.decide(p, DetailResult(), PASSED, monitor)
    assert d.decision == Decision.NOTIFY_ONLY
    assert "M4 版" in d.reason

    # M2 版 ¥3000 低于 M2 阈值 → 拍
    p2 = make_product(price=3000.0)
    p2.title = "MacBook Air M2 16+512"
    d2 = orderer.decide(p2, DetailResult(), PASSED, monitor)
    assert d2.decision == Decision.ORDER


def test_version_rule_fallback_to_monitor_max(orderer):
    """未命中任何版本规则 → 用 monitor.max_price 兜底。"""
    from core.settings import VersionRule

    monitor = make_monitor(
        max_price=4000.0,
        version_rules=[VersionRule(match_words=["m4"], max_price=5500.0, note="M4 版")],
    )
    p = make_product(price=4500.0)
    p.title = "MacBook Air M1 8+256"  # 未命中 m4 → 兜底 4000
    d = orderer.decide(p, DetailResult(), PASSED, monitor)
    assert d.decision == Decision.NOTIFY_ONLY
    assert "4000" in d.reason


def test_no_price_limit_notifies_only(orderer):
    """monitor 与版本规则都没有阈值 → 仅通知不拍。"""
    monitor = make_monitor(max_price=None)
    d = orderer.decide(make_product(), DetailResult(), PASSED, monitor)
    assert d.decision == Decision.NOTIFY_ONLY
    assert "阈值" in d.reason


def test_version_match_uses_desc(orderer):
    """版本词在描述中出现也算命中（标题没写版本时）。"""
    from core.settings import VersionRule

    monitor = make_monitor(
        max_price=6000.0,
        version_rules=[VersionRule(match_words=["m2"], max_price=3500.0, note="M2 版")],
    )
    p = make_product(price=3200.0)
    p.title = "MacBook Air 16+256"
    detail = DetailResult(desc="2023 款 M2 芯片")
    d = orderer.decide(p, detail, PASSED, monitor)
    assert d.decision == Decision.ORDER  # 命中 M2 规则，3200 < 3500


def test_execute_unknown_blocks_retry(orderer, store):
    """UNKNOWN 落盘后禁止再次自动拍（设计文档第 18 节）。"""
    product = make_product("x2")
    orderer.order_fn = lambda p, mp=None: OrderResult(status="unknown", reason="超时")
    orderer.execute(product, make_monitor())

    d = orderer.decide(product, DetailResult(), PASSED, make_monitor())
    assert d.decision == Decision.SKIP
