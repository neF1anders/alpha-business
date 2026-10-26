"""Pinned offline cross-encoder used as a pair scorer."""
import math
import os

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


class CrossEncoderScorer:
    def __init__(self,device='auto'):
        self.device=device;self._model=None

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

    def token_count(self,text):
        return len(self.tokenizer.encode(text,add_special_tokens=True))

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
