"""Resumable news embedding index build and exact-cosine search over the stored vectors."""
import json
import time

import numpy as np

from ..domain.embedding import MODEL, RECIPE, SPACE_ID, source_hash
from .ports import EmbeddingStore, TextEncoder


def build_index(encoder: TextEncoder, store: EmbeddingStore, *, documents_per_batch=32):
    if type(documents_per_batch) is not int or not 1<=documents_per_batch<=128:
        raise ValueError('documents_per_batch must be 1..128')
    store.ensure_space(SPACE_ID,RECIPE)
    rows=store.read_news()
    existing=store.read_indexed_summary(SPACE_ID)
    complete={}
    for doc,h,count,lo,hi,first,last in existing:
        if count!=lo or lo!=hi or first!=0 or last!=count-1:raise ValueError('Incomplete stored document '+doc)
        complete[doc]=h
    pending=[]
    for doc,title,text in rows:
        h=source_hash(title,text)
        if doc in complete:
            if complete[doc]!=h:raise ValueError('Stale embedding: '+doc)
        else:pending.append((doc,title,text,h))
    start=time.perf_counter();processed=0
    print(json.dumps(dict(total_documents=len(rows),already_indexed=len(complete),pending=len(pending),
                          model=MODEL,space_id=SPACE_ID,device=encoder.device)),flush=True)
    for offset in range(0,len(pending),documents_per_batch):
        group=pending[offset:offset+documents_per_batch];sequences=[];metadata=[]
        for doc,title,text,h in group:
            parts=encoder.passages(title,text)
            for index,(ids,a,b) in enumerate(parts):
                sequences.append(ids);metadata.append((doc,h,index,len(parts),a,b))
        vectors=encoder.encode_ids(sequences)
        store.save_group(SPACE_ID,group,metadata,vectors)
        processed+=len(group)
        print(json.dumps(dict(indexed=len(complete)+processed,total=len(rows),
                              elapsed_s=round(time.perf_counter()-start,1))),flush=True)
    return dict(total_documents=len(rows),new_documents=processed,space_id=SPACE_ID)


class SemanticNewsIndex:
    """Read-only snapshot, exact cosine with max passage score per news article."""
    def __init__(self, store: EmbeddingStore):
        self.documents,rows=store.load_snapshot(SPACE_ID)
        counts={};expected={};self.passages=[];vectors=[]
        for doc,h,index,count,a,b,blob in rows:
            source=self.documents[doc]
            if source_hash(source['title'],source['text'])!=h:raise ValueError('Stale source hash')
            if index!=counts.get(doc,0):raise ValueError('Noncontiguous passage index')
            if doc in expected and expected[doc]!=count:raise ValueError('Mixed chunk counts')
            counts[doc]=index+1;expected[doc]=count
            vector=np.frombuffer(blob,dtype='<f4').copy()
            if vector.shape!=(384,) or not np.isfinite(vector).all() or not np.isclose(np.linalg.norm(vector),1,atol=.001):
                raise ValueError('Invalid stored vector')
            self.passages.append((doc,index,a,b));vectors.append(vector)
        if not rows or set(counts)!=set(self.documents) or any(counts[d]!=expected[d] for d in counts):
            raise ValueError('Embedding index is incomplete; run the resumable indexer')
        self.matrix=np.stack(vectors)
        self.matrix.flags.writeable=False

    def search(self,vector,*,as_of,published_after,limit=20,space_id=SPACE_ID):
        if space_id!=SPACE_ID:raise ValueError('Embedding space mismatch')
        if type(limit) is not int or limit<1:raise ValueError('Positive limit required')
        if as_of.utcoffset() is None or published_after.utcoffset() is None or published_after>as_of:
            raise ValueError('Invalid aware date bounds')
        vector=np.asarray(vector,dtype=np.float32)
        if vector.shape!=(384,) or not np.isfinite(vector).all() or np.linalg.norm(vector)==0:
            raise ValueError('Invalid query vector')
        scores=self.matrix@(vector/np.linalg.norm(vector));best={}
        for i,(doc,index,a,b) in enumerate(self.passages):
            if not published_after<=self.documents[doc]['published_at']<=as_of:continue
            score=float(scores[i])
            if doc not in best or score>best[doc]['score']:
                best[doc]=dict(news_id=doc,score=score,chunk_index=index,token_start=a,token_end=b)
        return sorted(best.values(),key=lambda r:(-r['score'],r['news_id']))[:limit]
