"""Small deterministic cleanup for natural questions; no LLM or hidden synonyms."""
import re

# Interrogatives and conversational framing only, not business entities or factors.
FRAMING = frozenset("""почему зачем как какой какая какое какие каким какими какую
какого каких что чем чего кто кого кому где когда сколько насколько пожалуйста
расскажи объясни покажи найди подскажи узнать интересует
будет будут повлияет влияет влиять происходит меняется изменится сейчас ситуация""".split())
TOKEN = re.compile(r"[\w]+(?:[-][\w]+)*", re.UNICODE)


def natural_query(text):
    words = TOKEN.findall(text.casefold().replace('ё', 'е'))
    words = [word for word in words if word not in FRAMING and len(word) > 1]
    return ' '.join(dict.fromkeys(words))
