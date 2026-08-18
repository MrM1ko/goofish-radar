"""goofish-radar 入口：常驻轮询，Ctrl+C 优雅退出。

用法：
    python run.py

启动流程：
  1. 日志初始化（data/logs/ 按日期滚动）；
  2. 单实例锁（防止启动项 + 手动重复启动）；
  3. 加载并校验配置；
  4. 启动浏览器会话，验证/引导登录；
  5. 常驻主循环：每轮执行 Pipeline.run_once()，间隔 poll_interval_minutes。
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))  # 支持任意工作目录启动

from core.config import (  # noqa: E402
    DATA_DIR,
    AppConfig,
    ConfigError,
    human_readable,
    load_config,
)
from core.dedupe import DedupeStore  # noqa: E402
from core.history import HistoryStore  # noqa: E402
from core.notifier.clawbot import ClawbotNotifier  # noqa: E402
from core.notifier.email import EmailNotifier  # noqa: E402
from core.orderer import Orderer, OrderStore  # noqa: E402
from core.pipeline import Pipeline  # noqa: E402
from core.runtime import RuntimeState, SingleInstanceLock  # noqa: E402

from browser.session import Session  # noqa: E402

logger = logging.getLogger("goofish-radar")


def setup_logging() -> None:
    """控制台 + 按日期滚动的文件日志（设计文档第 26 节）。"""
    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)

    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_dir / "run.log", when="midnight", backupCount=14, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(console)
    root.addHandler(file_handler)

    # 第三方库日志保持安静
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def build_components(cfg: AppConfig, session: Session) -> Pipeline:
    """组装全部组件（依赖注入，测试与替换实现都从这里改）。

    注意：调用前必须先 session.start()，组件依赖已启动的 page。
    """
    from browser.order import OrderCreator

    dedupe = DedupeStore(DATA_DIR / "seen.json")
    history = HistoryStore(DATA_DIR / "history.jsonl")
    order_store = OrderStore(DATA_DIR / "orders.json")
    runtime = RuntimeState(DATA_DIR / "runtime_state.json")

    order_creator = OrderCreator(session.page, session.selectors)
    orderer = Orderer(order_store, cfg.buy, order_creator)

    notifiers = []
    if cfg.smtp.enabled:
        notifiers.append(EmailNotifier(cfg.smtp))
    if cfg.clawbot.enabled:
        notifiers.append(ClawbotNotifier(cfg.clawbot))

    return Pipeline(
        cfg=cfg,
        dedupe=dedupe,
        history=history,
        orderer=orderer,
        notifiers=notifiers,
        runtime=runtime,
        session=session,
    )


def main() -> int:
    setup_logging()

    # 单实例锁（设计文档第 22 节）
    lock = SingleInstanceLock(DATA_DIR / "run.lock")
    if not lock.acquire():
        return 1
    try:
        try:
            cfg = load_config()
        except ConfigError as e:
            logger.error("配置错误: %s", e)
            return 1

        logger.info("=== goofish-radar 启动 ===")
        for line in human_readable(cfg).splitlines():
            logger.info("  %s", line)

        session = Session(
            storage_path=DATA_DIR / "storage_state.json",
            headless=cfg.search.headless,
        )
        session.start()
        pipeline = build_components(cfg, session)
        try:
            while True:
                if not session.ensure_logged_in():
                    # 扫码超时/放弃：本轮退出，等待下一轮再尝试
                    logger.warning("登录不可用，等待下一轮重试")
                    time.sleep(cfg.poll_interval_minutes * 60)
                    continue

                pipeline.run_once()

                next_run = datetime.now().timestamp() + cfg.poll_interval_minutes * 60
                logger.info("本轮完成，下一轮: %s", datetime.fromtimestamp(next_run).strftime("%H:%M:%S"))
                time.sleep(cfg.poll_interval_minutes * 60)
        finally:
            session.close()
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，优雅退出")
    finally:
        lock.release()
        logger.info("=== goofish-radar 已退出 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
