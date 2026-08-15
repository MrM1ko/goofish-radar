"""searcher 纯逻辑单元测试：价格解析与 item_id 提取（不依赖浏览器）。"""

from __future__ import annotations

import pytest

from browser.searcher import Searcher, parse_price


class TestParsePrice:
    def test_plain_number(self):
        assert parse_price("1999") == 1999.0

    def test_yuan_symbol(self):
        assert parse_price("¥1999") == 1999.0

    def test_decimal(self):
        assert parse_price("1999.50") == 1999.5

    def test_comma_separated(self):
        assert parse_price("1,999") == 1999.0

    def test_suffix_yuan(self):
        assert parse_price("1999元") == 1999.0

    def test_empty_returns_none(self):
        assert parse_price("") is None
        assert parse_price(None) is None

    @pytest.mark.parametrize("text", ["999起", "面议", "私聊", "询价", "价格联系客服"])
    def test_uncertain_price_returns_none(self, text):
        # 不确定价格宁可解析失败（→不自动拍），也不能按假价格下单
        assert parse_price(text) is None

    def test_range_price_returns_none(self):
        assert parse_price("800-1000") is None


class TestExtractItemId:
    def test_query_param(self):
        assert Searcher._extract_item_id("https://www.goofish.com/item?id=123456") == "123456"

    def test_path_style(self):
        assert Searcher._extract_item_id("https://www.goofish.com/item/98765") == "98765"

    def test_unrecognized_returns_none(self):
        assert Searcher._extract_item_id("https://www.goofish.com/search?q=x") is None
