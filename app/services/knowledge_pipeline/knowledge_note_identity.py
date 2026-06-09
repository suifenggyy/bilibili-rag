import hashlib
from typing import Dict

def build_knowledge_note_id(source_identity: Dict[str, str]) -> str:
    parts = [
        source_identity.get("source_url", ""),
        source_identity.get("published_date", ""),
        source_identity.get("persisted_first_seen_inbox_path", ""),
        source_identity.get("title", "")
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
