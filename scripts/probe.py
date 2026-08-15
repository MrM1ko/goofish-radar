"""页面结构探测脚本（设计文档阶段 2）。

用途：在真实页面上一一验证 selectors.py 中的候选选择器，
输出命中报告 + 商品区真实 DOM 结构样本，用于固化到 browser/selectors.py。

用法（无头模式无法扫码，先确认 storage_state 已存在）：
    python scripts/probe.py [关键词]

输出：控制台报告 + data/debug/ 下的页面快照。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from browser.selectors import Selectors  # noqa: E402
from browser.session import Session  # noqa: E402
from core.config import DATA_DIR  # noqa: E402


def _wait_cards(page, timeout_s: int = 25) -> bool:
    """等待商品卡片区渲染完成（网络空闲后再补等）。"""
    import time

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            # 商品链接特征：/item 或 detail 页面
            n = page.locator('a[href*="/item"]').count()
            if n > 0:
                return True
        except Exception:
            pass
        page.wait_for_timeout(1000)
    return False


def probe_search_page(session: Session, keyword: str) -> None:
    page = session.page
    url = f"https://www.goofish.com/search?q={keyword}"
    print(f"\n[1] 打开搜索页: {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    ready = _wait_cards(page)
    print(f"    商品卡片渲染: {'已出现' if ready else '未等到（可能未登录/空结果/结构变化）'}")
    print(f"    当前 URL: {page.url}")
    print(f"    页面标题: {page.title()}")

    print("\n[2] 登录状态探测:")
    if session._has_login_cookie():
        print("    Cookie 判定: 已登录")
    for css in session.selectors.logged_in_mark:
        try:
            n = page.locator(css).count()
            print(f"    logged_in_mark {css!r}: {n} 个命中")
        except Exception as e:
            print(f"    logged_in_mark {css!r}: 异常 {e}")

    print("\n[3] 风控探测:")
    found = False
    for css in session.selectors.captcha_mark + session.selectors.slider_mark:
        try:
            if page.locator(css).count() > 0:
                print(f"    ⚠️ 疑似风控: {css!r}")
                found = True
        except Exception:
            pass
    if not found:
        print("    未发现验证码/滑块")

    print("\n[4] 商品卡片候选选择器命中情况:")
    for css in session.selectors.item_card:
        try:
            n = page.locator(css).count()
            print(f"    item_card {css!r}: {n} 个命中")
        except Exception as e:
            print(f"    item_card {css!r}: 异常 {e}")

    print("\n[5] 商品区真实链接样本（a[href*=/item]）:")
    try:
        links = page.locator('a[href*="/item"]')
        n = links.count()
        print(f"    共 {n} 条")
        for i in range(min(n, 10)):
            loc = links.nth(i)
            try:
                cls = loc.get_attribute("class") or ""
                href = loc.get_attribute("href") or ""
                print(f"    [{i}] class={cls!r}")
                print(f"        href={href}")
            except Exception as e:
                print(f"    [{i}] 读取失败: {e}")
    except Exception as e:
        print(f"    定位失败: {e}")

    print("\n[6] 商品容器结构样本（DOM 向上 3 层 class）:")
    try:
        first = page.locator('a[href*="/item"]').first
        if first.count() > 0:
            js = """
            el => {
                const chain = [];
                let node = el;
                for (let i = 0; i < 4 && node; i++) {
                    chain.push(`${node.tagName}.${node.className || ''}`);
                    node = node.parentElement;
                }
                return chain;
            }
            """
            chain = first.evaluate(js)
            for level, desc in enumerate(chain):
                print(f"    L{level}: {desc}")
    except Exception as e:
        print(f"    结构读取失败: {e}")

    print("\n[7] 排序按钮:")
    for css in session.selectors.sort_button:
        try:
            n = page.locator(css).count()
            print(f"    sort_button {css!r}: {n} 个命中")
        except Exception as e:
            print(f"    sort_button {css!r}: 异常 {e}")

    print("\n[8] 保存现场快照到 data/debug/")
    stamp = Path(DATA_DIR / "debug" / "probe_search.html")
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(page.content(), encoding="utf-8")
    print(f"    {stamp}")

    print("\n探测完成。请把命中的选择器固化到 browser/selectors.py 对应列表首位。")


def main() -> None:
    keyword = sys.argv[1] if len(sys.argv) > 1 else "iPhone 15"
    session = Session(storage_path=DATA_DIR / "storage_state.json", headless=False)
    session.start()
    try:
        if not session.ensure_logged_in():
            print("登录失败/超时，无法继续探测")
            return
        probe_search_page(session, keyword)
    finally:
        session.close()


if __name__ == "__main__":
    main()
