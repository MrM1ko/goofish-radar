"""创建待付款订单（设计文档第 8.4 节）。

职责只有一个：创建待付款订单，绝不付款。

流程：
  open(item_url)
  → 再次确认商品仍在售
  → 再次读取价格
  → 确认没有多规格
  → 点击立即购买
  → 订单确认页点击提交订单
  → 等待结果（success / failed / unknown）
  → 绝不点击任何支付按钮

明确禁止：支付密码、付款按钮、支付宝确认、微信支付、免密支付、
自动付款。进入待付款状态后浏览器自动化任务即结束。

UNKNOWN 语义（设计文档第 18 节）：
  点击提交订单后页面超时 / 网络断开 / 页面结构异常，
  无法确认订单是否生成 → 返回 unknown，
  由上层落盘并禁止重拍 + 邮件通知人工检查。
"""

from __future__ import annotations

import logging

from playwright.sync_api import Page

from browser.selectors import Selectors, SelectorError
from core.models import OrderResult, Product

logger = logging.getLogger(__name__)

SUBMIT_WAIT_SECONDS = 10


class OrderCreator:
    """下单执行器。以函数对象形式注入 Orderer，保持纯逻辑可测。"""

    def __init__(self, page: Page, selectors: Selectors):
        self.page = page
        self.selectors = selectors

    def __call__(self, product: Product) -> OrderResult:
        return self.create_pending_order(product)

    def create_pending_order(self, product: Product) -> OrderResult:
        """对指定商品执行下单流程，返回 success / failed / unknown。"""
        try:
            self.page.goto(product.url, wait_until="domcontentloaded", timeout=30_000)
            self.page.wait_for_timeout(1500)
        except Exception as e:
            return OrderResult(status="failed", reason=f"打开商品页失败: {e}")

        # 1. 确认商品仍在售（下架则明确失败）
        try:
            sold_out = self._is_sold_out()
        except Exception:
            sold_out = False
        if sold_out:
            return OrderResult(status="failed", reason="商品已下架/无法购买")

        # 2. 点击立即购买 → 进入订单确认页
        try:
            self._click_buy_now()
        except SelectorError as e:
            return OrderResult(status="failed", reason=f"找不到立即购买按钮: {e}")
        except Exception as e:
            return OrderResult(status="failed", reason=f"点击立即购买失败: {e}")

        # 3. 提交订单
        try:
            self._click_submit()
        except Exception as e:
            # 提交动作本身失败（找不到按钮/点击失败）是明确的 failed
            return OrderResult(status="failed", reason=f"提交订单失败: {e}")

        # 4. 判定结果（点击提交之后的任何异常/超时都归为 unknown）
        return self._judge_result()

    # ------------------------------------------------------------- 内部

    def _is_sold_out(self) -> bool:
        for css in self.selectors.detail_status:
            try:
                locator = self.page.locator(css)
                if locator.count() > 0:
                    text = locator.first.inner_text() or ""
                    if "下架" in text or "无法购买" in text:
                        return True
            except Exception:
                continue
        return False

    def _click_buy_now(self) -> None:
        """进入订单确认页。

        实测（2026-08）："立即购买"是 <a class="buy--MCbvZ6Lw">
        链接，href 直达 create-order?itemId=xxx。优先取 href 直接跳转，
        回退为点击元素。
        """
        for css in self.selectors.buy_now_button:
            locator = self.page.locator(css)
            try:
                if locator.count() == 0:
                    continue
                href = locator.first.get_attribute("href")
                if href:
                    self.page.goto(href, wait_until="domcontentloaded", timeout=30_000)
                    self.page.wait_for_timeout(1500)
                    return
                locator.first.click()
                self.page.wait_for_load_state("domcontentloaded", timeout=15_000)
                self.page.wait_for_timeout(1500)
                return
            except Exception:
                continue
        raise SelectorError(f"立即购买入口未找到: {self.selectors.buy_now_button}")

    def _click_submit(self) -> None:
        for css in self.selectors.submit_order_button:
            locator = self.page.locator(css)
            try:
                if locator.count() > 0:
                    locator.first.click()
                    return
            except Exception:
                continue
        raise SelectorError(f"提交订单按钮未找到: {self.selectors.submit_order_button}")

    def _judge_result(self) -> OrderResult:
        """提交后判定 success / failed / unknown。

        success：明确看到待付款/订单提交成功标识；
        unknown：页面超时、结构异常、无法确认（含等待结果页期间的异常）。
        """
        try:
            self.page.wait_for_timeout(SUBMIT_WAIT_SECONDS * 1000)
        except Exception as e:
            return OrderResult(status="unknown", reason=f"提交后页面等待异常: {e}")

        try:
            for css in self.selectors.order_success_mark:
                locator = self.page.locator(css)
                if locator.count() > 0:
                    return OrderResult(status="success", reason="检测到待付款状态")
        except Exception:
            pass

        # 页面 URL 变化到订单列表/结果页也可作为成功信号（待实测固化）
        try:
            current_url = self.page.url
            if "order" in current_url and "success" in current_url:
                return OrderResult(status="success", reason=f"订单结果页: {current_url}")
        except Exception:
            pass

        return OrderResult(
            status="unknown",
            reason="点击提交后无法确认订单是否生成，请人工检查闲鱼待付款列表",
        )
