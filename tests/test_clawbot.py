"""clawbot 通知器单元测试：请求构造与失败处理（不发起真实网络请求）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.settings import ClawbotConfig
from core.notifier.clawbot import ClawbotNotifier


def make_notifier(monkeypatch, enabled=True, token="t" * 64):
    monkeypatch.setenv("REASONIX_BOT_CONTROL_TOKEN", token)
    cfg = ClawbotConfig(enabled=enabled, chat_id="someone@im.wechat")
    return ClawbotNotifier(cfg)


def test_disabled_skips(monkeypatch):
    n = make_notifier(monkeypatch, enabled=False)
    assert n.notify("标题", "正文") is False


def test_notify_builds_request(monkeypatch):
    n = make_notifier(monkeypatch)
    calls = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["url"] = url
        calls["json"] = json
        calls["headers"] = headers
        calls["timeout"] = timeout

        class Resp:
            def raise_for_status(self):
                pass

        return Resp()

    monkeypatch.setattr("core.notifier.clawbot.requests.post", fake_post)
    assert n.notify("标题", "正文") is True
    assert calls["url"] == "http://127.0.0.1:37913/send"
    assert calls["json"]["text"] == "标题\n\n正文"
    assert calls["json"]["chat_id"] == "someone@im.wechat"
    assert calls["json"]["connection_id"] == "weixin-weixin"
    assert calls["json"]["domain"] == "weixin"
    assert calls["json"]["chat_type"] == "dm"
    assert calls["headers"]["Authorization"] == f"Bearer {'t' * 64}"


def test_missing_token_returns_false(monkeypatch):
    monkeypatch.delenv("REASONIX_BOT_CONTROL_TOKEN", raising=False)
    cfg = ClawbotConfig(enabled=True, chat_id="someone@im.wechat")
    n = ClawbotNotifier(cfg)
    assert n.notify("标题", "正文") is False


def test_request_error_returns_false(monkeypatch):
    n = make_notifier(monkeypatch)

    def fake_post(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("core.notifier.clawbot.requests.post", fake_post)
    assert n.notify("标题", "正文") is False
