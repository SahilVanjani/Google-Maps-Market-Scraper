from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class ScrapeState:
    processed_urls: set[str] = field(default_factory=set)
    lead_keys: set[str] = field(default_factory=set)
    completed_queries: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, path: str | Path) -> "ScrapeState":
        state_path = Path(path)
        if not state_path.exists():
            return cls()

        data = json.loads(state_path.read_text(encoding="utf-8"))
        return cls(
            processed_urls=set(data.get("processed_urls", [])),
            lead_keys=set(data.get("lead_keys", [])),
            completed_queries=set(data.get("completed_queries", [])),
        )

    def save(self, path: str | Path) -> None:
        state_path = Path(path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "processed_urls": sorted(self.processed_urls),
            "lead_keys": sorted(self.lead_keys),
            "completed_queries": sorted(self.completed_queries),
        }
        state_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

