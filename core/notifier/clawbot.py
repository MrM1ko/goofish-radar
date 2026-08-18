"""微信 clawbot 通知：通过 Reasonix bot control loopback API 推送。

复用 pipeline 传入的 subject/body，拼成一条微信 text 发送。
认证 token 从环境变量 REASONIX_BOT_CONTROL_TOKEN 读取，绝不写入配置。
发送失败只记录日志并返回 False，不影响主流程。
"""

from __future__ import annotations

import logging
import os

import requests

from core.settings import ClawbotConfig
from core.notifier.base import Notifier

logger = logging.getLogger(__name__)


class ClawbotNotifier(Notifier):
    name = "clawbot"

    def __init__(self, cfg: ClawbotConfig):
        self.cfg = cfg
        self.token = os.environ.get("REASONIX_BOT_CONTROL_TOKEN", "")

    def notify(self, subject: str, body: str) -> bool:
        if not self.cfg.enabled:
            logger.debug("微信通知未启用，跳过: %s", subject)
            return False

        if not self.token:
            logger.error("缺少环境变量 REASONIX_BOT_CONTROL_TOKEN，微信通知跳过: %s", subject)
            return False

        text = f"{subject}\n\n{body}"
        payload = {
            "connection_id": self.cfg.connection_id,
            "domain": self.cfg.domain,
            "chat_id": self.cfg.chat_id,
            "chat_type": self.cfg.chat_type,
            "text": text,
        }
        headers = {"Authorization": f"Bearer {self.token}"}

        try:
            resp = requests.post(
                f"{self.cfg.api_url.rstrip('/')}/send",
                json=payload,
                headers=headers,
                timeout=self.cfg.timeout_seconds,
            )
            resp.raise_for_status()
        except Exception as e:
            # 通知失败不应拖垮监控主流程，但要留下明确日志
            logger.error("微信发送失败 [%s]: %s", subject, e)
            return False

        logger.info("微信已发送 [%s]", subject)
        return True
