"""Deterministic text chunking with source character offsets."""


def chunks(text, size=1400, overlap=200):
    if size < 100 or not 0 <= overlap < size // 2:
        raise ValueError('chunk size must be >=100; overlap must be < size/2')
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = max(text.rfind('\n\n', start + size // 2, end),
                           text.rfind('. ', start + size // 2, end))
            if boundary >= 0:
                end = boundary + 1
            else:
                boundary = text.rfind(' ', start + size // 2, end)
                if boundary >= 0:
                    end = boundary
        if text[start:end].strip():
            yield {'start_char': start, 'end_char': end, 'text': text[start:end]}
        if end == len(text):
            break
        next_start = max(start + 1, end - overlap)
        # Start the overlapping window at a word boundary.
        while next_start < end and next_start > 0 and not text[next_start - 1].isspace():
            next_start += 1
        start = next_start
