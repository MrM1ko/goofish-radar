"""通知器抽象基类。

新增通知渠道（如 Telegram、ServerChan）时：
  1. 继承 Notifier 并实现 notify()；
  2. 在 pipeline 组装处注册即可，无需改动其他模块。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Notifier(ABC):
    name: str = "notifier"

    @abstractmethod
    def notify(self, subject: str, body: str) -> bool:
        """发送一条通知。返回是否成功（失败不应抛出异常）。"""
