"""rule_filter 单元测试：损坏/引流 + 文本标准化 + 否定判断。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core.config import MonitorConfig
from core.filter.rule_filter import RuleFilter
from core.models import DetailResult, Product


def make_product(title: str, desc: str | None = None) -> Product:
    return Product(item_id="1", title=title, price=100.0, url="https://x", desc=desc)


def make_monitor() -> MonitorConfig:
    return MonitorConfig(name="t", keyword="k", enabled=True, auto_order=False)


@pytest.fixture
def rule_filter(tmp_path):
    negative = tmp_path / "negative.txt"
    negative.write_text("磕碰\n划痕\n维修过\n屏幕碎\n", encoding="utf-8")
    traction = tmp_path / "traction.txt"
    traction.write_text("加微信\n加微\n货到付款\n", encoding="utf-8")
    return RuleFilter(negative_file=negative, traction_file=traction)


def test_clean_product_passes(rule_filter):
    result = rule_filter.check(
        make_product("自用显卡，成色很好", "箱说全"),
        DetailResult(desc="箱说全"),
        make_monitor(),
    )
    assert result.passed is True


def test_damage_rejected(rule_filter):
    result = rule_filter.check(make_product("显卡 有划痕"), None, make_monitor())
    assert result.passed is False
    assert "划痕" in result.reasons[0]


def test_traction_rejected(rule_filter):
    result = rule_filter.check(make_product("低价显卡 加微信聊"), None, make_monitor())
    assert result.passed is False
    assert "加微信" in result.reasons[0]


def test_negation_not_rejected(rule_filter):
    # "无划痕" 不应命中损坏词
    result = rule_filter.check(make_product("显卡 无划痕 无磕碰"), None, make_monitor())
    assert result.passed is True


def test_fullwidth_normalized(rule_filter):
    # 全角 "ＶＸ" 标准化后应命中 "vx" 类引流词？这里验证全角数字字母标准化本身
    result = rule_filter.check(make_product("显卡 Ｖｘ 加我"), None, make_monitor())
    # 标准化后 "Ｖｘ" → "vx"，但当前词表没有 vx，验证不崩溃且逻辑正常
    assert result.passed is True


def test_traction_variant_whitespace(rule_filter):
    # "加 微 信" 标准化后（去空格）应为 "加微信" → 命中引流词
    result = rule_filter.check(make_product("显卡 加 微 信"), None, make_monitor())
    assert result.passed is False
    assert "加微信" in result.reasons[0]


def test_desc_checked(rule_filter):
    result = rule_filter.check(
        make_product("显卡"),
        DetailResult(desc="有磕碰，介意勿拍"),
        make_monitor(),
    )
    assert result.passed is False
