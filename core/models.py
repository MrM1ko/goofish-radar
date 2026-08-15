"""数据模型。

所有跨模块传递的核心数据结构集中定义在这里，
保持模块之间只依赖这些纯数据结构，方便替换实现与测试。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Product:
    """搜索页提取的商品信息。

    matched_keywords 记录该商品命中了哪些 monitor 关键词，
    同一个商品可能命中多个关键词，但全局去重后只处理一次。
    """

    item_id: str
    title: str
    price: float
    url: str
    publish_time: str | None = None
    desc: str | None = None
    matched_keywords: list[str] = field(default_factory=list)


@dataclass
class DetailResult:
    """详情页读取结果。"""

    title: str | None = None
    desc: str | None = None
    price: float | None = None
    postage: str | None = None        # 邮费（"包邮" / 金额文本）
    status: str | None = None            # 商品状态，如"已下架"
    has_sku: bool = False
    sku_count: int = 0
    seller_info: str | None = None
    failed: bool = False
    error: str | None = None


@dataclass
class FilterResult:
    """三层过滤（身份/规则/AI）的汇总结果。"""

    passed: bool
    reasons: list[str] = field(default_factory=list)
    ai_checked: bool = False             # AI 是否真正执行过检查（失败/未启用均为 False）
    ai_notes: str | None = None          # AI 拒绝或异常时的补充说明


@dataclass
class OrderResult:
    """下单结果。

    status 只允许三种取值：
      success —— 明确看到订单号或待付款状态；
      failed  —— 明确看到商品下架/无法购买/提交失败；
      unknown —— 点击提交后无法确认订单是否生成。
    """

    status: str
    order_id: str | None = None
    reason: str | None = None


@dataclass
class SeenRecord:
    """seen.json 中单条商品的去重记录。"""

    first_seen: str
    last_seen: str
    last_price: float
    title: str
    matched_keywords: list[str] = field(default_factory=list)

    @classmethod
    def now(cls, product: Product) -> "SeenRecord":
        ts = datetime.now().isoformat(timespec="seconds")
        return cls(
            first_seen=ts,
            last_seen=ts,
            last_price=product.price,
            title=product.title,
            matched_keywords=list(product.matched_keywords),
        )
