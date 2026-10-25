"""Pinned local E5 embeddings and resumable PostgreSQL index; no inference API.

Only the explicit download command contacts Hugging Face. All encoding is offline.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
from datetime import datetime, timedelta, timezone

import numpy as np
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ingestion.database import connect

MODEL='intfloat/multilingual-e5-small'
REVISION='614241f622f53c4eeff9890bdc4f31cfecc418b3'
RECIPE=dict(model=MODEL,revision=REVISION,dimension=384,pooling='attention_mask_mean',
            normalization='l2',query_prefix='query: ',passage_prefix='passage: ',
            max_tokens=512,title_token_cap=64,overlap_tokens=48,version=1)
SPACE_ID=hashlib.sha256(json.dumps(RECIPE,sort_keys=True).encode()).hexdigest()
SCHEMA=Path(__file__).resolve().parent.parent/'sql/embeddings.sql'


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


class LocalE5:
    def __init__(self, *, device='auto'):
        os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN']='1'
        os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
        os.environ['TOKENIZERS_PARALLELISM']='false'
        from huggingface_hub import snapshot_download
        from transformers import AutoModel,AutoTokenizer
        import torch
        torch.set_num_threads(4)
        self.torch=torch
        self.device=('mps' if torch.backends.mps.is_available() else 'cpu') if device=='auto' else device
        if self.device not in ('cpu','mps'):raise ValueError('Use cpu, mps or auto')
        path=snapshot_download(MODEL,revision=REVISION,local_files_only=True,token=False,
            allow_patterns=['config.json','model.safetensors','tokenizer.json','tokenizer_config.json','special_tokens_map.json','README.md'])
        self.tokenizer=AutoTokenizer.from_pretrained(path,local_files_only=True,trust_remote_code=False)
        self.model=AutoModel.from_pretrained(path,local_files_only=True,trust_remote_code=False,
                                             use_safetensors=True).to(self.device).eval()

    def passages(self,title,text):
        title_ids=self.tokenizer.encode(title,add_special_tokens=False)[:RECIPE['title_token_cap']]
        prefix=self.tokenizer.encode(RECIPE['passage_prefix'],add_special_tokens=False)+title_ids
        prefix+=self.tokenizer.encode('\n',add_special_tokens=False)
        body=self.tokenizer.encode(text,add_special_tokens=False,truncation=False,verbose=False)
        capacity=512-self.tokenizer.num_special_tokens_to_add(False)-len(prefix)
        output=[]
        for start,end in windows(len(body),capacity,RECIPE['overlap_tokens']):
            if self.tokenizer.num_special_tokens_to_add(False)!=2:
                raise ValueError('Unexpected tokenizer special-token recipe')
            ids=[self.tokenizer.bos_token_id]+prefix+body[start:end]+[self.tokenizer.eos_token_id]
            if len(ids)>512:raise ValueError('Passage overflow')
            output.append((ids,start,end))
        if not output:raise ValueError('Empty news body')
        return output

    def encode_ids(self, sequences, batch_size=16):
        if not sequences:return np.empty((0,384),dtype=np.float32)
        outputs=[]
        for offset in range(0,len(sequences),batch_size):
            batch=self.tokenizer.pad({'input_ids':sequences[offset:offset+batch_size]},
                                      padding=True,return_tensors='pt')
            batch={k:v.to(self.device) for k,v in batch.items()}
            with self.torch.inference_mode():
                states=self.model(**batch).last_hidden_state
                mask=batch['attention_mask'][...,None].bool()
                pooled=states.masked_fill(~mask,0).sum(dim=1)/mask.sum(dim=1)
                pooled=self.torch.nn.functional.normalize(pooled,p=2,dim=1)
            outputs.append(pooled.cpu().float().numpy())
        result=np.concatenate(outputs).astype('<f4',copy=False)
        if result.shape!=(len(sequences),384) or not np.isfinite(result).all():
            raise ValueError('Invalid model output')
        return result

    def query(self,text):
        if not isinstance(text,str) or not text.strip():raise ValueError('Nonempty query required')
        ids=self.tokenizer.encode(RECIPE['query_prefix']+text,truncation=False)
        if len(ids)>512:raise ValueError('Query exceeds model context; shorten it explicitly')
        return self.encode_ids([ids])[0]


def build_index(encoder, *, documents_per_batch=32):
    if type(documents_per_batch) is not int or not 1<=documents_per_batch<=128:
        raise ValueError('documents_per_batch must be 1..128')
    with connect() as c:
        c.execute(SCHEMA.read_text())
        c.execute('INSERT INTO insights.embedding_spaces VALUES (%s,%s,now()) ON CONFLICT DO NOTHING',
                  (SPACE_ID,Jsonb(RECIPE)))
        saved=c.execute('SELECT recipe FROM insights.embedding_spaces WHERE space_id=%s',(SPACE_ID,)).fetchone()[0]
        if saved!=RECIPE:raise ValueError('Conflicting embedding recipe')
    with connect() as c:
        c.execute('SET TRANSACTION READ ONLY')
        rows=c.execute('SELECT news_id,title,text FROM insights.news_articles ORDER BY news_id').fetchall()
        existing=c.execute('''SELECT news_id,min(source_hash),count(*),min(chunk_count),max(chunk_count),
                            min(chunk_index),max(chunk_index)
                            FROM insights.news_embeddings WHERE space_id=%s GROUP BY news_id''',(SPACE_ID,)).fetchall()
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
        with connect() as c:
            # Serialize only writes, not expensive inference. Each document is atomic.
            c.execute('SELECT pg_advisory_xact_lock(521104)')
            for doc,title,text,h in group:
                current=c.execute('SELECT title,text FROM insights.news_articles WHERE news_id=%s FOR SHARE',(doc,)).fetchone()
                if current is None or source_hash(*current)!=h:raise ValueError('Source changed during encoding')
            for meta,vector in zip(metadata,vectors):
                c.execute('''INSERT INTO insights.news_embeddings
                    (space_id,news_id,source_hash,chunk_index,chunk_count,token_start,token_end,embedding)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',(SPACE_ID,*meta,vector.tobytes()))
        processed+=len(group)
        print(json.dumps(dict(indexed=len(complete)+processed,total=len(rows),
                              elapsed_s=round(time.perf_counter()-start,1))),flush=True)
    return dict(total_documents=len(rows),new_documents=processed,space_id=SPACE_ID)


class SemanticNewsIndex:
    """Read-only snapshot, exact cosine with max passage score per news article."""
    def __init__(self):
        with connect() as c:
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            with c.cursor(row_factory=dict_row) as cur:
                cur.execute('SELECT news_id,title,text,url,source_kind,published_at FROM insights.news_articles ORDER BY news_id')
                self.documents={r['news_id']:r for r in cur.fetchall()}
            rows=c.execute('''SELECT news_id,source_hash,chunk_index,chunk_count,token_start,token_end,embedding
                FROM insights.news_embeddings WHERE space_id=%s ORDER BY news_id,chunk_index''',(SPACE_ID,)).fetchall()
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


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['download','index','search'])
    parser.add_argument('--device',choices=['auto','cpu','mps'],default='auto')
    parser.add_argument('--company-id')
    parser.add_argument('--as-of')
    parser.add_argument('--limit',type=int,default=5)
    args=parser.parse_args()
    if args.command=='download':
        os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN']='1';os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
        from huggingface_hub import snapshot_download
        path=snapshot_download(MODEL,revision=REVISION,token=False,max_workers=2,
            allow_patterns=['config.json','model.safetensors','tokenizer.json','tokenizer_config.json','special_tokens_map.json','README.md'])
        print(path)
    elif args.command=='index':
        print(json.dumps(build_index(LocalE5(device=args.device))))
    else:
        if not args.company_id:parser.error('--company-id required for search')
        as_of=datetime.fromisoformat(args.as_of) if args.as_of else datetime.now(timezone.utc)
        if as_of.utcoffset() is None:parser.error('Timezone-aware as-of required')
        if not 1<=args.limit<=100:parser.error('--limit must be 1..100')
        with connect() as c:
            c.execute('SET TRANSACTION READ ONLY')
            row=c.execute('SELECT mainokved_name FROM insights.companies WHERE synthetic_u=%s',(args.company_id,)).fetchone()
        if row is None or not row[0]:parser.error('Company activity unavailable')
        query='Основная деятельность компании: '+row[0]
        encoder=LocalE5(device=args.device);index=SemanticNewsIndex()
        hits=index.search(encoder.query(query),as_of=as_of,published_after=as_of-timedelta(days=90),limit=args.limit)
        print(json.dumps(dict(company_id=args.company_id,query=query,model=MODEL,
            interpretation='semantic candidates, not business impact judgments',
            hits=[dict(**hit,title=index.documents[hit['news_id']]['title'],url=index.documents[hit['news_id']]['url'])
                  for hit in hits]),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
