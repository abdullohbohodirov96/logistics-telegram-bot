"""
Reverse geocoding via Yandex Geocoder API — turns a delivered_lat/delivered_lng
pair into just the rayon (tuman/district) name, used in the finish
notification instead of (or alongside) a plain map link. Dispatcher only
asked for the rayon, not a full street address, so that's all this returns —
simpler and less likely to come back oddly formatted than a full address
string would be.

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
    Returns just the rayon/district name (e.g. "Toshkent tumani") for the
    given coordinates, or None if unavailable (no API key, request failed,
    coordinates fall outside any known district, etc).
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
        meta = geo_object.get("metaDataProperty", {}).get("GeocoderMetaData", {})

        # Yandex breaks the address into components tagged by admin level:
        # country / province / area (= tuman/rayon) / locality / district
        # (= mahalla) / street / house. "area" is the rayon level for
        # Uzbekistan addresses — that's specifically what was asked for.
        components = meta.get("Address", {}).get("Components", [])
        by_kind = {c.get("kind"): c.get("name") for c in components if c.get("kind") and c.get("name")}
        rayon = by_kind.get("area") or by_kind.get("district") or by_kind.get("locality")
        if rayon:
            return rayon

        # Fallback if Yandex didn't break it into components for some reason
        # (rare) — better to show the full text than nothing.
        return meta.get("text") or geo_object.get("name")
    except Exception as e:
        logger.warning(f"[geocode] reverse_geocode failed for ({lat},{lng}): {e}")
        return None
