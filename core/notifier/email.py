"""邮件通知：SMTP + SSL/STARTTLS。

使用标准库 smtplib + email，无额外依赖。
发送失败只记录日志并返回 False，不影响主流程。
"""

from __future__ import annotations

import logging
import smtplib
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

from core.config import SmtpConfig
from core.notifier.base import Notifier

logger = logging.getLogger(__name__)


class EmailNotifier(Notifier):
    name = "email"

    def __init__(self, cfg: SmtpConfig):
        self.cfg = cfg

    def notify(self, subject: str, body: str) -> bool:
        if not self.cfg.enabled:
            logger.debug("邮件通知未启用，跳过: %s", subject)
            return False

        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = formataddr((str(Header("闲鱼雷达", "utf-8")), self.cfg.user))
        msg["To"] = ", ".join(self.cfg.to)

        try:
            if self.cfg.use_ssl:
                server = smtplib.SMTP_SSL(self.cfg.host, self.cfg.port, timeout=30)
            else:
                server = smtplib.SMTP(self.cfg.host, self.cfg.port, timeout=30)
                server.starttls()
            with server:
                server.login(self.cfg.user, self.cfg.password)
                server.sendmail(self.cfg.user, self.cfg.to, msg.as_string())
        except Exception as e:
            # 通知失败不应拖垮监控主流程，但要留下明确日志
            logger.error("邮件发送失败 [%s]: %s", subject, e)
            return False

        logger.info("邮件已发送 [%s] → %s", subject, ", ".join(self.cfg.to))
        return True
