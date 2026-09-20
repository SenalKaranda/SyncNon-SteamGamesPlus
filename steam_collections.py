from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

COLLECTION_NAME = "Xbox Game Pass"
COLLECTION_ID = "plus-xbox-game-pass"


def collection_appid(appid) -> int:
    if isinstance(appid, str):
        appid = int(appid)
    return appid & 0xffffffff


def merge_managed_appids(existing_added: list[int], previous_managed: list[int], current_managed: list[int]) -> list[int]:
    previous = {collection_appid(value) for value in previous_managed}
    current = {collection_appid(value) for value in current_managed}
    result = []
    seen = set()
    for value in existing_added:
        appid = collection_appid(value)
        if appid in seen:
            continue
        if appid in previous and appid not in current:
            continue
        seen.add(appid)
        result.append(appid)
    for value in current_managed:
        appid = collection_appid(value)
        if appid in seen:
            continue
        seen.add(appid)
        result.append(appid)
    return result


def encode_collection_id(name: str) -> str:
    encoded = base64.b64encode(name.encode("utf-8")).decode("ascii")
    encoded = encoded.replace("+", "-").replace("/", "_").rstrip("=")
    return f"plus-{encoded}"


def _parse_entry(item) -> tuple[str | None, dict | None]:
    if not isinstance(item, (list, tuple)) or len(item) < 2:
        return None, None
    key = item[0]
    payload = item[1]
    if not isinstance(key, str) or not isinstance(payload, dict):
        return None, None
    return key, payload


def find_named_collection(cloud_data: list, name: str) -> tuple[int | None, str | None, dict | None]:
    wanted = name.casefold()
    for index, item in enumerate(cloud_data):
        key, payload = _parse_entry(item)
        if not key or not key.startswith("user-collections.") or not payload or payload.get("is_deleted"):
            continue
        raw_value = payload.get("value")
        if not raw_value:
            continue
        try:
            value = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
        except (TypeError, json.JSONDecodeError):
            continue
        if str(value.get("name", "")).casefold() == wanted:
            return index, key.removeprefix("user-collections."), value
        if key.removeprefix("user-collections.") in {COLLECTION_ID, encode_collection_id(name)}:
            return index, key.removeprefix("user-collections."), value
    return None, None, None


def next_version(cloud_data: list, timestamp: int) -> str:
    highest = 0
    for item in cloud_data:
        _key, payload = _parse_entry(item)
        if not payload:
            continue
        version = str(payload.get("version") or "0")
        if version.isdigit():
            highest = max(highest, int(version))
    return str(max(highest + 1, timestamp))


def build_collection_entry(collection_id: str, name: str, added: list[int], timestamp: int, version: str) -> list:
    key = f"user-collections.{collection_id}"
    value = {
        "id": collection_id,
        "name": name,
        "added": [collection_appid(appid) for appid in added],
        "removed": [],
    }
    return [key, {
        "key": key,
        "timestamp": timestamp,
        "value": json.dumps(value, separators=(",", ":")),
        "version": version,
        "conflictResolutionMethod": "custom",
        "strMethodId": "union-collections",
    }]


def upsert_xbox_collection(
    cloud_data: list | None,
    current_appids: list[int],
    previous_managed: list[int],
    timestamp: int | None = None,
    name: str = COLLECTION_NAME,
) -> tuple[list, list[int]]:
    data = list(cloud_data or [])
    now = int(timestamp if timestamp is not None else time.time())
    index, collection_id, value = find_named_collection(data, name)
    collection_id = collection_id or COLLECTION_ID
    existing_added = list((value or {}).get("added") or [])
    merged = merge_managed_appids(existing_added, previous_managed, current_appids)
    entry = build_collection_entry(collection_id, name, merged, now, next_version(data, now))
    if index is None:
        data.append(entry)
    else:
        data[index] = entry
    return data, merged


def resolve_cloud_storage_path(userdata_config_dir: str) -> Path:
    cloud_dir = Path(userdata_config_dir) / "cloudstorage"
    namespaces_path = cloud_dir / "cloud-storage-namespaces.json"
    namespace = 1
    if namespaces_path.is_file():
        try:
            namespaces = json.loads(namespaces_path.read_text(encoding="utf-8"))
            ranked = []
            for item in namespaces:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    ranked.append((int(item[1] or 0), int(item[0])))
            if ranked:
                ranked.sort(reverse=True)
                if ranked[0][0] != 0:
                    namespace = ranked[0][1]
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            namespace = 1
    return cloud_dir / f"cloud-storage-namespace-{namespace}.json"


def write_xbox_collection(
    userdata_config_dir: str,
    current_appids: list[int],
    previous_managed: list[int],
) -> list[int]:
    path = resolve_cloud_storage_path(userdata_config_dir)
    os.makedirs(path.parent, exist_ok=True)
    cloud_data = []
    if path.is_file():
        raw = path.read_text(encoding="utf-8")
        if raw.strip():
            loaded = json.loads(raw)
            if isinstance(loaded, list):
                cloud_data = loaded
    updated, managed = upsert_xbox_collection(cloud_data, current_appids, previous_managed)
    path.write_text(json.dumps(updated), encoding="utf-8")
    return managed
