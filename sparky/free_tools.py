"""Free non-AI tools for Sparky.

These tools use public APIs with no API key for common totem tasks. Responses
are cached briefly to avoid repeated calls while Gemini Live is speaking.
"""

import json
import time
from datetime import date
from urllib.parse import quote

import requests

from sparky.config import FREE_TOOLS_CACHE_SECONDS, FREE_TOOLS_ENABLED, FREE_TOOLS_TIMEOUT
from sparky.od_data import consultar_encuesta


try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass


USER_AGENT = "SparkyTotem/1.0 (local assistant; contact: local)"
_CACHE = {}


def call_free_tool(name, args):
    """Dispatch a public-data tool call by name."""
    if not FREE_TOOLS_ENABLED:
        return {"ok": False, "error": "Herramientas gratuitas deshabilitadas."}
    args = args or {}
    handlers = {
        "get_weather": get_weather,
        "search_wikipedia": search_wikipedia,
        "convert_currency": convert_currency,
        "get_holidays": get_holidays,
        "get_country_info": get_country_info,
        "search_books": search_books,
        "get_news_brief": get_news_brief,
        "consultar_encuesta_od": consultar_encuesta,
    }
    handler = handlers.get(name)
    if not handler:
        return {"ok": False, "error": f"Herramienta no soportada: {name}"}
    try:
        return handler(**args)
    except Exception as exc:
        return {"ok": False, "tool": name, "error": str(exc)}


def get_weather(city, country_code=""):
    """Current weather for a city using Open-Meteo."""
    city = _required(city, "city")
    geo = _get_json(
        "https://geocoding-api.open-meteo.com/v1/search",
        {
            "name": city,
            "count": 1,
            "language": "es",
            "format": "json",
            **({"countryCode": country_code.upper()} if country_code else {}),
        },
        ttl=86400,
    )
    results = geo.get("results") or []
    if not results:
        return {"ok": False, "error": f"No encontre la ciudad: {city}"}
    place = results[0]
    weather = _get_json(
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
            "timezone": "auto",
            "forecast_days": 1,
        },
    )
    current = weather.get("current", {})
    units = weather.get("current_units", {})
    return {
        "ok": True,
        "source": "Open-Meteo",
        "location": _place_name(place),
        "temperature": _unit(current.get("temperature_2m"), units.get("temperature_2m")),
        "apparent_temperature": _unit(current.get("apparent_temperature"), units.get("apparent_temperature")),
        "humidity": _unit(current.get("relative_humidity_2m"), units.get("relative_humidity_2m")),
        "precipitation": _unit(current.get("precipitation"), units.get("precipitation")),
        "wind": _unit(current.get("wind_speed_10m"), units.get("wind_speed_10m")),
        "condition": _weather_label(current.get("weather_code")),
        "time": current.get("time", ""),
    }


def search_wikipedia(topic, language="es"):
    """Short encyclopedia answer with image when available."""
    topic = _required(topic, "topic")
    language = (language or "es").lower()[:8]
    search = _get_json(
        f"https://{language}.wikipedia.org/w/api.php",
        {
            "action": "query",
            "list": "search",
            "srsearch": topic,
            "srlimit": 1,
            "format": "json",
            "origin": "*",
        },
    )
    hits = search.get("query", {}).get("search", [])
    if not hits:
        return {"ok": False, "error": f"No encontre Wikipedia para: {topic}"}
    title = hits[0]["title"]
    summary = _get_json(f"https://{language}.wikipedia.org/api/rest_v1/page/summary/{quote(title)}")
    return {
        "ok": True,
        "source": f"Wikipedia {language}",
        "title": summary.get("title", title),
        "description": summary.get("description", ""),
        "summary": summary.get("extract", ""),
        "url": summary.get("content_urls", {}).get("desktop", {}).get("page", ""),
        "image": (summary.get("thumbnail") or {}).get("source", ""),
    }


def convert_currency(amount, from_currency, to_currency):
    """Currency conversion using Frankfurter."""
    amount = float(amount)
    from_currency = _required(from_currency, "from_currency").upper()
    to_currency = _required(to_currency, "to_currency").upper()
    data = _get_json(f"https://api.frankfurter.dev/v2/rate/{from_currency}/{to_currency}")
    rate = data.get("rate")
    if rate is None:
        return {"ok": False, "error": f"No hay tasa {from_currency}->{to_currency}"}
    converted = round(amount * float(rate), 4)
    return {
        "ok": True,
        "source": "Frankfurter",
        "date": data.get("date", ""),
        "amount": amount,
        "from": from_currency,
        "to": to_currency,
        "rate": rate,
        "converted": converted,
    }


