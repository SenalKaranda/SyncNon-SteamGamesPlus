from dataclasses import dataclass
import os


SOURCE_FOLDER = "folder"
SOURCE_XBOX = "xbox"
OWNERSHIP_TAG = "SyncNon-SteamPlus"
XBOX_TAG = "Xbox Game Pass"


@dataclass
class GameEntry:
    name: str
    search_name: str
    exe_path: str
    start_dir: str
    source: str
    icon_path: str = ""

    def normalized_start_dir(self) -> str:
        return normalize_path(self.start_dir)


def normalize_path(p: str) -> str:
    return os.path.normcase(os.path.normpath((p or "").strip('"')))


def split_path_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip().strip('"') for part in str(value).split(";") if part.strip()]


def paths_overlap(left: str, right: str) -> bool:
    left_norm = normalize_path(left)
    right_norm = normalize_path(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True
    sep = os.sep
    return left_norm.startswith(right_norm + sep) or right_norm.startswith(left_norm + sep)


def merge_game_entries(xbox_entries: list[GameEntry], folder_entries: list[GameEntry]) -> list[GameEntry]:
    """Prefer Xbox entries when the same install is also found by the folder scan."""
    merged: list[GameEntry] = []
    seen_start_dirs: set[str] = set()

    for entry in xbox_entries:
        key = entry.normalized_start_dir()
        if key in seen_start_dirs:
            continue
        seen_start_dirs.add(key)
        merged.append(entry)

    for entry in folder_entries:
        if any(paths_overlap(entry.start_dir, xbox.start_dir) for xbox in xbox_entries):
            continue
        key = entry.normalized_start_dir()
        if key in seen_start_dirs:
            continue
        seen_start_dirs.add(key)
        merged.append(entry)

    return merged
