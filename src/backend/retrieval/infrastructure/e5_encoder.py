"""Pinned local multilingual-E5 encoder; offline except for the explicit download."""
import os

import numpy as np

from ..domain.embedding import MODEL, RECIPE, REVISION, windows

FILES=['config.json','model.safetensors','tokenizer.json','tokenizer_config.json',
       'special_tokens_map.json','README.md']


def download_model():
    os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN']='1';os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
    from huggingface_hub import snapshot_download
    return snapshot_download(MODEL,revision=REVISION,token=False,max_workers=2,allow_patterns=FILES)


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
            allow_patterns=FILES)
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
