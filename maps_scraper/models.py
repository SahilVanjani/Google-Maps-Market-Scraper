from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import re


def normalized_key_part(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


@dataclass(slots=True)
class BusinessRecord:
    search_query: str
    name: str = ""
    category: str = ""
    address: str = ""
    phone: str = ""
    website: str = ""
    emails: list[str] = field(default_factory=list)
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_row(self) -> dict[str, str]:
        row = asdict(self)
        row["emails"] = ", ".join(self.emails)
        return row

    def lead_key(self) -> str:
        parts = [self.name, self.address, self.phone, self.website]
        normalized = [normalized_key_part(part) for part in parts if part]
        return "|".join(part for part in normalized if part)


@dataclass(frozen=True, slots=True)
class PlaceLink:
    name: str
    url: str
    position: int = 0


@dataclass(frozen=True, slots=True)
class ScrapedPlace:
    place: PlaceLink
    record: BusinessRecord
