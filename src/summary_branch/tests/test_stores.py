from datetime import timedelta

from summary_branch.models import (
    Banner, BannerDTO, ContentKey, Decision, Severity, utcnow,
)
from summary_branch.stores import InMemoryBannerStore, InMemorySegmentCache



def key(cluster="c1", segment="s"):
    return ContentKey(cluster_id=cluster, segment_id=segment, prompt_version="v1")


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


def test_top_k_skips_expired_and_suppressed():
    store = InMemoryBannerStore()
    put(store, banner("ok", topic="rate"), banner("old", topic="fx", expires_in_h=-1),
        banner("hidden", topic="labor"))
    store.suppress("co1", "hidden", "не про меня")
    assert ids(store.top_k("co1", 3)) == ["ok"]


def test_top_k_diversifies_topics_when_alternatives_exist():
    store = InMemoryBannerStore()
    put(store, banner("r1", "rate"), banner("r2", "rate"), banner("r3", "rate"),
        banner("l1", "labor", severity=Severity.WATCH, confidence=0.8))
    result = ids(store.top_k("co1", 3))
    assert "l1" in result and len(result) == 3


def test_mark_seen_lowers_priority():
    store = InMemoryBannerStore()
    put(store, banner("a", "rate", confidence=0.95), banner("b", "labor", confidence=0.9))
    assert ids(store.top_k("co1", 1)) == ["a"]
    store.mark_seen("co1", "a")
    assert ids(store.top_k("co1", 1)) == ["b"]


def test_put_is_idempotent_and_keeps_state():
    store = InMemoryBannerStore()
    b = banner("a")
    put(store, b, b)
    assert ids(store.top_k("co1", 3)) == ["a"]
    store.suppress("co1", "a", "неточно")
    put(store, b)
    assert store.top_k("co1", 3) == []


def test_put_returns_true_only_for_new_banner_id():
    store = InMemoryBannerStore()
    b = banner("a")
    assert store.put(b, dto(b)) is True
    assert store.put(b, dto(b)) is False
    assert store.put(banner("a", company="co2"), dto(b)) is True      # у другой компании баннер новый
    store.invalidate_cluster("c1", "новое событие")
    assert store.put(b, dto(b)) is True                                # после снятия снова новый


def test_top_k_is_isolated_per_company():
    store = InMemoryBannerStore()
    put(store, banner("a", company="co1"), banner("b", company="co2"))
    assert ids(store.top_k("co2", 3)) == ["b"]
    assert store.top_k("nobody", 3) == []


def test_invalidate_cluster_removes_for_all_companies_and_counts():
    store = InMemoryBannerStore()
    put(store, banner("a", company="co1", cluster="c1"), banner("b", company="co2", cluster="c1", topic="fx"),
        banner("c", company="co1", cluster="c2", topic="labor"))
    assert store.invalidate_cluster("c1", "новое событие") == 2
    assert ids(store.top_k("co1", 3)) == ["c"]
    assert store.top_k("co2", 3) == []
    assert store.invalidate_cluster("c1", "повтор") == 0


def test_segment_cache_ttl_and_hit_counter():
    now = [utcnow()]
    cache = InMemorySegmentCache(clock=lambda: now[0])
    k = key()
    assert cache.get(k) is None
    item = _summary(k)
    cache.put(item, ttl_s=60)
    assert cache.get(k) is item
    now[0] += timedelta(seconds=61)
    assert cache.get(k) is None
    assert (cache.hits, cache.misses) == (1, 2)


def _summary(k):
    from summary_branch.models import ContentSignals, LeadRef, SegmentSummary
    signals = ContentSignals(grounding=1, source_quality=1, claim_mix=1, faithfulness=1, coverage=1,
                             specificity=1, brevity=1, structure_ok=True, sqs=1)
    lead = LeadRef(cluster_id=k.cluster_id, published_at=utcnow(), rerank_score=0.5)
    return SegmentSummary(key=k, model_id="m", headline="h", for_you_template="", for_you_fallback="f", topic="rate",
                          severity=Severity.WATCH, claims=[], sources=[], lead=lead, signals=signals)
