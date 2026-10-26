"""Conservative publication-time checks; standard library only."""
import re


def historical_retrospective(title, text, as_of):
    """Conservative lead rule; never reject merely because an old year occurs."""
    lead=' '.join(text[:900].casefold().split())
    match=re.match(r'^(?:в\s+)?(1\d{3})\s*(?:году|год[ау]?\b|г\.)',lead)
    if not match or int(match[1])>=as_of.year-40:
        return False
    sample=(title+' '+lead).casefold()
    if not re.search(r'истори|ссср|советск|революц|нэп|прошлого века',sample):
        return False
    contemporary=re.search(r'\b(?:сегодня|вчера|сейчас|теперь)\b|в этом году',sample)
    years=[int(y) for y in re.findall(r'\b(?:19|20)\d{2}\b',sample)]
    return not contemporary and not any(y>=as_of.year-2 for y in years)
