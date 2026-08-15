"""商品身份过滤：判断该商品是否是目标商品本体。

执行顺序优先于损坏判断——先排除"根本不是我要买的东西"，
再考虑"东西本身有没有问题"。

判断文本 = title + seller_desc；
词表 = 全局 invalid_item_words.txt + monitor.exclude_words。
命中任意词直接 reject。
"""

from __future__ import annotations

import logging
from functools import lru_cache

from core.config import INVALID_ITEM_WORDS_FILE, MonitorConfig
from core.filter.base import Filter, load_words, match_words, normalize_text
from core.models import DetailResult, FilterResult, Product

logger = logging.getLogger(__name__)


class IdentityFilter(Filter):
    name = "identity"

    def __init__(self, words_file=INVALID_ITEM_WORDS_FILE):
        self.words_file = words_file

    def check(
        self,
        product: Product,
        detail: DetailResult | None,
        monitor: MonitorConfig,
    ) -> FilterResult:
        # 详情读取失败时只基于标题判断；详情可用时合并描述
        text_parts = [product.title]
        if detail is not None and detail.desc:
            text_parts.append(detail.desc)
        text = normalize_text(" ".join(text_parts))

        global_plain, global_patterns = self._global_words()
        hits: list[str] = match_words(text, global_plain, global_patterns)

        monitor_plain = [normalize_text(w) for w in monitor.exclude_words]
        hits += match_words(text, monitor_plain, [])

        if hits:
            return FilterResult(
                passed=False,
                reasons=[f"商品身份不符（命中: {'、'.join(hits)}）"],
            )
        return FilterResult(
            passed=True,
            reasons=["商品身份符合"],
        )

    @lru_cache(maxsize=1)
    def _global_words(self):
        return load_words(self.words_file)
