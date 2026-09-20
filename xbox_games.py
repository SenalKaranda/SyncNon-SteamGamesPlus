import logging
import os
import string
import xml.etree.ElementTree as ET

from game_entry import GameEntry, SOURCE_XBOX, normalize_path, split_path_list

GAMING_ROOT_HEADER = b"RGBX"
GAME_LAUNCH_HELPER = "gamelaunchhelper.exe"
DEFAULT_XBOX_FOLDER_NAMES = ("XboxGames", "Xbox Games")

logger = logging.getLogger(__name__)


def _log(message: str, level: int = logging.INFO):
    logger.log(level, message)


def iter_drive_roots() -> list[str]:
    roots: list[str] = []
    if os.name != "nt":
        return roots

    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        try:
            if os.path.isdir(root):
                roots.append(root)
        except OSError:
            continue
    return roots


def parse_gaming_root(path: str) -> str | None:
    """Return the relative Xbox games folder name stored in a .GamingRoot file."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return None

    if len(data) < 8:
        return None

    relative_name = None
    if data.startswith(GAMING_ROOT_HEADER):
        relative_name = _decode_utf16le_z(data[8:])
    if not relative_name:
        relative_name = _decode_utf16le_z(data)

    if not relative_name:
        return None

    relative_name = relative_name.strip().strip("\x00").replace("/", os.sep).replace("\\", os.sep)
    if os.path.isabs(relative_name):
        return relative_name
    return relative_name


def _decode_utf16le_z(data: bytes) -> str | None:
    if not data:
        return None
    try:
        text = data.decode("utf-16le")
    except UnicodeDecodeError:
        return None
    return text.split("\x00", 1)[0].strip() or None


def find_xbox_roots(extra_roots: str | None = None, drive_roots: list[str] | None = None) -> list[str]:
    roots: list[str] = []
    seen: set[str] = set()

    def add_root(path: str | None):
        if not path:
            return
        try:
            if not os.path.isdir(path):
                return
        except OSError:
            return
        key = normalize_path(path)
        if key in seen:
            return
        seen.add(key)
        roots.append(os.path.normpath(path))

    for drive in drive_roots if drive_roots is not None else iter_drive_roots():
        gaming_root_file = os.path.join(drive, ".GamingRoot")
        parsed = parse_gaming_root(gaming_root_file) if os.path.isfile(gaming_root_file) else None
        if parsed:
            add_root(parsed if os.path.isabs(parsed) else os.path.join(drive, parsed))
        for folder_name in DEFAULT_XBOX_FOLDER_NAMES:
            add_root(os.path.join(drive, folder_name))

    for extra in split_path_list(extra_roots):
        add_root(extra)

    return roots


def find_gamelaunchhelper(game_dir: str) -> str | None:
    content_dir = _find_child_dir(game_dir, "content")
    if content_dir:
        helper = _find_child_file(content_dir, GAME_LAUNCH_HELPER)
        if helper:
            return helper

    try:
        for root, _dirs, files in os.walk(game_dir):
            for filename in files:
                if filename.lower() == GAME_LAUNCH_HELPER:
                    return os.path.join(root, filename)
    except OSError:
        return None
    return None


IMAGE_EXTENSIONS = (".ico", ".png", ".jpg", ".jpeg")


def find_store_icon(start_dir: str) -> str | None:
    """Find StoreIcon (or StoreLogo fallback) in a game Content folder."""
    if not start_dir or not os.path.isdir(start_dir):
        return None

    try:
        filenames = os.listdir(start_dir)
    except OSError:
        return None

    for prefix in ("StoreIcon", "StoreLogo"):
        chosen = _choose_image_with_prefix(start_dir, filenames, prefix)
        if chosen:
            return chosen

    config_logo = _read_shell_visual_path(start_dir, "StoreLogo")
    if config_logo:
        path = config_logo if os.path.isabs(config_logo) else os.path.join(start_dir, config_logo)
        if os.path.isfile(path):
            return os.path.normpath(path)

    return None


def _choose_image_with_prefix(start_dir: str, filenames: list[str], prefix: str) -> str | None:
    matches = []
    exact = []
    prefix_lower = prefix.lower()
    for name in filenames:
        stem, ext = os.path.splitext(name)
        if ext.lower() not in IMAGE_EXTENSIONS:
            continue
        stem_lower = stem.lower()
        if stem_lower == prefix_lower or stem_lower.startswith(f"{prefix_lower}.") or stem_lower.startswith(f"{prefix_lower}-"):
            path = os.path.join(start_dir, name)
            matches.append(path)
            if stem_lower == prefix_lower:
                exact.append(path)
    return _prefer_icon_file(exact or matches)


def _prefer_icon_file(paths: list[str]) -> str | None:
    if not paths:
        return None

    def sort_key(path: str):
        ext = os.path.splitext(path)[1].lower()
        ico_rank = 0 if ext == ".ico" else 1
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        return (ico_rank, -size, os.path.basename(path).lower())

    return os.path.normpath(sorted(paths, key=sort_key)[0])


def _read_shell_visual_path(start_dir: str, attribute: str) -> str | None:
    config_path = _find_microsoft_game_config(start_dir)
    if not config_path:
        return None
    try:
        tree = ET.parse(config_path)
        root = tree.getroot()
    except (OSError, ET.ParseError):
        return None

    for element in root.iter():
        if _local_tag(element.tag) != "ShellVisuals":
            continue
        value = (element.get(attribute) or "").strip()
        if value:
            return value.replace("/", os.sep)
    return None


def read_display_name(start_dir: str, fallback: str) -> str:
    config_path = _find_microsoft_game_config(start_dir)
    if not config_path:
        return fallback

    try:
        tree = ET.parse(config_path)
        root = tree.getroot()
    except (OSError, ET.ParseError):
        return fallback

    for element in root.iter():
        if _local_tag(element.tag) != "ShellVisuals":
            continue
        display_name = (element.get("DefaultDisplayName") or "").strip()
        if display_name:
            return display_name
        for child in list(element):
            if _local_tag(child.tag) in {"DefaultDisplayName", "DisplayName"} and (child.text or "").strip():
                return child.text.strip()

    for element in root.iter():
        if _local_tag(element.tag) in {"DefaultDisplayName", "DisplayName"} and (element.text or "").strip():
            return element.text.strip()

    return fallback


def discover_xbox_games(extra_roots: str | None = None, drive_roots: list[str] | None = None) -> list[GameEntry]:
    entries: list[GameEntry] = []
    seen: set[str] = set()

    for xbox_root in find_xbox_roots(extra_roots=extra_roots, drive_roots=drive_roots):
        try:
            child_names = os.listdir(xbox_root)
        except OSError as exc:
            _log(f"Could not read Xbox Games folder {xbox_root}: {exc}", logging.ERROR)
            continue

        for child_name in child_names:
            game_dir = os.path.join(xbox_root, child_name)
            try:
                if not os.path.isdir(game_dir):
                    continue
            except OSError:
                continue

            helper = find_gamelaunchhelper(game_dir)
            if not helper:
                _log(f"Skipping Xbox folder without {GAME_LAUNCH_HELPER}: {game_dir}")
                continue

            start_dir = os.path.dirname(helper)
            key = normalize_path(start_dir)
            if key in seen:
                continue
            seen.add(key)

            folder_name = child_name
            search_name = read_display_name(start_dir, folder_name)
            icon_path = find_store_icon(start_dir) or ""
            entries.append(
                GameEntry(
                    name=folder_name,
                    search_name=search_name,
                    exe_path=helper,
                    start_dir=start_dir,
                    source=SOURCE_XBOX,
                    icon_path=icon_path,
                )
            )
            _log(f"Found Xbox Game Pass game: {search_name} ({helper})")

    return entries


def _local_tag(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _find_child_dir(parent: str, name: str) -> str | None:
    try:
        for child in os.listdir(parent):
            if child.lower() == name.lower():
                path = os.path.join(parent, child)
                if os.path.isdir(path):
                    return path
    except OSError:
        return None
    return None


def _find_child_file(parent: str, name: str) -> str | None:
    try:
        for child in os.listdir(parent):
            if child.lower() == name.lower():
                path = os.path.join(parent, child)
                if os.path.isfile(path):
                    return path
    except OSError:
        return None
    return None


def _find_microsoft_game_config(start_dir: str) -> str | None:
    direct = os.path.join(start_dir, "MicrosoftGame.config")
    if os.path.isfile(direct):
        return direct
    found = _find_child_file(start_dir, "MicrosoftGame.config")
    if found:
        return found
    parent = os.path.dirname(start_dir)
    if parent and os.path.isdir(parent):
        return _find_child_file(parent, "MicrosoftGame.config")
    return None
