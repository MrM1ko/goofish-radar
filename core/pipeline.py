"""主流程编排（设计文档第 5 节总体数据流）。

Pipeline 只负责编排，具体能力全部通过构造参数注入，
因此各环节都可被假实现替换用于测试：

  对每个 monitor:
    搜索 → 排序 → 扫描新品 → 全局去重 → 详情 → 身份过滤
    → 规则过滤 → AI 过滤 → 价格/多规格判断 → 拍单决策
    → 过滤通过即发「新商品」邮件（与 auto_order 无关）；
      真正执行下单后再发「拍单结果」邮件（标题 = 关键词 + 价格）

风控处理：
  - 验证码/滑块：记录暂停 30 分钟 → 邮件 → 本轮立即退出，不无限重试。
  - 登录拦截（搜索空白/重定向登录页）：发风控邮件 → 自动重登后重搜。
首次运行：建立 seen 基线，只记录不拍单，发送初始化完成通知。
"""

from __future__ import annotations

import logging

from browser.detail import DetailReader
from browser.order import OrderCreator
from browser.searcher import Searcher
from browser.session import Session
from core.config import AppConfig
from core.dedupe import DedupeStore
from core.filter.ai_filter import AiFilter
from core.filter.identity_filter import IdentityFilter
from core.filter.rule_filter import RuleFilter
from core.history import HistoryStore
from core.models import DetailResult, FilterResult, Product
from core.notifier.base import Notifier
from core.orderer import Decision, Orderer
from core.runtime import RuntimeState

logger = logging.getLogger(__name__)

# 验证码暂停时长（分钟），设计文档第 8.1 节
CAPTCHA_PAUSE_MINUTES = 30


