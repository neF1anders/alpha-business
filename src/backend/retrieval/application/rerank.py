"""Cross-encoder ranking use case. Scores express relevance, never causal impact."""
import math
import threading

from orchestrator.domain.errors import DependencyUnavailable
from orchestrator.domain.models import RankedNews, utcnow
from orchestrator.domain.news_quality import historical_retrospective
from orchestrator.domain.ranking import TagReranker

from ..domain.needs import monitoring_queries
from .ports import PairScorer


class PairReranker:
    def __init__(self,scorer: PairScorer):
        self.scorer=scorer;self._lock=threading.Lock();self._legacy=TagReranker()

    def rerank(self,profile,items,limit,*,require_tag_match=True,now=None):
        # Chat retains its question-conditioned lexical path; source activity is
        # needed for company feeds. Missing context never becomes an empty query.
        if not require_tag_match or not profile.activity:
            return self._legacy.rerank(profile,items,limit,require_tag_match=require_tag_match,now=now)
        if type(limit) is not int or not 1<=limit<=100 or len(items)>100:
            raise ValueError('Invalid ranking bounds')
        if not self._lock.acquire(blocking=False):
            raise DependencyUnavailable('reranker','локальная модель занята')
        try:
            now=now or utcnow()
            monitoring_queries(profile)  # validate activity context
            # Same bounded lead representation for every article, regardless of
            # its total length. Full source remains in NewsItem/DB.
            eligible=[n for n in items if n.published_at<=now and
                      not historical_retrospective(n.title,n.text,now)]
            query=profile.activity+'. Новости об изменениях спроса, цен, затрат и правил работы.'
            if self.scorer.token_count(query)>160:
                raise ValueError('Activity is too long for pair ranking')
            passages=[n.title+'\n'+n.text[:2400] for n in eligible]
            scores=self.scorer.score_pairs([(query,p) for p in passages])
            best={}
            for n,logit,passage in zip(eligible,scores,passages,strict=True):
                relevance=1/(1+math.exp(-max(-50,min(logit,50))))
                age=max(0,(now-n.published_at).total_seconds()/86400)
                freshness=0.5**(age/14)
                # No calibrated abstention threshold; logits rank this candidate pool.
                # Freshness only breaks exact relevance ties.
                score=relevance
                ranked=RankedNews(n,score,tuple(sorted(n.tags&profile.tags)),
                    (f'pair_logit={logit:.4f}',f'freshness={freshness:.3f}',
                     f'lead_chars={len(passage)}; pair_tokens<=512',
                     'relevance_only; applicability_and_impact_unverified'))
                old=best.get(n.cluster_id)
                if old is None or (ranked.score,ranked.item.published_at)>(old.score,old.item.published_at):
                    best[n.cluster_id]=ranked
            return sorted(best.values(),key=lambda r:(-r.score,-r.item.published_at.timestamp(),r.item.news_id))[:limit]
        finally:self._lock.release()
