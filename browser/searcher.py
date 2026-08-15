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
        商品列表是 JS 异步渲染，打开后需等待卡片出现。
        """
        url = SEARCH_URL.format(keyword=quote(keyword))
        self.page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        cards_ready = self._wait_for_cards()
        if not cards_ready:
            logger.warning("等待商品卡片超时（关键词: %s），页面可能未登录或结果为空", keyword)

        if not self._apply_sort(sort):
            logger.warning("排序方式未能确认（关键词: %s），按页面默认顺序继续", keyword)

    def _wait_for_cards(self, timeout_ms: int = 25_000) -> bool:
        """等待商品卡片渲染完成。返回是否等到。"""
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            if self._collect_cards():
                return True
            self.page.wait_for_timeout(500)
        return False

    def _apply_sort(self, sort: str) -> bool:
        """设置"最新发布"= 列表上方【发布时间】筛选器选择"最新"。

        实测（2026-08）：
          - 页面有两个排序控件：排序维度（综合/最近活跃/…）与
            发布时间（最新/1天内/3天内/…）；"最新发布"对应后者；
          - 发布时间默认就是"最新"，此时无需任何操作；
          - 需要切换时：点击标题展开下拉 → 原生点击"最新"选项
            （JS el.click() 对 React 无效，且选项可能在滚动区）。
        """
        if sort != "time":
            logger.warning("不支持的排序方式: %s", sort)
            return False
        try:
            control = pick(self.page, self.selectors.sort_button, "发布时间筛选器")

            # 已是"最新"则直接确认成功
            try:
                current = control.locator(".search-select-title--zzthyzLG").first.inner_text()
                if "最新" in current:
                    logger.debug("发布时间已是'最新'，无需切换")
                    return True
            except Exception:
                pass

            # 展开下拉并选择"最新"
            title = control.locator(".search-select-title-container--PqkTXn91").first
            title.click()
            self.page.wait_for_timeout(1000)
            option = control.locator(
                '.search-select-item--H_AJBURX:has-text("最新")'
            ).first
            option.scroll_into_view_if_needed(timeout=3000)
            self.page.wait_for_timeout(300)
            option.click()
            self.page.wait_for_timeout(1500)  # 等列表重新渲染

            current = control.locator(".search-select-title--zzthyzLG").first.inner_text()
            if "最新" not in current:
                logger.warning("发布时间切换后标题仍为 %r，可能未生效", current)
                return False
            return True
        except Exception as e:
            logger.warning("排序设置失败（按页面默认顺序继续）: %s", e)
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
