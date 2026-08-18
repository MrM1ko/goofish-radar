"""过滤器抽象基类与共享工具。

过滤层职责划分（执行顺序固定）：
  1. identity_filter —— 商品是不是想买的本体（空盒/配件/求购/租赁…）
  2. rule_filter    —— 损坏/瑕疵 + 引流（词表规则）
  3. ai_filter      —— AI 语义判断（可选，失败降级到规则层结果）

任何一层 reject 都会终止后续过滤。
"""

from __future__ import annotations

import re
import unicodedata
from abc import ABC, abstractmethod
from pathlib import Path

from core.settings import MonitorConfig
from core.models import DetailResult, FilterResult, Product


class Filter(ABC):
    """所有过滤器的统一接口。"""

    name: str = "filter"

    @abstractmethod
    def check(
        self,
        product: Product,
        detail: DetailResult | None,
        monitor: MonitorConfig,
    ) -> FilterResult:
        """返回该层的过滤结果；passed=False 表示拒绝。"""


# ------------------------------------------------------------------ 词表


def load_words(path: Path) -> tuple[list[str], list[re.Pattern]]:
    """加载词表文件。

    规则：
      - 每行一个词或短语；`#` 开头为注释；空行忽略；
      - `regex:<表达式>` 形式编译为正则，编译失败的行忽略并记录警告；
      - 其余行作为普通子串匹配。

    返回 (普通词列表, 正则列表)。
    """
    plain: list[str] = []
    patterns: list[re.Pattern] = []
    if not path.exists():
        return plain, patterns

    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("regex:"):
            expr = line[len("regex:"):].strip()
            try:
                patterns.append(re.compile(expr, re.IGNORECASE))
            except re.error:
                # 词表是用户可编辑的外部文件，坏正则不应让程序崩溃
                import logging

                logging.getLogger(__name__).warning(
                    "词表 %s 第 %d 行正则非法，已忽略: %r", path.name, line_no, expr
                )
        else:
            plain.append(line)
    return plain, patterns


def match_words(text: str, plain: list[str], patterns: list[re.Pattern]) -> list[str]:
    """返回在 text 中命中的词/正则原始表达式列表（用于生成拒绝原因）。"""
    hits: list[str] = []
    for word in plain:
        if word in text:
            hits.append(word)
    for pattern in patterns:
        if pattern.search(text):
            hits.append(f"regex:{pattern.pattern}")
    return hits


# ------------------------------------------------------------------ 文本标准化


_FULLWIDTH = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
    "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"
    "（）！？．，；：",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "()!?.,;:",
)


def normalize_text(text: str | None) -> str:
    """规则匹配前的文本标准化。

    - Unicode NFKC 归一化
    - 全角转半角
    - 统一小写
    - 压缩连续空白（去多余空格，如 "V X"、"微 信" 变体）
    """
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.translate(_FULLWIDTH)
    normalized = normalized.lower()
    normalized = re.sub(r"\s+", "", normalized)
    return normalized
