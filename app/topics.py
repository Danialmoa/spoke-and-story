import re
from concurrent.futures import ThreadPoolExecutor

from . import agent, story

MAX_TOPICS = 6
# Shorter intros are lists, stubs and timelines.
MIN_FACTS = 200
FACTS_CHARS = 1200
# The country's Wikipedia, where its language code isn't the country code.
# Countries whose Wikipedia uses the country code (it, fr, de…). Others (CY, CO, IL…) would hit an unrelated or missing wiki.
SAME_CODE = {"IT", "FR", "DE", "ES", "NL", "PL", "PT", "RU", "FI", "HU", "RO", "TR", "SK", "HR", "BG", "LT", "LV", "NO"}
LANGS = {
    "AT": "de",
    "CH": "de",
    "BE": "nl",
    "BR": "pt",
    "MX": "es",
    "AR": "es",
    "SE": "sv",
    "DK": "da",
    "CZ": "cs",
    "GR": "el",
    "SI": "sl",
    "UA": "uk",
    "JP": "ja",
    "CN": "zh",
    "KR": "ko",
    "GB": "en",
    "IE": "en",
    "US": "en",
    "CA": "en",
    "AU": "en",
    "NZ": "en",
}


def _search(wiki: str, query: str) -> list[dict]:
    """The top full-text matches with their intros, best first."""
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrlimit": 3,
        "prop": "extracts|pageprops",
        "ppprop": "disambiguation",
        "exintro": 1,
        "explaintext": 1,
        "exlimit": 3,
        "redirects": 1,
        "format": "json",
        "formatversion": 2,
    }
    pages = story._get(f"{wiki}/w/api.php", params).get("query", {}).get("pages", [])
    return sorted(pages, key=lambda p: p.get("index", 0))


def _fits(page: dict, idea: dict, query: str, skip: set[str]) -> bool:
    """Full-text search matches words anywhere in an article; the intro must be about the place and the topic."""
    intro = page.get("extract", "")
    text = f"{page['title']} {intro}".lower()
    place = idea["place"].lower()
    words = [w[:5] for w in re.findall(r"\w{4,}", query.lower()) if w not in place]
    return (
        page["title"].lower() not in skip
        and "pageprops" not in page
        and len(intro) >= MIN_FACTS
        and place in text
        and (not words or any(w in text for w in words))
    )


def _first(wiki: str, ideas: list[dict], key: str, skip: set[str]) -> dict | None:
    queries = [idea[key] for idea in ideas]
    with ThreadPoolExecutor(len(ideas)) as pool:
        found = list(pool.map(lambda q: _search(wiki, q) if q else [], queries))
    for idea, query, pages in zip(ideas, queries, found):
        for page in pages:
            if _fits(page, idea, query, skip):
                return {"place": page["title"], "topic": idea["topic"], "facts": page["extract"][:FACTS_CHARS]}
    return None


def find(town: dict, nearby_towns: list[str], told: list[str]) -> dict | None:
    """{"place": <Wikipedia title>, "topic", "facts"} for the first topic Gemma proposes about the area that Wikipedia
    has a good article for: English first, then the country's language. Gemma only names topics; the facts are
    Wikipedia's. town: news.town_at's answer."""
    # The town's own article is the area story's last fallback, not a topic.
    skip = {t.lower() for t in told} | {town["town"].lower()}
    area = {"town": town["town"], "region": town["region"], "country": town["country"], "nearby_towns": nearby_towns}
    ideas = [i for i in agent.area_topics(area, told) if i["topic"].lower() not in skip][:MAX_TOPICS]
    if not ideas:
        return None
    found = _first(story.WIKI, ideas, "topic", skip)
    code = town["country_code"].upper()
    lang = LANGS.get(code) or (code.lower() if code in SAME_CODE else None)
    if not found and lang and lang != "en":
        found = _first(f"https://{lang}.wikipedia.org", ideas, "local", skip)
    return found
