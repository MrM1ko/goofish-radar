"""搜索 + 最新发布排序 + 新品扫描（设计文档第 8.2 节）。

扫描逻辑：
  从最新商品开始逐个读取 item_id：
    - item_id 已在 seen 中 → 到达上一轮边界，停止；
    - 累计达到 max_scan_items → 停止（防页面/排序异常导致无限扫描）。

提取失败（如价格解析失败）的商品不中断整体扫描，仅记录日志。
"""

from __future__ import annotations

import logging
import re
import time
from typing import Callable
from urllib.parse import quote

from playwright.sync_api import Page

from browser.selectors import Selectors, pick
from core.models import Product

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.goofish.com/search?q={keyword}"

# 从商品链接中提取 item_id：/item?id=xxx 或 /item/xxx
_ITEM_ID_PATTERNS = (
    re.compile(r"[?&]id=(\d+)"),
    re.compile(r"/item/(\d+)"),
)

# 不确定价格标记：文本中出现这些词时价格不可信，宁可解析失败
# （解析失败 → 上层策略：不自动拍，仅通知）
_UNCERTAIN_PRICE_MARKS = ("起", "面议", "私聊", "询价", "联系")


def parse_price(text: str | None) -> float | None:
    """解析价格文本："¥1999" / "1999.00" / "1999元" → 1999.0。

    以下情况返回 None（视为解析失败）：
      - 空文本；
      - 含 "999起"、"面议"、"私聊" 等不确定价格标记；
      - 价格区间 "800-1000"（无法确定实际价格）。
    """
    if not text:
        return None
    cleaned = text.replace(",", "")
    for mark in _UNCERTAIN_PRICE_MARKS:
        if mark in cleaned:
            return None
    if re.search(r"\d+\s*-\s*\d+", cleaned):
        return None
    m = re.search(r"\d+(?:\.\d+)?", cleaned)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


class Searcher:
    """搜索页操作封装。"""

    def __init__(self, page: Page, selectors: Selectors, max_scan_items: int = 50):
        self.page = page
        self.selectors = selectors
        self.max_scan_items = max_scan_items

    # ------------------------------------------------------------- 搜索

    def search(self, keyword: str, sort: str = "time") -> None:
        """打开搜索页并设置排序。

        优先尝试 URL 参数（实测后固化），失败则点击页面排序按钮。
        """
        url = SEARCH_URL.format(keyword=quote(keyword))
        self.page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        self.page.wait_for_timeout(1500)

        if not self._apply_sort(sort):
            logger.warning("排序方式未能确认（关键词: %s），按页面默认顺序继续", keyword)

    def _apply_sort(self, sort: str) -> bool:
        """确认页面处于目标排序。返回 False 表示无法确认。

        sort 值映射：time → 最新发布。
        待 probe.py 实测后把有效方式固化到这里（URL 参数优先于页面点击）。
        """
        try:
            button = pick(self.page, self.selectors.sort_button, "排序按钮")
            button.click()
            self.page.wait_for_timeout(1200)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------- 扫描

    def scan(self, is_new: Callable[[str], bool]) -> list[Product]:
        """从最新开始扫描商品。

        is_new(item_id) -> bool：去重判断，由 dedupe.DedupeStore 提供。
        返回本轮新商品列表（不含边界处已见商品）。
        """
        products: list[Product] = []
        cards = self._collect_cards()
        if not cards:
            logger.warning("搜索页未找到商品卡片，可能页面结构变化或结果为空")
            return products

        for card in cards:
            if len(products) >= self.max_scan_items:
                logger.info("达到扫描上限 %d，停止", self.max_scan_items)
                break

            product = self._extract_product(card)
            if product is None:
                continue

            if not is_new(product.item_id):
                logger.debug("遇到已见商品 %s，扫描边界停止", product.item_id)
                break

            products.append(product)
            logger.debug("发现新商品: %s %s", product.item_id, product.title)

        return products

    def _collect_cards(self) -> list:
        """收集当前页面的商品卡片元素（遍历候选选择器）。"""
        for css in self.selectors.item_card:
            locator = self.page.locator(css)
            try:
                if locator.count() > 0:
                    return locator.all()
            except Exception:
                continue
        return []

    def _extract_product(self, card) -> Product | None:
        """从单个卡片提取商品信息。提取失败返回 None（不中断扫描）。"""
        try:
            url = self._card_href(card)
            if not url:
                return None
            item_id = self._extract_item_id(url)
            if not item_id:
                logger.debug("无法从链接提取 item_id，跳过: %s", url)
                return None

            title = self._card_text(card, self.selectors.item_title) or ""
            price = parse_price(self._card_text(card, self.selectors.item_price))
            if price is None:
                logger.debug("价格解析失败，跳过商品 %s", item_id)
                return None

            return Product(
                item_id=item_id,
                title=title.strip(),
                price=price,
                url=url,
            )
        except Exception as e:
            logger.debug("商品卡片提取失败: %s", e)
            return None

    # ------------------------------------------------------------- 工具

    @staticmethod
    def _extract_item_id(url: str) -> str | None:
        for pattern in _ITEM_ID_PATTERNS:
            m = pattern.search(url)
            if m:
                return m.group(1)
        return None

    def _card_href(self, card) -> str | None:
        """提取卡片中商品链接（卡片自身或其内部第一个链接）。"""
        try:
            if card.get_attribute("href"):
                return card.get_attribute("href")
            for css in self.selectors.item_link:
                inner = card.locator(css)
                if inner.count() > 0:
                    return inner.first.get_attribute("href")
        except Exception:
            pass
        return None

    def _card_text(self, card, candidates: list[str]) -> str | None:
        """在卡片内按候选顺序提取第一个非空文本。"""
        for css in candidates:
            try:
                locator = card.locator(css)
                if locator.count() > 0:
                    text = locator.first.inner_text()
                    if text and text.strip():
                        return text.strip()
            except Exception:
                continue
        return None

    def random_delay(self, min_seconds: int, max_seconds: int) -> None:
        """搜索前随机延时，降低机械感（设计文档第 20 节）。"""
        import random

        delay = random.uniform(min_seconds, max_seconds)
        time.sleep(delay)
