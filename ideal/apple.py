from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation

from .http import HttpClient
from .models import OfficialApp


class AppleLookup:
    URL = "https://itunes.apple.com/lookup"

    def __init__(self, client: HttpClient, batch_size: int = 50):
        self.client = client
        self.batch_size = max(1, min(batch_size, 100))
        self._cache: dict[tuple[str, str], OfficialApp | None] = {}
        self.batch_metrics: list[dict[str, int | float | str | bool]] = []

    def lookup_many(
        self, app_ids: list[str], region: str
    ) -> dict[str, OfficialApp | None]:
        clean_ids = list(dict.fromkeys(str(x).strip() for x in app_ids if str(x).strip()))
        missing = [app_id for app_id in clean_ids if (app_id, region) not in self._cache]
        for offset in range(0, len(missing), self.batch_size):
            batch = missing[offset : offset + self.batch_size]
            started = time.perf_counter()
            attempts_before = self.client.get_attempts
            try:
                payload = self.client.get(
                    self.URL,
                    params={
                        "id": ",".join(batch),
                        "country": region,
                        "entity": "software",
                    },
                ).json()
            except Exception:
                self.batch_metrics.append(
                    {
                        "region": region.upper(),
                        "ids": len(batch),
                        "elapsed_ms": round((time.perf_counter() - started) * 1000),
                        "attempts": self.client.get_attempts - attempts_before,
                        "ok": False,
                    }
                )
                raise
            self.batch_metrics.append(
                {
                    "region": region.upper(),
                    "ids": len(batch),
                    "elapsed_ms": round((time.perf_counter() - started) * 1000),
                    "attempts": self.client.get_attempts - attempts_before,
                    "ok": True,
                }
            )
            found: set[str] = set()
            for item in payload.get("results", []):
                app_id = str(item.get("trackId", "")).strip()
                if not app_id:
                    continue
                found.add(app_id)
                try:
                    price = Decimal(str(item.get("price", "0")))
                except InvalidOperation:
                    continue
                self._cache[(app_id, region)] = OfficialApp(
                    app_id=app_id,
                    region=region,
                    title=str(item.get("trackName", "")).strip() or f"App {app_id}",
                    price=price,
                    currency=str(item.get("currency", "")).strip(),
                    formatted_price=str(item.get("formattedPrice", "")).strip(),
                    url=str(item.get("trackViewUrl", "")).strip(),
                    primary_genre_id=str(item.get("primaryGenreId", "")).strip(),
                    primary_genre_name=str(item.get("primaryGenreName", "")).strip(),
                    seller=str(item.get("sellerName", "")).strip(),
                    version=str(item.get("version", "")).strip(),
                    release_notes=str(item.get("releaseNotes", "")).strip(),
                    updated_at=str(item.get("currentVersionReleaseDate", "")).strip(),
                    minimum_os=str(item.get("minimumOsVersion", "")).strip(),
                    rating=float(item.get("averageUserRating", 0) or 0),
                    rating_count=int(item.get("userRatingCount", 0) or 0),
                    artwork_url=str(item.get("artworkUrl512", "")).strip(),
                    description=str(item.get("description", "")).strip(),
                    content_rating=str(item.get("contentAdvisoryRating", "")).strip(),
                    languages=[
                        str(x) for x in (item.get("languageCodesISO2A", []) or [])
                    ],
                )
            for app_id in batch:
                if app_id not in found:
                    self._cache[(app_id, region)] = None
        return {app_id: self._cache.get((app_id, region)) for app_id in clean_ids}

    def request_stats(self) -> dict[str, object]:
        return {
            "logical_batches": len(self.batch_metrics),
            "network_attempts": sum(
                int(metric["attempts"]) for metric in self.batch_metrics
            ),
            "requested_ids": sum(int(metric["ids"]) for metric in self.batch_metrics),
            "batches": [dict(metric) for metric in self.batch_metrics],
        }
