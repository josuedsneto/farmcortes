from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo


DEFAULT_POLICY = Path(__file__).resolve().parent.parent / "editorial-policy.json"


def load_policy(path: Path | None = None) -> dict:
    configured = path or Path(os.getenv("FARMCORTES_EDITORIAL_POLICY", DEFAULT_POLICY))
    return json.loads(configured.read_text(encoding="utf-8"))


def local_date(policy: dict, now: datetime | None = None) -> date:
    timezone = ZoneInfo(str(policy.get("timezone") or "America/Sao_Paulo"))
    current = now or datetime.now(timezone)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone)
    return current.astimezone(timezone).date()


def active_campaign(policy: dict, now: datetime | None = None) -> dict | None:
    today = local_date(policy, now)
    for campaign in policy.get("campaigns", []):
        if date.fromisoformat(campaign["start"]) <= today <= date.fromisoformat(campaign["end"]):
            return campaign
    return None


def discovery_topics(policy: dict, *, count: int, now: datetime | None = None) -> list[dict]:
    if count < 1:
        return []
    defaults = [dict(item) for item in policy.get("default_topics", [])]
    campaign = active_campaign(policy, now)
    if not campaign:
        return defaults
    campaign_slots = max(1, min(count, round(count * float(campaign.get("share", 0.5)))))
    campaign_topics = [
        {"theme": campaign["theme"], "query": query, "campaign": campaign["id"]}
        for query in campaign.get("queries", [])[:campaign_slots]
    ]
    return campaign_topics + defaults[:max(0, count - campaign_slots)]
