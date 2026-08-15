"""商品详情页读取（设计文档第 8.3 节）。

读取：标题、描述全文、详情页价格、商品状态、规格信息、卖家基础信息。
重点输出 has_sku / sku_count 供多规格策略使用：
  搜索页展示价不一定对应目标商品本体（手机 ¥3000 / 包装盒 ¥100），
  因此检测到多规格时默认不自动拍。

读取失败不抛异常，返回 DetailResult(failed=True) 交给上层策略
（设计文档第 25 节：详情失败 → 不自动拍 → 记录 → 可通知）。
"""

from __future__ import annotations

import logging

from playwright.sync_api import Page

from browser.selectors import Selectors
from browser.searcher import parse_price
from core.models import DetailResult

logger = logging.getLogger(__name__)


class DetailReader:
    """详情页读取封装。"""

    def __init__(self, page: Page, selectors: Selectors):
        self.page = page
        self.selectors = selectors

    def read(self, url: str) -> DetailResult:
        """打开详情页并读取信息。任何异常都返回 failed=True 的结果。"""
        result = DetailResult()
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            self.page.wait_for_timeout(1500)
        except Exception as e:
            return self._fail(f"打开详情页失败: {e}")

        try:
            result.title = self._read_title()
            result.desc = self._text(self.selectors.detail_desc)
            result.price = self._price(self._text(self.selectors.detail_price))
            result.postage = self._text(self.selectors.detail_post)
            result.status = self._text(self.selectors.detail_status)
            result.has_sku, result.sku_count = self._detect_sku()
            result.seller_info = self._text(self.selectors.logged_in_mark)
        except Exception as e:
            return self._fail(f"详情字段读取失败: {e}")

        logger.debug(
            "详情读取完成: %s price=%s sku=%s/%d",
            result.title, result.price, result.has_sku, result.sku_count,
        )
        return result

    # ------------------------------------------------------------- 内部

    def _read_title(self) -> str | None:
        """读取标题：优先页面 <title>（去掉 _闲鱼 后缀），回退 DOM 选择器。"""
        try:
            page_title = self.page.title()
            if page_title:
                return page_title.removesuffix("_闲鱼").strip() or None
        except Exception:
            pass
        return self._text(self.selectors.detail_title)

    def _detect_sku(self) -> tuple[bool, int]:
        """检测多规格：找到规格容器后统计【容器内可选规格项】的数量。

        注意不能直接统计容器数量：一个商品通常只有一个规格容器，
        里面才是多个可选规格项（手机 ¥3000 / 包装盒 ¥100 / 配件 ¥20）。
        统计不到项时回退为页面级 sku_item 数量。
        """
        sku_count = 0
        # 1. 容器内统计
        for group_css in self.selectors.sku_group:
            try:
                group = self.page.locator(group_css)
                if group.count() == 0:
                    continue
                container = group.first
                for item_css in self.selectors.sku_item:
                    try:
                        sku_count = container.locator(item_css).count()
                    except Exception:
                        continue
                    if sku_count > 0:
                        break
                if sku_count > 0:
                    break
            except Exception:
                continue
        # 2. 页面级回退
        if sku_count == 0:
            for item_css in self.selectors.sku_item:
                try:
                    locator = self.page.locator(item_css)
                    if locator.count() > 0:
                        sku_count = locator.count()
                        break
                except Exception:
                    continue
        return sku_count > 1, sku_count

    def _text(self, candidates: list[str]) -> str | None:
        for css in candidates:
            try:
                locator = self.page.locator(css)
                if locator.count() > 0:
                    text = locator.first.inner_text()
                    if text and text.strip():
                        return text.strip()
            except Exception:
                continue
        return None

    @staticmethod
    def _price(text: str | None) -> float | None:
        return parse_price(text)

    @staticmethod
    def _fail(error: str) -> DetailResult:
        logger.warning("详情读取失败: %s", error)
        return DetailResult(failed=True, error=error)
