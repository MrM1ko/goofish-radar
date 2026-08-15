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

    每个列表【首位】为 2026-08 通过 scripts/probe.py 实测命中的选择器，
    其余为页面改版时的降级候选。页面改版后请重新跑 probe.py 并更新首位。
    """

    # ------------------------------------------------------- 搜索页
    # 商品卡片（实测：a.feeds-item-wrap，30 个/页）
    item_card: list[str] = [
        "a.feeds-item-wrap--rGdH_KoF",
        'a[href*="/item?id="]',
        'a[href*="goofish.com/item"]',
    ]
    item_title: list[str] = [
        ".main-title--sMrtWSJa",
        '[class*="main-title"]',
    ]
    item_price: list[str] = [
        ".price-wrap--YzmU5cUl",
        '[class*="price-wrap"]',
    ]
    item_link: list[str] = [
        'a[href*="/item"]',
        'a[href*="item?id"]',
    ]
    # 排序（实测：列表上方筛选区有【排序维度】和【发布时间】两个控件，
    # "最新发布"= 发布时间控件选"最新"。发布时间控件可用其独有选项
    # "1天内"特征定位，页面改版也稳定）
    sort_button: list[str] = [
        '.search-select-container--ANusUe9S:has(.search-select-item--H_AJBURX:has-text("1天内"))',
        '[class*="search-select-title"]',
        "text=最新发布",
    ]
    sort_option_latest: list[str] = [
        '.search-select-item--H_AJBURX:has-text("最新")',
        '[class*="search-select-item"]:has-text("最新")',
    ]
    # 登录状态（实测：已登录页面 avatar 元素 30 个命中）
    logged_in_mark: list[str] = [
        '[class*="avatar"]',
        'img[class*="avatar"]',
        '[class*="userCenter"]',
        '[class*="my-account"]',
    ]
    login_entry: list[str] = [
        'text=登录',
        'a[href*="/login"]',
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
    # （2026-08 实测固化）
    detail_title: list[str] = [
        ".desc--GaIUKUQY span span",  # 描述区第一段即标题；亦可回退 page.title()
        '[class*="item-main-info"] [class*="title"]',
        "h1",
    ]
    detail_desc: list[str] = [
        ".desc--GaIUKUQY",
        '[class*="desc--"]',
    ]
    detail_price: list[str] = [
        ".price--OEWLbcxC",   # 实测：纯数字文本，¥ 符号在兄弟元素 symbol 中
        '[class*="price--"]',
    ]
    detail_post: list[str] = [
        ".post--eemp1Mym",    # 邮费（"包邮" 或金额）
    ]
    detail_status: list[str] = [
        'text=已下架',
        '[class*="soldOut"]',
    ]
    # 规格（多规格检测）：候选式，多规格商品待风控平息后实测固化
    sku_group: list[str] = [
        '[class*="sku"]',
        '[class*="spec"]',
        '[class*="Spec"]',
        '[class*="option"]',
    ]
    sku_item: list[str] = [
        '[class*="sku"] [class*="item"]',
        '[class*="spec"] [class*="item"]',
    ]
    # 下单（实测："立即购买" 是链接，直达 create-order?itemId=xxx）
    buy_now_button: list[str] = [
        "a.buy--MCbvZ6Lw",
        'a[href*="create-order"]',
        'text=立即购买',
    ]
    # 订单确认页（2026-08 实测：URL 为 /create-order?itemId=xxx）
    submit_order_button: list[str] = [
        "div.button--_ICQy2Ha",          # 实测：div 元素，文案"确认购买"
        'div:has-text("确认购买")',
        'text=提交订单',
        'button:has-text("提交订单")',
    ]
    order_total: list[str] = [
        ".money--eJruSjOm",             # 实测：合计金额 "¥3066.00"
        '[class*="money"]',
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
