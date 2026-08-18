"""AI 语义过滤（可选层）。

调用 OpenAI 兼容接口（默认 DeepSeek）做复杂语义判断：
  复杂损坏描述 / 模糊商品状态 / 语义引流 / 商品本体错误。

任何失败（超时、HTTP 错误、JSON 解析失败、缺 API key）都降级：
  返回 ai_checked=False 的结果，由上层保留规则层判断，
  并在邮件中明确标记 "AI 检查：失败 / 未执行"。
"""

from __future__ import annotations

import json
import logging
import re

import requests

from core.settings import AiConfig, MonitorConfig
from core.filter.base import Filter
from core.models import DetailResult, FilterResult, Product

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "你是闲鱼二手商品筛选助手。根据用户给的监控关键词，判断该商品是否值得购买。"
    "只输出 JSON，不要输出任何其他内容。"
)

_JSON_SCHEMA_HINT = (
    '输出格式：{"damaged": false, "damaged_reason": "", "traction": false, '
    '"traction_reason": "", "wrong_item": false, "wrong_item_reason": ""}'
)


class AiFilter(Filter):
    name = "ai"

    def __init__(self, cfg: AiConfig):
        self.cfg = cfg

    @property
    def available(self) -> bool:
        return self.cfg.enabled and bool(self.cfg.api_key)

    def check(
        self,
        product: Product,
        detail: DetailResult | None,
        monitor: MonitorConfig,
    ) -> FilterResult:
        """返回的 FilterResult.ai_checked 恒为 False（表示未生效/失败），
        判断结果附加在 reasons 中供上层读取。"""
        if not self.available:
            return FilterResult(passed=True, reasons=["AI 未启用"], ai_checked=False)

        desc = (detail.desc if detail and detail.desc else "") or product.desc or ""
        user_prompt = (
            f"监控关键词: {monitor.keyword}\n"
            f"标题: {product.title}\n"
            f"描述: {desc[:2000]}\n"
            f"价格: {product.price}\n"
            f"要求: {_JSON_SCHEMA_HINT}"
        )

        try:
            raw = self._call(user_prompt)
            verdict = self._parse(raw)
        except Exception as e:  # 网络/解析等一切异常都走降级
            logger.warning("AI 检查失败，降级为规则层结果: %s", e)
            return FilterResult(
                passed=True,
                reasons=[f"AI 检查失败，按规则层结果处理（{type(e).__name__}）"],
                ai_checked=False,
                ai_notes=f"AI 检查失败: {e}",
            )

        rejected = [
            reason
            for flag, reason in (
                (verdict.get("damaged"), verdict.get("damaged_reason")),
                (verdict.get("traction"), verdict.get("traction_reason")),
                (verdict.get("wrong_item"), verdict.get("wrong_item_reason")),
            )
            if flag and reason
        ]
        if rejected:
            return FilterResult(
                passed=False,
                reasons=[f"AI 判定拒绝（{'；'.join(rejected)}）"],
                ai_checked=True,
            )
        return FilterResult(passed=True, reasons=["AI 未发现问题"], ai_checked=True)

    # ------------------------------------------------------------- 内部

    def _call(self, user_prompt: str) -> str:
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {self.cfg.api_key}"},
            json={
                "model": self.cfg.model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0,
            },
            timeout=self.cfg.timeout_seconds,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    @staticmethod
    def _parse(raw: str) -> dict:
        """从模型输出中提取 JSON 对象。

        模型偶尔会输出 ```json ... ``` 代码块或前后缀文字，这里做容错。
        """
        text = raw.strip()
        code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if code_block:
            text = code_block.group(1)
        else:
            brace = text.find("{")
            if brace > 0:
                text = text[brace:]
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"AI 返回内容无法解析为 JSON: {raw[:200]!r}") from e
        if not isinstance(data, dict):
            raise ValueError(f"AI 返回内容不是 JSON 对象: {raw[:200]!r}")
        return data
