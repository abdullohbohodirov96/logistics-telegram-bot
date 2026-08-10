"""
Reverse geocoding via Yandex Geocoder API — turns a delivered_lat/delivered_lng
pair into a human-readable Uzbek address (rayon/mahalla/ko'cha), used in the
finish notification instead of (or alongside) a plain map link.

Requires YANDEX_GEOCODER_API_KEY (core/config.py). Get a free key at
https://developer.tech.yandex.ru/ -> "Geocoder API". Until the key is set,
reverse_geocode() always returns None and callers fall back to a map link.
"""
import logging
import httpx
from core.config import YANDEX_GEOCODER_API_KEY

logger = logging.getLogger(__name__)

_GEOCODE_URL = "https://geocode-maps.yandex.ru/1.x/"


def reverse_geocode(lat, lng) -> str | None:
    """
    Returns a short Uzbek address string like
    "Toshkent tumani, Qorasaroy mahalla, Shohsada ko'chasi" for the given
    coordinates, or None if unavailable (no API key, request failed, etc).
    """
    if not YANDEX_GEOCODER_API_KEY or lat is None or lng is None:
        return None
    try:
        resp = httpx.get(
            _GEOCODE_URL,
            params={
                "apikey": YANDEX_GEOCODER_API_KEY,
                "geocode": f"{lng},{lat}",  # Yandex wants "lng,lat"
                "format": "json",
                "lang": "uz_UZ",
                "results": 1,
            },
            timeout=6,
        )
        if resp.status_code != 200:
            logger.warning(f"[geocode] Yandex returned {resp.status_code}: {resp.text[:200]}")
            return None
        data = resp.json()
        members = (
            data.get("response", {})
            .get("GeoObjectCollection", {})
            .get("featureMember", [])
        )
        if not members:
            return None
        geo_object = members[0]["GeoObject"]
        # Prefer the full formatted address text; fall back to the name.
        text = (
            geo_object.get("metaDataProperty", {})
            .get("GeocoderMetaData", {})
            .get("text")
        )
        return text or geo_object.get("name")
    except Exception as e:
        logger.warning(f"[geocode] reverse_geocode failed for ({lat},{lng}): {e}")
        return None
