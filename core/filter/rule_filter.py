"""规则过滤：损坏/瑕疵 + 引流。

- negative_words.txt —— 明确的损坏、故障、瑕疵描述；
- traction_words.txt —— 明显引流行为（仅命中明确词才排除，保持保守）。

轻量否定判断（"无划痕"、"没有磕碰"、"未维修" 等）：
规则层不追求复杂自然语言推理，复杂表达交给 AI 层。
"""

from __future__ import annotations

import logging
from functools import lru_cache

from core.config import NEGATIVE_WORDS_FILE, TRACTION_WORDS_FILE, MonitorConfig
from core.filter.base import Filter, load_words, match_words, normalize_text
from core.models import DetailResult, FilterResult, Product

logger = logging.getLogger(__name__)

# 否定前缀：词表词前面出现这些前缀时视为"没有该问题"
_NEGATION_PREFIXES = ("无", "没有", "未", "没", "无任何", "不存在", "非")


class RuleFilter(Filter):
    name = "rule"

    def __init__(self, negative_file=NEGATIVE_WORDS_FILE, traction_file=TRACTION_WORDS_FILE):
        self.negative_file = negative_file
        self.traction_file = traction_file

    def check(
        self,
        product: Product,
        detail: DetailResult | None,
        monitor: MonitorConfig,
    ) -> FilterResult:
        text_parts = [product.title]
        if detail is not None and detail.desc:
            text_parts.append(detail.desc)
        text = normalize_text(" ".join(text_parts))

        reasons: list[str] = []

        neg_plain, neg_patterns = self._negative_words()
        trac_plain, trac_patterns = self._traction_words()

        damaged = self._match_with_negation(text, neg_plain, neg_patterns)
        if damaged:
            reasons.append(f"疑似损坏/瑕疵（命中: {'、'.join(damaged)}）")

        traction = match_words(text, trac_plain, trac_patterns)
        if traction:
            reasons.append(f"疑似引流（命中: {'、'.join(traction)}）")

        if reasons:
            return FilterResult(passed=False, reasons=reasons)
        return FilterResult(passed=True, reasons=["未命中损坏/引流词"])

    def _match_with_negation(self, text, plain, patterns):
        """带轻量否定判断的匹配：词表词前出现否定前缀时不计入。"""
        hits = []
        for word in plain:
            idx = text.find(word)
            while idx >= 0:
                if not self._is_negated(text, idx):
                    hits.append(word)
                    break
                idx = text.find(word, idx + 1)
        for pattern in patterns:
            for m in pattern.finditer(text):
                if not self._is_negated(text, m.start()):
                    hits.append(f"regex:{pattern.pattern}")
                    break
        return hits

    @staticmethod
    def _is_negated(text: str, word_start: int) -> bool:
        """检查 word_start 位置前的字符是否构成否定前缀。

        例如 "无划痕" 中 "划痕" 前是 "无" → 否定成立。
        """
        for prefix in _NEGATION_PREFIXES:
            start = word_start - len(prefix)
            if start >= 0 and text[start:word_start] == prefix:
                return True
        return False

    @lru_cache(maxsize=1)
    def _negative_words(self):
        return load_words(self.negative_file)

    @lru_cache(maxsize=1)
    def _traction_words(self):
        return load_words(self.traction_file)
