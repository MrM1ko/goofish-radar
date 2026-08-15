"""配置加载与校验。

从 config/config.json 读取全部配置，解析为强类型 dataclass，
启动时做一次完整校验，避免运行中途才发现配置错误。

所有文件路径都相对项目根目录解析，保证项目整体目录可整体迁移。
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"

CONFIG_FILE = CONFIG_DIR / "config.json"
CONFIG_EXAMPLE_FILE = CONFIG_DIR / "config.example.json"

NEGATIVE_WORDS_FILE = CONFIG_DIR / "negative_words.txt"
TRACTION_WORDS_FILE = CONFIG_DIR / "traction_words.txt"
INVALID_ITEM_WORDS_FILE = CONFIG_DIR / "invalid_item_words.txt"


class ConfigError(Exception):
    """配置缺失或非法。"""


@dataclass
class SearchConfig:
    sort: str = "time"
    max_scan_items: int = 50
    headless: bool = True
    random_delay_seconds: list[int] = field(default_factory=lambda: [3, 8])
    screenshot: bool = False


@dataclass
class MonitorConfig:
    name: str
    keyword: str
    enabled: bool = True
    auto_order: bool = False
    max_price: float | None = None
    exclude_words: list[str] = field(default_factory=list)


@dataclass
class BuyConfig:
    enabled: bool = True
    daily_limit: int = 3
    order_interval_minutes: int = 20


@dataclass
class AiConfig:
    enabled: bool = False
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    timeout_seconds: int = 20


@dataclass
class SmtpConfig:
    enabled: bool = False
    host: str = ""
    port: int = 465
    use_ssl: bool = True
    user: str = ""
    password: str = ""
    to: list[str] = field(default_factory=list)


@dataclass
class AppConfig:
    poll_interval_minutes: int = 5
    search: SearchConfig = field(default_factory=SearchConfig)
    monitors: list[MonitorConfig] = field(default_factory=list)
    buy: BuyConfig = field(default_factory=BuyConfig)
    ai: AiConfig = field(default_factory=AiConfig)
    smtp: SmtpConfig = field(default_factory=SmtpConfig)

    def enabled_monitors(self) -> list[MonitorConfig]:
        return [m for m in self.monitors if m.enabled]


# ---------------------------------------------------------------- 加载与校验


def load_config(path: Path | None = None) -> AppConfig:
    """加载并校验配置文件。

    文件不存在时抛出 ConfigError 并提示复制示例配置。
    """
    path = path or CONFIG_FILE
    if not path.exists():
        raise ConfigError(
            f"配置文件不存在: {path}\n"
            f"请先复制 {CONFIG_EXAMPLE_FILE.name} 为 {CONFIG_FILE.name} 并填写真实配置。"
        )
    try:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"配置文件不是合法 JSON: {e}") from e

    cfg = _parse(raw)
    _validate(cfg)
    return cfg


def _parse(raw: dict[str, Any]) -> AppConfig:
    def _get_int(section: str, key: str, default: int, minimum: int = 0) -> int:
        value = raw.get(section, {}).get(key, default)
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            raise ConfigError(f"{section}.{key} 必须是不小于 {minimum} 的整数")
        return value

    search = raw.get("search", {})
    if not isinstance(search, dict):
        raise ConfigError("search 必须是一个对象")
    delay = search.get("random_delay_seconds", [3, 8])
    if not isinstance(delay, list) or len(delay) != 2 or not all(isinstance(x, int) for x in delay):
        raise ConfigError("search.random_delay_seconds 必须是 [最小秒, 最大秒] 两个整数")

    monitors_raw = raw.get("monitors", [])
    if not isinstance(monitors_raw, list) or not monitors_raw:
        raise ConfigError("monitors 必须是非空数组")
    monitors: list[MonitorConfig] = []
    for item in monitors_raw:
        if not isinstance(item, dict):
            raise ConfigError("monitors 中每一项都必须是对象")
        name = item.get("name")
        keyword = item.get("keyword")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("每个 monitor 必须提供非空 name")
        if not isinstance(keyword, str) or not keyword.strip():
            raise ConfigError(f"monitor {name!r} 必须提供非空 keyword")
        max_price = item.get("max_price")
        if max_price is not None and not isinstance(max_price, (int, float)):
            raise ConfigError(f"monitor {name!r} 的 max_price 必须是数字")
        exclude = item.get("exclude_words", [])
        if not isinstance(exclude, list) or not all(isinstance(w, str) for w in exclude):
            raise ConfigError(f"monitor {name!r} 的 exclude_words 必须是字符串数组")
        monitors.append(
            MonitorConfig(
                name=name.strip(),
                keyword=keyword.strip(),
                enabled=bool(item.get("enabled", True)),
                auto_order=bool(item.get("auto_order", False)),
                max_price=float(max_price) if max_price is not None else None,
                exclude_words=[w.strip() for w in exclude],
            )
        )

    ai_raw = raw.get("ai", {})
    smtp_raw = raw.get("smtp", {})
    if not isinstance(ai_raw, dict) or not isinstance(smtp_raw, dict):
        raise ConfigError("ai 和 smtp 必须是对象")
    to = smtp_raw.get("to", [])
    if not isinstance(to, list) or not all(isinstance(x, str) for x in to):
        raise ConfigError("smtp.to 必须是字符串数组")

    port_raw = smtp_raw.get("port", 465)
    if not isinstance(port_raw, int) or isinstance(port_raw, bool):
        raise ConfigError("smtp.port 必须是整数")

    return AppConfig(
        poll_interval_minutes=raw.get("poll_interval_minutes", 5),
        search=SearchConfig(
            sort=search.get("sort", "time"),
            max_scan_items=_get_int("search", "max_scan_items", 50, 1),
            headless=bool(search.get("headless", True)),
            random_delay_seconds=delay,
            screenshot=bool(search.get("screenshot", False)),
        ),
        monitors=monitors,
        buy=BuyConfig(
            enabled=bool(raw.get("buy", {}).get("enabled", True)),
            daily_limit=_get_int("buy", "daily_limit", 3, 0),
            order_interval_minutes=_get_int("buy", "order_interval_minutes", 20, 0),
        ),
        ai=AiConfig(
            enabled=bool(ai_raw.get("enabled", False)),
            base_url=str(ai_raw.get("base_url", "")),
            model=str(ai_raw.get("model", "")),
            api_key=str(ai_raw.get("api_key", "")),
            timeout_seconds=_get_int("ai", "timeout_seconds", 20, 1),
        ),
        smtp=SmtpConfig(
            enabled=bool(smtp_raw.get("enabled", True)),
            host=str(smtp_raw.get("host", "")),
            port=port_raw,
            use_ssl=bool(smtp_raw.get("use_ssl", True)),
            user=str(smtp_raw.get("user", "")),
            password=str(smtp_raw.get("password", "")),
            to=[str(x) for x in to],
        ),
    )


def _validate(cfg: AppConfig) -> None:
    names = [m.name for m in cfg.monitors]
    if len(names) != len(set(names)):
        raise ConfigError(f"monitor name 必须唯一，当前: {names}")

    if not isinstance(cfg.poll_interval_minutes, int) or isinstance(cfg.poll_interval_minutes, bool):
        raise ConfigError("poll_interval_minutes 必须是整数")
    if not 1 <= cfg.poll_interval_minutes <= 1440:
        raise ConfigError("poll_interval_minutes 必须在 1~1440 之间")

    delay_min, delay_max = cfg.search.random_delay_seconds
    if delay_min < 0 or delay_max < delay_min:
        raise ConfigError("random_delay_seconds 必须满足 0 <= 最小 <= 最大")

    if cfg.ai.enabled:
        missing = [k for k, v in (("base_url", cfg.ai.base_url), ("model", cfg.ai.model), ("api_key", cfg.ai.api_key)) if not v]
        if missing:
            raise ConfigError(f"ai.enabled=true 但缺少配置: {', '.join(missing)}")

    if cfg.smtp.enabled:
        missing = [k for k, v in (("host", cfg.smtp.host), ("user", cfg.smtp.user), ("password", cfg.smtp.password)) if not v]
        if missing:
            raise ConfigError(f"smtp.enabled=true 但缺少配置: {', '.join(missing)}")
        if not cfg.smtp.to:
            raise ConfigError("smtp.to 收件人列表为空")


def human_readable(cfg: AppConfig) -> str:
    """生成不泄露密钥的配置摘要，用于启动日志。"""
    lines = [
        f"轮询间隔: {cfg.poll_interval_minutes} 分钟",
        f"扫描上限: {cfg.search.max_scan_items} 条",
        f"监控任务: {len(cfg.enabled_monitors())}/{len(cfg.monitors)} 个启用",
        f"拍单: {'开启' if cfg.buy.enabled else '关闭'} "
        f"(每日上限 {cfg.buy.daily_limit} 单, 间隔 {cfg.buy.order_interval_minutes} 分钟)",
        f"AI 过滤: {'开启 (' + cfg.ai.model + ')' if cfg.ai.enabled else '关闭'}",
        f"邮件通知: {'开启' if cfg.smtp.enabled else '关闭'}",
    ]
    for m in cfg.enabled_monitors():
        auto = "自动拍" if m.auto_order else "仅通知"
        price = f"阈值 {m.max_price}" if m.max_price is not None else "无价格阈值"
        lines.append(f"  - {m.name}: {m.keyword!r} [{auto}, {price}]")
    return "\n".join(lines)


if __name__ == "__main__":
    try:
        cfg = load_config()
    except ConfigError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    print(human_readable(cfg))
