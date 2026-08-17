"""Session 登录拦截检测单元测试（不依赖真实浏览器，用假 page/context）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from browser.session import Session


class FakeFrame:
    def __init__(self, url: str):
        self.url = url


class FakePage:
    def __init__(self, url: str = "", frames: list[FakeFrame] | None = None):
        self._url = url
        self.frames = list(frames or [])

    @property
    def url(self) -> str:
        return self._url


class FakeContext:
    def __init__(self, cookies: list[dict] | None = None):
        self._cookies = cookies or []

    def cookies(self) -> list[dict]:
        return self._cookies


def make_session(url: str, frames: list[FakeFrame] | None = None, cookies: list[dict] | None = None) -> Session:
    s = Session(storage_path=Path("unused.json"))
    s.page = FakePage(url, frames)
    s._context = FakeContext(cookies)
    return s


class TestDetectLoginRequired:
    def test_search_page_not_login(self):
        s = make_session("https://www.goofish.com/search?q=MacBook")
        assert s.detect_login_required() is False

    def test_redirected_to_login_url(self):
        s = make_session("https://www.goofish.com/login?redirect=https://www.goofish.com/search")
        assert s.detect_login_required() is True

    def test_passport_url(self):
        s = make_session("https://passport.goofish.com/mini_login.htm?lang=zh_cn")
        assert s.detect_login_required() is True

    def test_login_iframe_present(self):
        s = make_session(
            "https://www.goofish.com/search?q=MacBook",
            frames=[FakeFrame("https://passport.goofish.com/mini_login.htm?lang=zh_cn")],
        )
        assert s.detect_login_required() is True

    def test_unrelated_iframe_ignored(self):
        s = make_session(
            "https://www.goofish.com/search?q=MacBook",
            frames=[FakeFrame("https://g.alicdn.com/xdomain-storage/frame.html")],
        )
        assert s.detect_login_required() is False

    def test_login_url_detected_even_with_cookies(self):
        # 阿里系 cookie 匿名访问也会种，不可靠；URL 信号为准
        s = make_session(
            "https://www.goofish.com/login",
            cookies=[{"name": "unb", "value": "1", "domain": ".goofish.com"}],
        )
        assert s.detect_login_required() is True


class TestMaskUsername:
    def test_phone(self):
        assert Session._mask_username("13800001234") == "138****1234"

    def test_short(self):
        assert Session._mask_username("ab") == "a***"

    def test_email(self):
        masked = Session._mask_username("user@example.com")
        assert masked.startswith("use****")
        assert "user@example.com" not in masked
