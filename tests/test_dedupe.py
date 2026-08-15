"""dedupe 单元测试：全局 item_id 去重。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.dedupe import DedupeStore
from core.models import Product


def make_product(item_id: str, **kwargs) -> Product:
    return Product(item_id=item_id, title=f"t{item_id}", price=1.0, url=f"u{item_id}", **kwargs)


def test_empty_store_is_new(tmp_path):
    store = DedupeStore(tmp_path / "seen.json")
    assert store.is_empty() is True
    assert store.is_new("123") is True


def test_mark_seen_then_not_new(tmp_path):
    store = DedupeStore(tmp_path / "seen.json")
    store.mark_seen(make_product("123"))
    assert store.is_new("123") is False
    assert store.is_new("456") is True


def test_persist_and_reload(tmp_path):
    path = tmp_path / "seen.json"
    store = DedupeStore(path)
    store.mark_seen(make_product("123", matched_keywords=["a", "b"]))
    store.mark_seen(make_product("123", matched_keywords=["b", "c"]))
    store.save()

    reloaded = DedupeStore(path)
    assert reloaded.is_new("123") is False
    assert len(reloaded) == 1
    record = reloaded.record("123")
    assert record is not None
    # 相同商品多次 mark_seen 应合并 matched_keywords
    assert set(record.matched_keywords) == {"a", "b", "c"}


def test_first_seen_kept_on_update(tmp_path):
    store = DedupeStore(tmp_path / "seen.json")
    store.mark_seen(make_product("123"))
    first = store.record("123").first_seen
    store.mark_seen(make_product("123"))
    assert store.record("123").first_seen == first


def test_corrupted_file_treated_as_empty(tmp_path):
    path = tmp_path / "seen.json"
    path.write_text("{broken json", encoding="utf-8")
    store = DedupeStore(path)
    assert store.is_empty() is True
    assert store.is_new("123") is True


def test_global_dedupe_across_keywords(tmp_path):
    """不同关键词命中同一 item_id 只算一次（全局去重核心）。"""
    store = DedupeStore(tmp_path / "seen.json")
    store.mark_seen(make_product("999", matched_keywords=["iPhone 15"]))
    assert store.is_new("999") is False  # 换一个关键词也仍然不是新商品
    store.mark_seen(make_product("999", matched_keywords=["苹果15"]))
    assert set(store.record("999").matched_keywords) == {"iPhone 15", "苹果15"}
