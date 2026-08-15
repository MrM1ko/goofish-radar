"""所有页面选择器集中管理。

闲鱼页面结构可能随时调整（设计文档第 32 节），因此：
  1. 每个元素提供【候选选择器列表】，按顺序尝试，全部失效时
     自动保存页面现场到 data/debug/ 并抛出 SelectorError；
  2. 页面改版时只需修改本文件，业务代码不动（设计文档第 29 节）；
  3. 候选内容需通过 scripts/probe.py 实测后固化，当前为经验值。

pick() 辅助函数：按候选顺序返回页面上第一个可见元素。
"""

from __future__ import annotations

from typing import Iterable

from playwright.sync_api import Locator, Page


class SelectorError(Exception):
    """所有候选选择器均未命中。"""


def pick(page: Page, candidates: Iterable[str], label: str) -> Locator:
    """按候选顺序查找第一个可见元素。

    找不到时保存现场并抛出 SelectorError，
    方便事后用 data/debug/ 下的快照修复选择器。
    """
    for css in candidates:
        locator = page.locator(css)
        try:
            if locator.count() > 0:
                return locator.first
        except Exception:
            continue
    _save_debug_snapshot(page, label)
    raise SelectorError(f"选择器失效 [{label}]: 尝试过 {list(candidates)}")


def _save_debug_snapshot(page: Page, label: str) -> None:
    """异常现场保存：截图 + HTML。"""
    from datetime import datetime

    from core.config import DATA_DIR

    debug_dir = DATA_DIR / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_label = "".join(c if c.isalnum() or c in "_-" else "_" for c in label)
    try:
        page.screenshot(path=str(debug_dir / f"{stamp}_{safe_label}.png"), full_page=True)
    except Exception:
        pass
    try:
        (debug_dir / f"{stamp}_{safe_label}.html").write_text(
            page.content(), encoding="utf-8"
        )
    except Exception:
        pass


class Selectors:
    """闲鱼各页面元素候选选择器。

    ⚠️ 以下均为经验候选，必须先用 scripts/probe.py 实测确认，
    命中后把实测选择器移到各自列表第一位。
    """

    # ------------------------------------------------------- 搜索页
    # 商品卡片
    item_card: list[str] = [
        '[class*="card"] a[href*="/item?"]',
        'a[href*="goofish.com/item"]',
        'a[href*="/item?id="]',
    ]
    item_title: list[str] = [
        '[class*="title"]',
        '[class*="CardContent_title"]',
        "h3",
    ]
    item_price: list[str] = [
        '[class*="price"]',
        '[class*="Price_price"]',
    ]
    item_link: list[str] = [
        'a[href*="/item"]',
        'a[href*="item?id"]',
    ]
    # 排序：最新发布
    sort_button: list[str] = [
        'text=最新发布',
        '[class*="sort"] >> text=最新发布',
    ]
    # 登录状态
    logged_in_mark: list[str] = [
        '[class*="avatar"]',
        '[class*="user"]',
        'img[class*="avatar"]',
    ]
    login_entry: list[str] = [
        'text=登录',
        '[class*="login"]',
    ]
    # 风控
    captcha_mark: list[str] = [
        '[class*="captcha"]',
        '[id*="captcha"]',
        'iframe[src*="captcha"]',
        'text=安全验证',
    ]
    slider_mark: list[str] = [
        '[class*="slider"]',
        '[class*="nc_iconfont"]',
    ]

    # ------------------------------------------------------- 详情页
    detail_title: list[str] = [
        '[class*="detail"] [class*="title"]',
        '[class*="Detail"] h1',
        "h1",
    ]
    detail_desc: list[str] = [
        '[class*="desc"]',
        '[class*="content"]',
    ]
    detail_price: list[str] = [
        '[class*="price"]',
        '[class*="Price"]',
    ]
    detail_status: list[str] = [
        'text=已下架',
        '[class*="soldOut"]',
        '[class*="status"]',
    ]
    # 规格（多规格检测）
    sku_group: list[str] = [
        '[class*="sku"]',
        '[class*="spec"]',
        '[class*="Spec"]',
    ]
    sku_item: list[str] = [
        '[class*="sku"] [class*="item"]',
        '[class*="spec"] [class*="item"]',
    ]
    # 下单
    buy_now_button: list[str] = [
        'text=立即购买',
        '[class*="buy"] >> text=立即购买',
        'button:has-text("立即购买")',
    ]
    # 订单确认页
    submit_order_button: list[str] = [
        'text=提交订单',
        'button:has-text("提交订单")',
        '[class*="submit"] >> text=提交订单',
    ]
    order_pending_mark: list[str] = [
        'text=待付款',
        '[class*="pending"]',
    ]
    order_success_mark: list[str] = [
        'text=订单提交成功',
        'text=等待买家付款',
        'text=待付款',
    ]

    # ------------------------------------------------------- 通用
    # 需要登录后才会出现的元素（用于判断是否已登录）
    logout_entry: list[str] = [
        'text=退出登录',
        '[class*="logout"]',
    ]
