from datetime import timedelta

from summary_branch.domain.models import (
    Banner, BannerDTO, ContentKey, Decision, Severity, utcnow,
)
import pytest

from summary_branch.infrastructure.sqlite_store import SqliteBannerStore
from summary_branch.infrastructure.stores import InMemoryBannerStore, InMemorySummaryCache


@pytest.fixture(params=["memory", "sqlite"])
def new_store(request, tmp_path):
    """Один набор тестов на оба хранилища: контракт BannerStore должен выполняться одинаково."""
    stores = []

    def make():
        store = InMemoryBannerStore() if request.param == "memory" else SqliteBannerStore(tmp_path / "banners.db")
        stores.append(store)
        return store

    yield make
    for store in stores:
        if isinstance(store, SqliteBannerStore):
            store.close()



def key(cluster="c1"):
    return ContentKey(cluster_id=cluster, prompt_version="v1")


def banner(bid, topic="rate", company="co1", cluster="c1", confidence=0.9, severity=Severity.ATTENTION,
           expires_in_h=36, created_ago_h=0, decision=Decision.SHOW_SUMMARY):
    now = utcnow()
    return Banner(banner_id=bid, company_id=company, key=key(cluster), topic=topic, severity=severity,
                  for_you="f", confidence=confidence, decision=decision,
                  created_at=now - timedelta(hours=created_ago_h), expires_at=now + timedelta(hours=expires_in_h))


def dto(b: Banner) -> BannerDTO:
    return BannerDTO(banner_id=b.banner_id, severity=b.severity, label="l", headline=b.banner_id, for_you="f",
                     sources=[], expires_at=b.expires_at)


def put(store, *banners):
    for b in banners:
        store.put(b, dto(b))


def ids(dtos):
    return [d.banner_id for d in dtos]


def test_top_k_skips_expired_and_suppressed(new_store):
    store = new_store()
    put(store, banner("ok", topic="rate"), banner("old", topic="fx", expires_in_h=-1),
        banner("hidden", topic="labor"))
    store.suppress("co1", "hidden", "не про меня")
    assert ids(store.top_k("co1", 3)) == ["ok"]


def test_top_k_diversifies_topics_when_alternatives_exist(new_store):
    store = new_store()
    put(store, banner("r1", "rate"), banner("r2", "rate"), banner("r3", "rate"),
        banner("l1", "labor", severity=Severity.WATCH, confidence=0.8))
    result = ids(store.top_k("co1", 3))
    assert "l1" in result and len(result) == 3


def test_mark_seen_lowers_priority(new_store):
    store = new_store()
    put(store, banner("a", "rate", confidence=0.95), banner("b", "labor", confidence=0.9))
    assert ids(store.top_k("co1", 1)) == ["a"]
    store.mark_seen("co1", "a")
    assert ids(store.top_k("co1", 1)) == ["b"]


def test_put_is_idempotent_and_keeps_state(new_store):
    store = new_store()
    b = banner("a")
    put(store, b, b)
    assert ids(store.top_k("co1", 3)) == ["a"]
    store.suppress("co1", "a", "неточно")
    put(store, b)
    assert store.top_k("co1", 3) == []


def test_put_returns_true_only_for_new_banner_id(new_store):
    store = new_store()
    b = banner("a")
    assert store.put(b, dto(b)) is True
    assert store.put(b, dto(b)) is False
    assert store.put(banner("a", company="co2"), dto(b)) is True      # у другой компании баннер новый
    store.invalidate_cluster("c1", "новое событие")
    assert store.put(b, dto(b)) is True                                # после снятия снова новый


def test_top_k_is_isolated_per_company(new_store):
    store = new_store()
    put(store, banner("a", company="co1"), banner("b", company="co2"))
    assert ids(store.top_k("co2", 3)) == ["b"]
    assert store.top_k("nobody", 3) == []


def test_invalidate_cluster_removes_for_all_companies_and_counts(new_store):
    store = new_store()
    put(store, banner("a", company="co1", cluster="c1"), banner("b", company="co2", cluster="c1", topic="fx"),
        banner("c", company="co1", cluster="c2", topic="labor"))
    assert store.invalidate_cluster("c1", "новое событие") == 2
    assert ids(store.top_k("co1", 3)) == ["c"]
    assert store.top_k("co2", 3) == []
    assert store.invalidate_cluster("c1", "повтор") == 0


def test_segment_cache_ttl_and_hit_counter():
    now = [utcnow()]
    cache = InMemorySummaryCache(clock=lambda: now[0])
    k = key()
    assert cache.get(k) is None
    item = _summary(k)
    cache.put(item, ttl_s=60)
    assert cache.get(k) == item
    now[0] += timedelta(seconds=61)
    assert cache.get(k) is None
    assert (cache.hits, cache.misses) == (1, 2)


def _summary(k):
    from summary_branch.domain.models import ContentSignals, LeadRef, ClusterSummary
    signals = ContentSignals(grounding=1, source_quality=1, claim_mix=1, faithfulness=1, coverage=1,
                             specificity=1, brevity=1, structure_ok=True, sqs=1)
    lead = LeadRef(cluster_id=k.cluster_id, published_at=utcnow(), rerank_score=0.5)
    return ClusterSummary(key=k, model_id="m", headline="h", for_you_template="", for_you_fallback="f", topic="rate",
                          severity=Severity.WATCH, claims=[], sources=[], lead=lead, signals=signals)