def get_holidays(country_code="PE", year=None):
    """Public holidays by country using Nager.Date."""
    year = int(year or date.today().year)
    country_code = (country_code or "PE").upper()
    data = _get_json(f"https://date.nager.at/api/v3/PublicHolidays/{year}/{country_code}", ttl=86400)
    today = date.today().isoformat()
    upcoming = [h for h in data if h.get("date", "") >= today][:5]
    return {
        "ok": True,
        "source": "Nager.Date",
        "country_code": country_code,
        "year": year,
        "upcoming": [
            {"date": h.get("date"), "name": h.get("localName") or h.get("name"), "global": h.get("global")}
            for h in upcoming
        ],
        "total": len(data),
    }


def get_country_info(country):
    """Country overview using Wikipedia to avoid paid country APIs."""
    country = _required(country, "country")
    wiki = search_wikipedia(country, language="es")
    if not wiki.get("ok"):
        return wiki
    return {
        "ok": True,
        "source": wiki.get("source", "Wikipedia"),
        "name": wiki.get("title", country),
        "description": wiki.get("description", ""),
        "summary": wiki.get("summary", ""),
        "url": wiki.get("url", ""),
        "image": wiki.get("image", ""),
    }


def search_books(query, limit=3):
    """Book search using Open Library."""
    query = _required(query, "query")
    limit = max(1, min(int(limit or 3), 5))
    data = _get_json("https://openlibrary.org/search.json", {"q": query, "limit": limit})
    docs = data.get("docs", [])[:limit]
    return {
        "ok": True,
        "source": "Open Library",
        "query": query,
        "total": data.get("numFound", 0),
        "books": [
            {
                "title": d.get("title"),
                "author": ", ".join(d.get("author_name", [])[:3]),
                "first_publish_year": d.get("first_publish_year"),
                "key": "https://openlibrary.org" + d.get("key", ""),
            }
            for d in docs
        ],
    }


def get_news_brief(query, max_records=5):
    """Recent global news links using GDELT DOC 2.0."""
    query = _required(query, "query")
    max_records = max(1, min(int(max_records or 5), 10))
    try:
        data = _get_json(
            "https://api.gdeltproject.org/api/v2/doc/doc",
            {"query": query, "mode": "ArtList", "format": "json", "maxrecords": max_records},
            ttl=300,
        )
        articles = data.get("articles", [])[:max_records]
        if articles:
            return {
                "ok": True,
                "source": "GDELT",
                "query": query,
                "articles": [
                    {
                        "title": a.get("title", ""),
                        "domain": a.get("domain", ""),
                        "url": a.get("url", ""),
                        "language": a.get("language", ""),
                        "seendate": a.get("seendate", ""),
                    }
                    for a in articles
                ],
            }
    except Exception:
        pass

    data = _get_json(
        "https://es.wikinews.org/w/api.php",
        {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": max_records,
            "format": "json",
            "origin": "*",
        },
        ttl=600,
    )
    hits = data.get("query", {}).get("search", [])[:max_records]
    return {
        "ok": True,
        "source": "Wikinews",
        "query": query,
        "articles": [
            {
                "title": h.get("title", ""),
                "url": "https://es.wikinews.org/wiki/" + quote(h.get("title", "").replace(" ", "_")),
                "snippet": _strip_html(h.get("snippet", "")),
                "timestamp": h.get("timestamp", ""),
            }
            for h in hits
        ],
    }


def _get_json(url, params=None, ttl=None):
    params = params or {}
    ttl = FREE_TOOLS_CACHE_SECONDS if ttl is None else ttl
    key = (url, json.dumps(params, sort_keys=True, ensure_ascii=False))
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached["time"] < ttl:
        return cached["data"]
    response = requests.get(
        url,
        params=params,
        timeout=FREE_TOOLS_TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    response.raise_for_status()
    data = response.json()
    _CACHE[key] = {"time": now, "data": data}
    return data


def _required(value, name):
    value = str(value or "").strip()
    if not value:
        raise ValueError(f"Falta {name}.")
    return value


def _place_name(place):
    parts = [place.get("name"), place.get("admin1"), place.get("country")]
    return ", ".join(p for p in parts if p)


def _unit(value, unit):
    if value is None:
        return ""
    return f"{value} {unit or ''}".strip()


def _weather_label(code):
    labels = {
        0: "cielo despejado",
        1: "principalmente despejado",
        2: "parcialmente nublado",
        3: "nublado",
        45: "niebla",
        48: "niebla con escarcha",
        51: "llovizna ligera",
        53: "llovizna moderada",
        55: "llovizna intensa",
        61: "lluvia ligera",
        63: "lluvia moderada",
        65: "lluvia intensa",
        80: "chubascos ligeros",
        81: "chubascos moderados",
        82: "chubascos intensos",
        95: "tormenta",
    }
    return labels.get(code, f"codigo meteorologico {code}" if code is not None else "")


def _strip_html(text):
    return (
        str(text or "")
        .replace("<span class=\"searchmatch\">", "")
        .replace("</span>", "")
        .replace("&quot;", "\"")
        .replace("&amp;", "&")
    )