class Pipeline:
    """一轮监控的完整编排。"""

    def __init__(
        self,
        cfg: AppConfig,
        dedupe: DedupeStore,
        history: HistoryStore,
        orderer: Orderer,
        notifiers: list[Notifier],
        runtime: RuntimeState,
        session: Session,
    ):
        self.cfg = cfg
        self.dedupe = dedupe
        self.history = history
        self.orderer = orderer
        self.notifiers = notifiers
        self.runtime = runtime
        self.session = session

        selectors = session.selectors
        self._bound_page = session.page
        self.searcher = Searcher(
            self._bound_page, selectors, max_scan_items=cfg.search.max_scan_items
        )
        self.detail_reader = DetailReader(self._bound_page, selectors)

        self.identity_filter = IdentityFilter()
        self.rule_filter = RuleFilter()
        self.ai_filter = AiFilter(cfg.ai)

    # ------------------------------------------------------------- 会话组件

    def _refresh_components(self) -> None:
        """Session 重开浏览器（扫码登录路径）后 page 已更换，重建绑定新 page 的组件。

        账号密码登录复用原 page，无需重建；此处幂等，可每轮调用。
        """
        if self.session.page is self._bound_page:
            return
        self._bound_page = self.session.page
        selectors = self.session.selectors
        self.searcher = Searcher(
            self._bound_page, selectors, max_scan_items=self.cfg.search.max_scan_items
        )
        self.detail_reader = DetailReader(self._bound_page, selectors)
        logger.info("浏览器会话已重开，搜索/详情组件已重新绑定页面")

    # ------------------------------------------------------------- 一轮执行

    def run_once(self) -> None:
        """执行一轮完整扫描（所有启用的 monitor）。"""
        self._refresh_components()
        if self.runtime.is_paused():
            logger.warning("RUNTIME_PAUSED 暂停中: %s，本轮跳过", self.runtime.pause_reason)
            return

        for monitor in self.cfg.enabled_monitors():
            try:
                self._process_monitor(monitor)
            except Exception as e:
                # 单个 monitor 异常不影响其他 monitor（设计文档第 25 节）
                logger.exception("monitor %s 处理异常，跳过: %s", monitor.name, e)

    # ------------------------------------------------------------- 单个 monitor

    def _process_monitor(self, monitor) -> None:
        logger.info("SEARCH_START monitor=%s keyword=%s", monitor.name, monitor.keyword)

        # 搜索前随机延时，降低机械感
        self.searcher.random_delay(*self.cfg.search.random_delay_seconds)
        self.searcher.search(monitor.keyword, self.cfg.search.sort)

        # 登录拦截（风控把搜索重定向到登录页）→ 发风控邮件 + 自动重新登录后重搜一次
        if self.session.detect_login_required():
            self._handle_login_intercept(monitor.name)
            logger.warning(
                "monitor=%s 搜索被登录拦截（风控），尝试自动重新登录", monitor.name
            )
            if not self.session.re_login():
                logger.error("monitor=%s 重新登录失败，本轮跳过", monitor.name)
                return
            self._refresh_components()
            self.searcher.search(monitor.keyword, self.cfg.search.sort)
            if self.session.detect_login_required():
                logger.error("monitor=%s 重新登录后仍被登录拦截，本轮跳过", monitor.name)
                return

        # 风控：验证码 → 暂停 + 通知 + 本轮退出
        if self.session.detect_captcha():
            self._handle_captcha()
            return

        new_products = self.searcher.scan(self.dedupe.is_new)
        if not new_products:
            logger.info("monitor=%s 本轮无新商品", monitor.name)
            return

        # 首次运行：建立基线，只记录不拍单（设计文档第 28 节）
        first_run = self.dedupe.is_empty()
        if first_run:
            for p in new_products:
                self.dedupe.mark_seen(p)
                self.history.append(
                    "discovered", item_id=p.item_id, price=p.price, title=p.title
                )
            self.dedupe.save()
            logger.info("首次运行：已建立基线 %d 条，不执行拍单", len(new_products))
            self._notify(
                "goofish-radar 初始化完成",
                f"首次运行已建立基线：{len(new_products)} 条商品已记录为已见。\n"
                "下一轮开始才会处理真正的新商品。",
            )
            return

        for product in new_products:
            self._process_product(product, monitor)

        self.dedupe.save()

    def _process_product(self, product: Product, monitor) -> None:
        """单个新商品的完整处理链。"""
        # 先标记已见：即使后续处理失败，也不会在下一轮重复进入详情页
        self.dedupe.mark_seen(product)
        self.history.append(
            "discovered", item_id=product.item_id, price=product.price, title=product.title
        )

        # 详情读取（失败 → 不自动拍、不通知，仅记录，设计文档第 25 节）
        detail: DetailResult | None = self.detail_reader.read(product.url)
        if detail is not None and detail.failed:
            detail = None
            self.history.append("detail_failed", item_id=product.item_id)
            logger.info("monitor=%s item=%s 详情读取失败，跳过", monitor.name, product.item_id)
            return

        # 三层过滤
        filter_result = self._run_filters(product, detail, monitor)
        if filter_result.passed:
            self.history.append("filter_passed", item_id=product.item_id)
            # 符合筛选规则的商品即发邮件（与是否开启 auto_order 无关）
            self._notify_new_product(product, detail, filter_result, monitor)
        else:
            self.history.append(
                "filter_rejected", item_id=product.item_id, reasons=filter_result.reasons
            )

        # 拍单决策
        decision = self.orderer.decide(product, detail, filter_result, monitor)
        logger.info(
            "monitor=%s item=%s 决策=%s 理由=%s",
            monitor.name, product.item_id, decision.decision.value, decision.reason,
        )

        if decision.decision == Decision.ORDER:
            result = self.orderer.execute(product, monitor)
            self._handle_order_result(product, monitor, result)
        else:
            # 未满足拍单条件：不发邮件，仅留日志（history 已有 filter 事件可审计）
            logger.info(
                "monitor=%s item=%s 未下单: %s",
                monitor.name, product.item_id, decision.reason,
            )

    # ------------------------------------------------------------- 过滤

    def _run_filters(self, product, detail, monitor) -> FilterResult:
        """按 身份 → 规则 → AI 顺序执行，任一拒绝即短路。"""
        identity = self.identity_filter.check(product, detail, monitor)
        if not identity.passed:
            return identity

        rule = self.rule_filter.check(product, detail, monitor)
        if not rule.passed:
            return rule

        ai = self.ai_filter.check(product, detail, monitor)
        if not ai.passed:
            return ai
        # AI 通过或未执行时，汇总三层结论
        return FilterResult(
            passed=True,
            reasons=identity.reasons + rule.reasons + ai.reasons,
            ai_checked=ai.ai_checked,
            ai_notes=ai.ai_notes,
        )

    # ------------------------------------------------------------- 新商品通知

    def _notify_new_product(self, product, detail, filter_result, monitor) -> None:
        """过滤通过（符合筛选规则）的新商品通知。

        与是否开启 auto_order 无关：auto_order 关闭或未满足拍单条件时，
        这是用户知晓符合条件商品的唯一渠道；开启并自动拍单时，
        会先收到本通知，随后再收到拍单结果通知。
        """
        desc = (detail.desc if detail and detail.desc else None) or product.desc or ""
        if len(desc) > 300:
            desc = desc[:300] + "…"

        if filter_result.ai_checked:
            ai_status = "已通过"
        elif filter_result.ai_notes:
            ai_status = f"未完成（{filter_result.ai_notes}）"
        else:
            ai_status = "未启用"

        body = (
            f"发现符合筛选条件的新商品，请人工查看。\n\n"
            f"关键词: {monitor.keyword}\n"
            f"标题: {product.title}\n"
            f"价格: ¥{product.price}\n"
            f"链接: {product.url}\n"
            f"描述: {desc or '（无）'}\n"
            f"AI 检查: {ai_status}"
        )
        self._notify(f"{monitor.keyword} ¥{product.price} 新商品", body)

    # ------------------------------------------------------------- 下单结果

    def _handle_order_result(self, product, monitor, result) -> None:
        status = result.status
        self.history.append(
            f"order_{status}", item_id=product.item_id, order_id=result.order_id,
            reason=result.reason,
        )
        if status == "success":
            logger.info("ORDER_SUCCESS item=%s order_id=%s", product.item_id, result.order_id)
            self._notify(
                f"{monitor.keyword} ¥{product.price}",
                f"已生成待付款订单，请人工检查后决定是否付款。\n\n"
                f"标题: {product.title}\n"
                f"价格: ¥{product.price}\n"
                f"链接: {product.url}\n"
                f"订单号: {result.order_id or '见闲鱼订单列表'}",
            )
        elif status == "unknown":
            logger.warning("ORDER_UNKNOWN item=%s", product.item_id)
            self._notify(
                f"{monitor.keyword} ¥{product.price}",
                f"订单提交后状态无法确认，请人工检查闲鱼待付款列表。\n\n"
                f"标题: {product.title}\n链接: {product.url}\n原因: {result.reason}",
            )
        else:
            logger.warning("ORDER_FAILED item=%s reason=%s", product.item_id, result.reason)
            self._notify(
                f"{monitor.keyword} ¥{product.price}",
                f"拍单失败。\n\n"
                f"标题: {product.title}\n链接: {product.url}\n原因: {result.reason}",
            )

    # ------------------------------------------------------------- 风控

    def _handle_captcha(self) -> None:
        logger.warning("CAPTCHA_DETECTED 检测到验证码，暂停 %d 分钟", CAPTCHA_PAUSE_MINUTES)
        self.runtime.pause(CAPTCHA_PAUSE_MINUTES, "captcha")
        self._notify(
            "goofish-radar 风控暂停",
            f"检测到验证码/滑块，已暂停 {CAPTCHA_PAUSE_MINUTES} 分钟。\n"
            f"恢复时间: {self.runtime.paused_until}",
        )

    def _handle_login_intercept(self, monitor_name: str) -> None:
        """搜索被登录拦截时的风控通知。

        搜索后无返回结果（页面空白/被重定向到登录页）通常意味着会话
        被风控拦下，与验证码/滑块一样属于需要人工留意的风控信号。
        """
        self._notify(
            "goofish-radar 风控拦截",
            f"monitor={monitor_name} 搜索被登录拦截：搜索无返回结果（页面空白/"
            f"被重定向到登录页），疑似触发风控。程序正在尝试自动重新登录。",
        )

    # ------------------------------------------------------------- 通知

    def _notify(self, subject: str, body: str) -> None:
        if not self.notifiers:
            logger.info("无通知渠道，跳过: %s", subject)
            return
        for notifier in self.notifiers:
            try:
                notifier.notify(subject, body)
            except Exception as e:
                logger.error("通知异常 [%s]: %s", getattr(notifier, "name", "?"), e)


