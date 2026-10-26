"""Zero-shot sector matching with a local sentence-transformers model."""
from ..domain.tagging import MODEL, choose_sector
from ..domain.taxonomy import SECTORS

class SemanticSector:
    def __init__(self, model=MODEL, revision=None, threshold=0.30, margin=0.03,
                 cache_dir='data/cache/models', batch_size=32):
        import numpy as np
        import torch
        from sentence_transformers import SentenceTransformer
        torch.set_num_threads(min(4, torch.get_num_threads()))
        self.np = np
        self.model = SentenceTransformer(model, revision=revision, cache_folder=cache_dir, device='cpu', trust_remote_code=False)
        self.names = list(SECTORS)
        self.threshold, self.margin, self.batch_size = threshold, margin, batch_size
        self.prototypes = self.model.encode([v[1] for v in SECTORS.values()],
                                             normalize_embeddings=True, show_progress_bar=False)
        resolved_revision = getattr(self.model[0].auto_model.config, '_commit_hash', None)
        self.settings = dict(backend='sentence-transformers', model=model,
                             revision=resolved_revision or revision, threshold=threshold, margin=margin)

    def predict(self, texts):
        # MiniLM accepts 128 tokens: cover ALL of a chunk using token windows.
        tokenizer = self.model.tokenizer
        limit = self.model.max_seq_length - tokenizer.num_special_tokens_to_add(pair=False)
        windows, owners, weights = [], [], []
        for owner, text in enumerate(texts):
            tokens = tokenizer.encode(text, add_special_tokens=False, truncation=False)
            for start in range(0, max(1, len(tokens)), limit):
                part = tokens[start:start + limit]
                windows.append(tokenizer.decode(part, skip_special_tokens=True))
                owners.append(owner)
                weights.append(max(1, len(part)))
        vectors = self.model.encode(windows, batch_size=self.batch_size,
                                    normalize_embeddings=True, show_progress_bar=False)
        sums = self.np.zeros((len(texts), vectors.shape[1]), dtype='float32')
        for owner, weight, vector in zip(owners, weights, vectors):
            sums[owner] += weight * vector
        sums /= self.np.maximum(self.np.linalg.norm(sums, axis=1, keepdims=True), 1e-12)
        scores = sums @ self.prototypes.T
        return [choose_sector(dict(zip(self.names, row)), self.threshold, self.margin) for row in scores]