def test_put_returns_false_for_suppressed_banner_so_it_is_not_pushed(new_store):
    store = new_store()
    b = banner("a")
    put(store, b)
    store.suppress("co1", "a", "не про меня")
    store.invalidate_cluster("c1", "новое событие")
    assert store.put(b, dto(b)) is False        # тот же banner_id после обновления кластера остаётся скрытым
    assert store.top_k("co1", 3) == []


def test_invalidate_cluster_resets_seen_state(new_store):
    store = new_store()
    put(store, banner("a", "rate", confidence=0.95), banner("b", "labor", confidence=0.9))
    store.mark_seen("co1", "a")
    assert ids(store.top_k("co1", 1)) == ["b"]
    store.invalidate_cluster("c1", "новое событие")
    put(store, banner("a", "rate", confidence=0.95))
    assert ids(store.top_k("co1", 1)) == ["a"]  # свежий баннер не наследует штраф за «просмотрено»


def test_returned_objects_are_copies(new_store):
    store = new_store()
    put(store, banner("a"))
    store.top_k("co1", 1)[0].headline = "испорчено"
    assert store.top_k("co1", 1)[0].headline == "a"

    cache = InMemorySummaryCache()
    k = key()
    cache.put(_summary(k), ttl_s=60)
    cache.get(k).headline = "испорчено"
    assert cache.get(k).headline == "h"


def test_expired_banners_are_purged_on_put():
    store = InMemoryBannerStore()
    put(store, banner("old", expires_in_h=-1, topic="fx"))
    put(store, banner("fresh"))
    assert ("co1", "old") not in store._dtos and "old" not in store._banners["co1"]


def test_concurrent_access_does_not_break_store(new_store):
    from concurrent.futures import ThreadPoolExecutor
    store = new_store()

    def worker(i):
        for j in range(200):
            b = banner(f"b{i}-{j % 20}", topic=f"t{j % 5}", cluster=f"c{j % 7}")
            store.put(b, dto(b))
            store.top_k("co1", 3)
            store.mark_seen("co1", b.banner_id)
            if j % 25 == 0:
                store.invalidate_cluster(f"c{j % 7}", "тест")

    with ThreadPoolExecutor(8) as pool:
        for future in [pool.submit(worker, i) for i in range(8)]:
            future.result()
    assert len(store.top_k("co1", 3)) <= 3


def test_summary_cache_invalidate_cluster_drops_all_prompt_versions():
    cache = InMemorySummaryCache()
    keys = [ContentKey(cluster_id="c1", prompt_version=v) for v in ("v1", "v2")]
    other = ContentKey(cluster_id="c2", prompt_version="v1")
    for k in (*keys, other):
        cache.put(_summary(k), ttl_s=60)
    assert cache.invalidate_cluster("c1") == 2
    assert all(cache.get(k) is None for k in keys) and cache.get(other) is not None
    assert cache.invalidate_cluster("c1") == 0


def test_get_returns_only_live_visible_banner(new_store):
    store = new_store()
    put(store, banner("ok"), banner("old", expires_in_h=-1), banner("hidden"))
    store.suppress("co1", "hidden", "не про меня")
    assert store.get("co1", "ok").banner_id == "ok"
    assert store.get("co1", "old") is None and store.get("co1", "hidden") is None
    assert store.get("co1", "missing") is None and store.get("co2", "ok") is None


def test_sqlite_store_survives_restart(tmp_path):
    path = tmp_path / "nested" / "banners.db"
    first = SqliteBannerStore(path)
    put(first, banner("a", "rate", confidence=0.95), banner("b", "labor", confidence=0.9), banner("c", "fx"))
    first.mark_seen("co1", "a")
    first.suppress("co1", "c", "не про меня")
    first.close()

    second = SqliteBannerStore(path)
    assert second.get("co1", "c") is None                       # скрытие пережило перезапуск
    assert ids(second.top_k("co1", 1)) == ["b"]                 # «показан» тоже: a ушёл ниже
    assert second.put(banner("a"), dto(banner("a"))) is False   # баннер уже есть, пушить не нужно
    second.close()


def test_sqlite_store_keeps_same_banner_id_separate_per_company(new_store):
    store = new_store()
    first, second = banner("a", company="co1"), banner("a", company="co2")
    store.put(first, dto(first).model_copy(update={"headline": "first"}))
    store.put(second, dto(second).model_copy(update={"headline": "second"}))
    assert store.get("co1", "a").headline == "first" and store.get("co2", "a").headline == "second"


def test_hide_removes_banner_without_negative_feedback_and_counts_it(new_store):
    store = new_store()
    put(store, banner("a1"), banner("w1", topic="fx", severity=Severity.WATCH), banner("n1", topic="labor"),
        banner("old", topic="demand", expires_in_h=-1))
    store.hide("co1", "a1")
    store.suppress("co1", "n1", "не про меня")
    assert store.is_hidden("co1", "a1") and store.is_hidden("co1", "n1") and not store.is_hidden("co1", "w1")
    assert ids(store.top_k("co1", 3)) == ["w1"]
    assert store.counts("co1") == {"attention": 0, "watch": 1, "opportunity": 0, "hidden": 1}


def test_counts_cover_all_live_banners_not_only_top_k(new_store):
    store = new_store()
    put(store, *(banner(f"b{i}", topic=f"t{i}") for i in range(5)))
    assert len(store.top_k("co1", 3)) == 3
    assert store.counts("co1")["attention"] == 5
