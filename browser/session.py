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
import re
import time
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from browser.selectors import Selectors, pick
from core.settings import LoginConfig

logger = logging.getLogger(__name__)

HOME_URL = "https://www.goofish.com/"
LOGIN_URL = "https://www.goofish.com/login"

# 用户扫码等待时长（秒）
LOGIN_TIMEOUT_SECONDS = 180

# 登录 iframe 特征（实测 2026-08：密码登录表单在 passport.goofish.com mini_login 内）
_LOGIN_FRAME_URL_MARKS = ("mini_login", "passport.goofish.com")

# 密码登录表单元素（实测 2026-08 固化，见 selectors.py 注释约定）
_PASSWORD_TAB = "a.password-login-tab-item"
_LOGIN_ID_INPUT = "#fm-login-id"
_LOGIN_PASSWORD_INPUT = "#fm-login-password"
_LOGIN_SUBMIT_BUTTON = "button.fm-button.fm-submit.password-login"
_AGREEMENT_CHECKBOX = "#fm-agreement-checkbox"
_KEEP_LOGIN_BUTTON = "button.keep-login-confirm-btn.primary"


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
        login: LoginConfig | None = None,
    ):
        self.storage_path = storage_path
        self.headless = headless
        self.selectors = selectors or Selectors()
        self.login = login
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
        """验证登录状态；未登录则尝试登录（账号密码优先，失败回退扫码）。

        返回 True 表示会话可用，False 表示用户放弃/超时（本轮应退出）。
        """
        assert self.page is not None, "Session 未启动"
        if self._check_logged_in():
            return True

        logger.warning("未登录或登录已失效，尝试自动登录")
        return self.re_login()

    def re_login(self) -> bool:
        """会话失效后的重新登录：配置了账号密码则优先，失败回退扫码。"""
        if self.login is not None and self.login.enabled:
            if self._login_via_password():
                return True
            logger.warning("账号密码登录失败，回退扫码登录")
        return self._login_via_qrcode()

    def detect_login_required(self) -> bool:
        """当前页面是否被登录拦截（搜索时被风控重定向到登录页）。

        信号（任一命中即需要登录）：
          1. 当前 URL 是登录/认证路径（/login、passport）；
          2. 页面出现登录 iframe（passport.goofish.com mini_login）。

        注意：阿里系 Cookie（unb/_m_h5_tk/cookie2）匿名访问也会种，
        不能作为登录态信号（2026-08 实测假阳性），此处不参考 Cookie。
        """
        assert self.page is not None, "Session 未启动"
        try:
            url = self.page.url or ""
        except Exception:
            url = ""
        if re.search(r"/(?:login|passport)", url):
            return True
        try:
            for frame in self.page.frames:
                if any(mark in frame.url for mark in _LOGIN_FRAME_URL_MARKS):
                    return True
        except Exception:
            pass
        return False

    def _check_logged_in(self) -> bool:
        """打开首页判断登录状态（2026-08 实测修正）：

          1. 否定信号优先：页面存在"立即登录"入口（login_entry）→ 未登录；
          2. 肯定信号：顶栏昵称区显示昵称（logged_in_mark，排除匿名占位文本"登录"）。

        注意：阿里系 Cookie（unb/_m_h5_tk/cookie2）匿名访问也会种，
        商品卡卖家头像（[class*="avatar"]）匿名页也有 20 个，均不可作为
        登录信号——曾导致假阳性"登录成功"（2026-08 实测）。
        """
        try:
            self.page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30_000)
            self.page.wait_for_timeout(1500)
        except Exception as e:
            logger.warning("打开首页失败，暂按未登录处理: %s", e)
            return False
        for css in self.selectors.login_entry:
            try:
                if self.page.locator(css).count() > 0:
                    logger.debug("检测到'立即登录'入口，判定未登录")
                    return False
            except Exception:
                continue
        return self._has_logged_in_mark()

    def _has_logged_in_mark(self) -> bool:
        """当前页面是否出现登录成功标识（昵称区文本非"登录"占位）。"""
        assert self.page is not None, "Session 未启动"
        for css in self.selectors.logged_in_mark:
            try:
                locator = self.page.locator(css)
                for i in range(min(locator.count(), 5)):
                    text = (locator.nth(i).inner_text() or "").strip()
                    if text and "登录" not in text and "立即登录" not in text:
                        return True
            except Exception:
                continue
        return False

    def _login_via_password(self) -> bool:
        """账号密码登录（实测结构 2026-08，见模块顶部常量注释）。

        流程：打开闲鱼登录页 → 进入登录 iframe → 切"密码登录"tab
        → 填账号密码 → 勾选协议 → 点登录 → 轮询结果（自动处理
        "保持登录"弹层；滑块/短信验证需人工，等待期间持续轮询；
        密码错误等明确失败提示则提前返回 False）。
        成功后保存 storage_state，会话可复用。
        """
        assert self.login is not None and self.login.username and self.login.password

        if self.headless:
            # headless 会被闲鱼 baxia 风控拒绝，切有头（与 start() 注释一致）
            self.close()
            self.headless = False
            self.start()
        assert self.page is not None, "Session 未启动"

        logger.info("打开登录页，使用账号密码登录: %s", self._mask_username(self.login.username))
        self.page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
        self.page.wait_for_timeout(3000)

        frame = self._find_login_frame()
        if frame is None:
            logger.error("未找到登录 iframe（页面结构可能变化），账号密码登录失败")
            return False

        try:
            tab = frame.locator(_PASSWORD_TAB)
            if tab.count() > 0:
                tab.first.click()
                self.page.wait_for_timeout(1500)
        except Exception as e:
            logger.warning("切换'密码登录'tab 失败: %s", e)

        try:
            frame.locator(_LOGIN_ID_INPUT).fill(self.login.username)
            frame.locator(_LOGIN_PASSWORD_INPUT).fill(self.login.password)
        except Exception as e:
            logger.error("填写账号/密码失败（登录表单结构可能变化）: %s", e)
            return False

        try:
            checkbox = frame.locator(_AGREEMENT_CHECKBOX)
            if checkbox.count() > 0 and not checkbox.is_checked():
                checkbox.check()
        except Exception:
            pass

        try:
            submit = frame.locator(_LOGIN_SUBMIT_BUTTON)
            if submit.count() == 0:
                submit = frame.locator("button.fm-submit:has-text('登录')")
            submit.first.click()
        except Exception as e:
            logger.error("点击登录按钮失败: %s", e)
            return False

        deadline = time.time() + LOGIN_TIMEOUT_SECONDS
        slider_hint_logged = False
        while time.time() < deadline:
            # "保持登录"弹层（登录成功后出现，点"保持"）
            try:
                keep = frame.locator(_KEEP_LOGIN_BUTTON)
                if keep.count() > 0 and keep.first.is_visible():
                    keep.first.click()
                    self.page.wait_for_timeout(1000)
            except Exception:
                pass

            # 明确失败提示（密码错误/账号不存在等）→ 提前失败，回退扫码
            error_text = self._login_error_text(frame)
            if error_text:
                logger.error("账号密码登录失败，页面提示: %s", error_text)
                return False

            # 滑块/短信验证码提示（一次即可，等用户在有头窗口人工完成）
            if not slider_hint_logged and self._slider_visible(frame):
                logger.info("检测到滑块/安全验证，请在浏览器窗口中人工完成（剩余等待时间内的轮询会自动继续）")
                slider_hint_logged = True

            # 成功信号：跳回首页完整确认（否定信号优先，避免假阳性）
            if self._check_logged_in():
                self.save_storage()
                logger.info("账号密码登录成功，会话已保存到 %s", self.storage_path)
                return True

            self.page.wait_for_timeout(2000)

        logger.error("账号密码登录超时（可能卡在滑块/短信验证未完成）")
        return False

    def _find_login_frame(self):
        """返回登录 iframe（无则 None）。"""
        assert self.page is not None, "Session 未启动"
        for frame in self.page.frames:
            if any(mark in frame.url for mark in _LOGIN_FRAME_URL_MARKS):
                return frame
        return None

    def _login_error_text(self, frame) -> str | None:
        """登录 iframe 内的明确错误提示文本（如密码错误）；无则 None。"""
        keywords = ("密码", "账号", "错误", "不正确", "不存在", "频繁", "失败", "受限")
        for css in ("#fm-login-error", "[class*='fm-error']", "[class*='error']", "[class*='Error']"):
            try:
                locator = frame.locator(css)
                for i in range(min(locator.count(), 8)):
                    text = (locator.nth(i).inner_text() or "").strip()
                    if text and any(k in text for k in keywords):
                        return text[:120]
            except Exception:
                continue
        return None

    def _slider_visible(self, frame) -> bool:
        """登录 iframe 内是否出现滑块/安全验证组件。"""
        for css in (
            "#nc_1_captcha_input",
            "#nc_2_captcha_input",
            "[class*='nc-container']",
            "[class*='baxia-dialog']",
            "text=安全验证",
            "text=拖动滑块",
        ):
            try:
                locator = frame.locator(css)
                if locator.count() > 0 and locator.first.is_visible():
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _mask_username(username: str) -> str:
        """账号打码，避免日志泄露完整账号。"""
        if len(username) <= 4:
            return username[:1] + "***"
        return username[:3] + "****" + username[-4:]

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
        return self._has_logged_in_mark()

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
