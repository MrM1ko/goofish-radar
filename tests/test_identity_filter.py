"""identity_filter 单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core.config import MonitorConfig
from core.filter.identity_filter import IdentityFilter
from core.models import DetailResult, Product


def make_product(title: str, desc: str | None = None) -> Product:
    return Product(item_id="1", title=title, price=100.0, url="https://x", desc=desc)


def make_monitor(**kwargs) -> MonitorConfig:
    defaults = dict(name="t", keyword="RTX 4070", enabled=True, auto_order=False)
    defaults.update(kwargs)
    return MonitorConfig(**defaults)


@pytest.fixture
def identity_filter(tmp_path):
    words = tmp_path / "invalid.txt"
    words.write_text(
        "# 注释\n空盒\n维修\n求购\nregex:租[赁借]\n", encoding="utf-8"
    )
    return IdentityFilter(words_file=words)


def test_normal_product_passes(identity_filter):
    result = identity_filter.check(
        make_product("RTX 4070 显卡 自用无拆修", "正常描述"),
        DetailResult(desc="正常描述"),
        make_monitor(),
    )
    assert result.passed is True


def test_empty_box_rejected(identity_filter):
    result = identity_filter.check(
        make_product("RTX 4070 空盒 收藏用"),
        None,
        make_monitor(),
    )
    assert result.passed is False
    assert "空盒" in result.reasons[0]


def test_regex_word_rejected(identity_filter):
    result = identity_filter.check(
        make_product("RTX 4070 租赁"),
        None,
        make_monitor(),
    )
    assert result.passed is False
    assert "regex" in result.reasons[0]


def test_monitor_exclude_words_merged(identity_filter):
    monitor = make_monitor(exclude_words=["散热器"])
    result = identity_filter.check(
        make_product("RTX 4070 散热器 改装"),
        None,
        monitor,
    )
    assert result.passed is False
    assert "散热器" in result.reasons[0]


def test_detail_desc_is_checked(identity_filter):
    result = identity_filter.check(
        make_product("RTX 4070 显卡"),
        DetailResult(desc="注意：只卖空盒"),
        make_monitor(),
    )
    assert result.passed is False


def test_blind_box_traction_rejected(tmp_path):
    """2026-08 实测：'福利疯抢'盲盒引流货（¥0.01）必须被身份过滤拦截，
    否则价格判断会误认为超低价商品。"""
    words = tmp_path / "invalid.txt"
    words.write_text("盲盒\n福利疯抢\n随机发货\n", encoding="utf-8")
    f = IdentityFilter(words_file=words)
    product = make_product("【福利疯抢】iPhone 15", "盲盒随机打包手机，1人1单")
    detail = DetailResult(desc="盲盒随机打包手机，1人1单，随机发货")
    result = f.check(product, detail, make_monitor())
    assert result.passed is False
    assert "盲盒" in result.reasons[0]
