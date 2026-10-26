"""Offline cross-encoder ranking. Scores express relevance, never causal impact."""
import argparse
import math
import os
import threading

from orchestrator.domain.errors import DependencyUnavailable
from orchestrator.domain.models import RankedNews, utcnow
from orchestrator.domain.ranking import TagReranker
from .needs import historical_retrospective, monitoring_queries

MODEL='cross-encoder/mmarco-mMiniLMv2-L12-H384-v1'
REVISION='1427fd652930e4ba29e8149678df786c240d8825'
FILES=['config.json','model.safetensors','tokenizer.json','tokenizer_config.json',
       'special_tokens_map.json','README.md']


def model_path(download=False):
    os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN']='1'
    os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
    os.environ['TOKENIZERS_PARALLELISM']='false'
    from huggingface_hub import snapshot_download
    return snapshot_download(MODEL,revision=REVISION,token=False,local_files_only=not download,
                             allow_patterns=FILES,max_workers=2)


class LocalPairReranker:
    def __init__(self,device='auto'):
        self.device=device;self._model=None;self._lock=threading.Lock();self._legacy=TagReranker()

    def warmup(self):
        import torch
        from transformers import AutoTokenizer,AutoModelForSequenceClassification
        torch.set_num_threads(4)
        self.device=('mps' if torch.backends.mps.is_available() else 'cpu') if self.device=='auto' else self.device
        if self.device not in ('cpu','mps'):raise ValueError('Invalid local device')
        path=model_path()
        self.tokenizer=AutoTokenizer.from_pretrained(path,local_files_only=True,trust_remote_code=False)
        self._model=AutoModelForSequenceClassification.from_pretrained(path,local_files_only=True,
            trust_remote_code=False,use_safetensors=True).to(self.device).eval()
        self.torch=torch

    def score_pairs(self,pairs):
        if self._model is None:raise RuntimeError('Call warmup before serving requests')
        if not pairs:return []
        result=[]
        for offset in range(0,len(pairs),8):
            batch=pairs[offset:offset+8]
            inputs=self.tokenizer([p[0] for p in batch],[p[1] for p in batch],padding=True,
                truncation='only_second',max_length=512,return_tensors='pt')
            inputs={k:v.to(self.device) for k,v in inputs.items()}
            with self.torch.inference_mode():
                logits=self._model(**inputs).logits.reshape(-1).float().cpu().tolist()
            if len(logits)!=len(batch) or not all(math.isfinite(v) for v in logits):
                raise ValueError('Invalid reranker output')
            result.extend(logits)
        return result

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
            if len(self.tokenizer.encode(query,add_special_tokens=True))>160:
                raise ValueError('Activity is too long for pair ranking')
            passages=[n.title+'\n'+n.text[:2400] for n in eligible]
            scores=self.score_pairs([(query,p) for p in passages])
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


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['download'])
    p.parse_args();print(model_path(download=True))

if __name__=='__main__':main()
