"""config 单元测试：加载与校验。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core.config import ConfigError, load_config

VALID = {
    "poll_interval_minutes": 5,
    "search": {"sort": "time", "max_scan_items": 50, "headless": True,
               "random_delay_seconds": [3, 8], "screenshot": False},
    "monitors": [
        {"name": "m1", "keyword": "iPhone 15", "enabled": True,
         "auto_order": True, "max_price": 3500.0, "exclude_words": ["空盒"]},
        {"name": "m2", "keyword": "RTX 4070", "enabled": False},
    ],
    "buy": {"enabled": True, "daily_limit": 3, "order_interval_minutes": 20},
    "login": {"enabled": False, "username": "", "password": ""},
    "ai": {"enabled": False, "base_url": "", "model": "", "api_key": "", "timeout_seconds": 20},
    "smtp": {"enabled": False, "host": "", "port": 465, "use_ssl": True,
             "user": "", "password": "", "to": []},
}


def write_cfg(tmp_path, raw) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return path


def test_valid_config_loads(tmp_path):
    cfg = load_config(write_cfg(tmp_path, VALID))
    assert cfg.poll_interval_minutes == 5
    assert len(cfg.enabled_monitors()) == 1
    m = cfg.enabled_monitors()[0]
    assert m.name == "m1"
    assert m.max_price == 3500.0
    assert m.exclude_words == ["空盒"]


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="配置文件不存在"):
        load_config(tmp_path / "nonexistent.json")


def test_invalid_json_raises(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="不是合法 JSON"):
        load_config(path)


def test_empty_monitors_rejected(tmp_path):
    raw = dict(VALID, monitors=[])
    with pytest.raises(ConfigError, match="monitors 必须是非空数组"):
        load_config(write_cfg(tmp_path, raw))


def test_duplicate_monitor_names_rejected(tmp_path):
    raw = dict(VALID, monitors=[
        {"name": "a", "keyword": "k1"},
        {"name": "a", "keyword": "k2"},
    ])
    with pytest.raises(ConfigError, match="必须唯一"):
        load_config(write_cfg(tmp_path, raw))


def test_monitor_missing_keyword_rejected(tmp_path):
    raw = dict(VALID, monitors=[{"name": "a"}])
    with pytest.raises(ConfigError, match="keyword"):
        load_config(write_cfg(tmp_path, raw))


def test_ai_enabled_requires_credentials(tmp_path):
    raw = json.loads(json.dumps(VALID))
    raw["ai"] = {"enabled": True, "base_url": "https://x", "model": "m", "api_key": "", "timeout_seconds": 20}
    with pytest.raises(ConfigError, match="api_key"):
        load_config(write_cfg(tmp_path, raw))


def test_smtp_enabled_requires_recipient(tmp_path):
    raw = json.loads(json.dumps(VALID))
    raw["smtp"] = {"enabled": True, "host": "h", "user": "u", "password": "p", "to": []}
    with pytest.raises(ConfigError, match="收件人"):
        load_config(write_cfg(tmp_path, raw))


def test_login_parsed(tmp_path):
    raw = json.loads(json.dumps(VALID))
    raw["login"] = {"enabled": True, "username": "13800001234", "password": "secret"}
    cfg = load_config(write_cfg(tmp_path, raw))
    assert cfg.login.enabled is True
    assert cfg.login.username == "13800001234"
    assert cfg.login.password == "secret"


def test_login_enabled_requires_credentials(tmp_path):
    raw = json.loads(json.dumps(VALID))
    raw["login"] = {"enabled": True, "username": "", "password": ""}
    with pytest.raises(ConfigError, match="username"):
        load_config(write_cfg(tmp_path, raw))


def test_login_enabled_requires_password(tmp_path):
    raw = json.loads(json.dumps(VALID))
    raw["login"] = {"enabled": True, "username": "u", "password": ""}
    with pytest.raises(ConfigError, match="password"):
        load_config(write_cfg(tmp_path, raw))


def test_login_non_object_rejected(tmp_path):
    raw = json.loads(json.dumps(VALID))
    raw["login"] = "not-an-object"
    with pytest.raises(ConfigError, match="login"):
        load_config(write_cfg(tmp_path, raw))


def test_negative_daily_limit_rejected(tmp_path):
    raw = json.loads(json.dumps(VALID))
    raw["buy"]["daily_limit"] = -1
    with pytest.raises(ConfigError):
        load_config(write_cfg(tmp_path, raw))


def test_bad_delay_range_rejected(tmp_path):
    raw = json.loads(json.dumps(VALID))
    raw["search"]["random_delay_seconds"] = [9, 2]
    with pytest.raises(ConfigError):
        load_config(write_cfg(tmp_path, raw))


def test_human_readable_no_secrets(tmp_path):
    raw = json.loads(json.dumps(VALID))
    raw["smtp"]["enabled"] = True
    raw["smtp"]["host"] = "smtp.qq.com"
    raw["smtp"]["user"] = "secret@qq.com"
    raw["smtp"]["password"] = "SHOULD_NOT_APPEAR"
    raw["smtp"]["to"] = ["a@qq.com"]
    raw["ai"]["enabled"] = True
    raw["ai"]["api_key"] = "SK-SECRET"
    raw["ai"]["base_url"] = "https://api.deepseek.com/v1"
    raw["ai"]["model"] = "deepseek-chat"
    raw["login"] = {"enabled": True, "username": "13800001234", "password": "LOGIN_SECRET"}
    cfg = load_config(write_cfg(tmp_path, raw))
    text = __import__("core.config", fromlist=["human_readable"]).human_readable(cfg)
    assert "SHOULD_NOT_APPEAR" not in text
    assert "SK-SECRET" not in text
    assert "LOGIN_SECRET" not in text


def test_clawbot_default_disabled(tmp_path):
    cfg = load_config(write_cfg(tmp_path, VALID))
    assert cfg.clawbot.enabled is False


def test_clawbot_enabled_requires_chat_id(tmp_path):
    raw = json.loads(json.dumps(VALID))
    raw["clawbot"] = {"enabled": True, "chat_id": ""}
    with pytest.raises(ConfigError, match="chat_id"):
        load_config(write_cfg(tmp_path, raw))


def test_clawbot_enabled_requires_token_env(tmp_path, monkeypatch):
    monkeypatch.delenv("REASONIX_BOT_CONTROL_TOKEN", raising=False)
    raw = json.loads(json.dumps(VALID))
    raw["clawbot"] = {"enabled": True, "chat_id": "someone@im.wechat"}
    with pytest.raises(ConfigError, match="REASONIX_BOT_CONTROL_TOKEN"):
        load_config(write_cfg(tmp_path, raw))


def test_clawbot_enabled_with_token(tmp_path, monkeypatch):
    monkeypatch.setenv("REASONIX_BOT_CONTROL_TOKEN", "a" * 64)
    raw = json.loads(json.dumps(VALID))
    raw["clawbot"] = {"enabled": True, "chat_id": "someone@im.wechat"}
    cfg = load_config(write_cfg(tmp_path, raw))
    assert cfg.clawbot.enabled is True
    assert cfg.clawbot.chat_id == "someone@im.wechat"


def test_clawbot_non_object_rejected(tmp_path):
    raw = json.loads(json.dumps(VALID))
    raw["clawbot"] = "not-an-object"
    with pytest.raises(ConfigError, match="clawbot"):
        load_config(write_cfg(tmp_path, raw))
