"""Pinned embedding recipe and pure helpers for passage windows and source hashes."""
import hashlib
import json

MODEL='intfloat/multilingual-e5-small'
REVISION='614241f622f53c4eeff9890bdc4f31cfecc418b3'
RECIPE=dict(model=MODEL,revision=REVISION,dimension=384,pooling='attention_mask_mean',
            normalization='l2',query_prefix='query: ',passage_prefix='passage: ',
            max_tokens=512,title_token_cap=64,overlap_tokens=48,version=1)
SPACE_ID=hashlib.sha256(json.dumps(RECIPE,sort_keys=True).encode()).hexdigest()


def source_hash(title,text):
    return hashlib.sha256((title+'\n'+text).encode()).hexdigest()


def windows(length, capacity, overlap=48):
    if capacity<=overlap or overlap<0 or length<0:raise ValueError('Invalid token window')
    start=0
    while start<length:
        end=min(start+capacity,length)
        yield start,end
        if end==length:break
        start=end-overlap
