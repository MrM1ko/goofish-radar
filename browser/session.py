"""登录与会话复用（设计文档第 8.1 节）。

职责：
  - 启动 Chromium，创建 Playwright context；
  - 加载 storage_state.json，验证登录状态；
  - 失效时切换有头模式让用户扫码登录，成功后重新保存会话；
  - 识别验证码/滑块/风控页面，交给上层（pipeline）执行暂停策略。

扫码登录是唯一需要人工参与的环节：程序打开登录页后轮询检测
登录状态，用户完成扫码即继续；超过 timeout 秒仍未完成则放弃本轮。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from browser.selectors import Selectors, pick

logger = logging.getLogger(__name__)

HOME_URL = "https://www.goofish.com/"
LOGIN_URL = "https://www.goofish.com/login"

# 用户扫码等待时长（秒）
LOGIN_TIMEOUT_SECONDS = 180


class Session:
    """浏览器会话管理。

    一个 Session 对应一个 Browser + 一个 Context。
    主循环常驻复用，避免每轮开关浏览器（降低风控、提升性能）。
    """

    def __init__(
        self,
        storage_path: Path,
        headless: bool = True,
        selectors: Selectors | None = None,
    ):
        self.storage_path = storage_path
        self.headless = headless
        self.selectors = selectors or Selectors()
        self._pw = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self.page: Page | None = None

    # ------------------------------------------------------------- 生命周期

    def start(self) -> None:
        """启动浏览器并加载会话。

        ⚠️ headless=True 会被闲鱼 baxia 风控静默拒绝（返回空壳页面，
        商品列表永远为空），因此强制使用有头模式运行。
        """
        if self.headless:
            logger.warning(
                "headless=True 会被闲鱼风控拒绝（2026-08 实测），已强制切换为有头模式"
            )
            self.headless = False
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            # 与真人环境一致；无头模式下 UA 会被 Playwright 自动标注，
            # 保持默认即可，避免过度伪装反而触发风控
        )
        storage_state = str(self.storage_path) if self.storage_path.exists() else None
        self._context = self._browser.new_context(storage_state=storage_state)
        self.page = self._context.new_page()
        logger.info(
            "浏览器已启动（headless=%s, 会话复用=%s）",
            self.headless,
            storage_state is not None,
        )

    def close(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
        self._browser = None
        self._context = None
        self.page = None

    # ------------------------------------------------------------- 登录

    def ensure_logged_in(self) -> bool:
        """验证登录状态；未登录则切有头模式引导扫码。

        返回 True 表示会话可用，False 表示用户放弃/超时（本轮应退出）。
        """
        assert self.page is not None, "Session 未启动"
        if self._check_logged_in():
            return True

        logger.warning("未登录或登录已失效，尝试引导扫码登录")
        return self._login_via_qrcode()

    def _check_logged_in(self) -> bool:
        """打开首页判断登录状态（任一信号命中即视为已登录）：

          1. Cookie 层面：存在阿里登录态 Cookie（unb / _m_h5_tk / cookie2）
             —— 扫码成功后由页面写入，比 DOM 元素可靠；
          2. 页面存在用户头像等元素；
          3. 页面不存在精确文本"登录"的入口。
        """
        try:
            self.page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30_000)
            self.page.wait_for_timeout(1500)
        except Exception as e:
            logger.warning("打开首页失败，暂按未登录处理: %s", e)
            return False
        if self._has_login_cookie():
            return True
        for css in self.selectors.logged_in_mark:
            try:
                if self.page.locator(css).count() > 0:
                    return True
            except Exception:
                continue
        for css in self.selectors.login_entry:
            try:
                if self.page.locator(css).count() > 0:
                    logger.debug("检测到登录入口，判定未登录")
                    return False
            except Exception:
                continue
        return False

    def _has_login_cookie(self) -> bool:
        """检查当前 context 是否存在代表登录态的 Cookie。"""
        assert self._context is not None, "Session 未启动"
        login_cookies = {"unb", "_m_h5_tk", "cookie2", "sgcookie"}
        try:
            for cookie in self._context.cookies():
                if cookie.get("name") in login_cookies:
                    return True
        except Exception:
            pass
        return False

    def _login_via_qrcode(self) -> bool:
        """切换有头模式打开登录页，等待用户扫码。

        流程：重开浏览器为有头模式 → 打开闲鱼登录页 → 用户扫码
        → 程序轮询登录状态 → 成功后保存 storage_state。
        """
        self.close()
        self.headless = False
        self.start()

        logger.info("请在浏览器窗口中完成扫码登录（最长等待 %d 秒）", LOGIN_TIMEOUT_SECONDS)
        self.page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
        self.page.wait_for_timeout(2000)

        deadline = time.time() + LOGIN_TIMEOUT_SECONDS
        while time.time() < deadline:
            # 登录成功信号：页面跳转离开登录页，或当前页出现用户头像等标识。
            # 无论哪个信号，都跳回首页做一次完整确认（否定信号优先），
            # 避免登录页上的"用户协议"等元素造成假阳性。
            try:
                left_login = "login" not in self.page.url
            except Exception:
                left_login = False
            if left_login or self._check_logged_in_on_current_page():
                if self._check_logged_in():
                    self.save_storage()
                    logger.info("扫码登录成功，会话已保存到 %s", self.storage_path)
                    return True
            self.page.wait_for_timeout(3000)

        logger.error("扫码登录超时，本轮退出")
        return False

    def _check_logged_in_on_current_page(self) -> bool:
        """不导航，仅检查当前页面是否出现登录成功标识。"""
        for css in self.selectors.logged_in_mark:
            try:
                if self.page.locator(css).count() > 0:
                    return True
            except Exception:
                continue
        return False

    def save_storage(self) -> None:
        """登录成功后保存会话，后续运行无需重复登录。"""
        assert self._context is not None, "Session 未启动"
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._context.storage_state(path=str(self.storage_path))

    # ------------------------------------------------------------- 风控识别

    def detect_captcha(self) -> bool:
        """当前页面是否出现验证码/滑块。

        注意：搜索流程中可能每个 monitor 都会调用，检测失败
        （元素定位异常）按"未检测到"处理，避免误暂停。
        """
        assert self.page is not None, "Session 未启动"
        for css in self.selectors.captcha_mark + self.selectors.slider_mark:
            try:
                if self.page.locator(css).count() > 0:
                    logger.warning("检测到验证码/滑块: %s", css)
                    return True
            except Exception:
                continue
        return False
