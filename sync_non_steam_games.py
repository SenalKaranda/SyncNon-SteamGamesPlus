import sys, os, re
from typing import Tuple

import vdf
import requests
import logging
import zlib
import json
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import quote
from gooey import Gooey, GooeyParser
import traceback

from game_entry import (
    GameEntry,
    OWNERSHIP_TAG,
    SOURCE_FOLDER,
    SOURCE_XBOX,
    XBOX_TAG,
    merge_game_entries,
    normalize_path,
    split_path_list,
)
from steamgrid_match import (
    GridCandidate,
    GridPick,
    extract_candidates,
    pick_best_candidate,
    prompt_grid_match_cli,
    prompt_grid_match_gui,
    rank_candidates,
    titles_look_similar,
)
from steam_collections import write_xbox_collection
from xbox_games import discover_xbox_games

os.environ.setdefault("PYTHONIOENCODING", "utf-8")


class SafeTextStream:
    def __init__(self, stream):
        self.stream = stream

    def write(self, text):
        if isinstance(text, str):
            text = text.encode("ascii", errors="backslashreplace").decode("ascii")
        return self.stream.write(text)

    def flush(self):
        return self.stream.flush()

    def isatty(self):
        return self.stream.isatty()

    def __getattr__(self, name):
        return getattr(self.stream, name)


def configure_text_stream(stream):
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (OSError, ValueError):
            pass
    return SafeTextStream(stream)


sys.stdout = configure_text_stream(sys.stdout)
sys.stderr = configure_text_stream(sys.stderr)


class SafeLogFormatter(logging.Formatter):
    def format(self, record):
        message = super().format(record)
        return message.encode("ascii", errors="backslashreplace").decode("ascii")


def decode_process_output(line: bytes, encoding: str) -> str:
    try:
        return line.decode(encoding)
    except UnicodeDecodeError:
        return line.decode("cp1252", errors="backslashreplace")


def patch_gooey_output_decoding():
    try:
        from gooey.gui import processor
    except ImportError:
        return

    if getattr(processor.ProcessController, "_syncnsg_decode_patch", False):
        return

    def _forward_stdout(self, process):
        while True:
            line = process.stdout.readline()
            if not line:
                break
            progress = self._extract_progress(line)

            processor.pub.send_message(processor.events.PROGRESS_UPDATE, progress=progress)
            if progress is None or self.hide_progress_msg is False:
                processor.pub.send_message(
                    processor.events.CONSOLE_UPDATE,
                    msg=decode_process_output(line, self.encoding),
                )
        processor.pub.send_message(processor.events.EXECUTION_COMPLETE)

    def _extract_progress(self, text):
        decoded_text = decode_process_output(text.strip(), self.encoding)
        if self.progress_regex:
            match = re.search(self.progress_regex, decoded_text)
            if match:
                self.result = self._calculate_progress(match)
        return self.result

    processor.ProcessController._forward_stdout = _forward_stdout
    processor.ProcessController._extract_progress = _extract_progress
    processor.ProcessController._syncnsg_decode_patch = True

STEAM_ID64_BASE = 76561197960265728
DEFAULT_STEAMDIR_PATH = r"C:\Program Files (x86)\Steam"
ORIGINAL_APP_CONFIG_DIRNAME = "SyncNonSteamGames"
PLUS_APP_CONFIG_DIRNAME = "SyncNonSteamGamesPlus"


def coerce_bool(value, default=True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return default


def is_default_steamdir_path(path: str) -> bool:
    return normalize_path(path) == normalize_path(DEFAULT_STEAMDIR_PATH)


def normalize_appid(appid):
    if isinstance(appid, str):
        appid = int(appid)
    return str(appid & 0xffffffff)


def escape_path(path):
    return path.replace("\\", "\\\\")


def saveJsonFile(filename, data):
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, 'w') as f:
        json.dump(data, f)


def readJsonFile(filename):
    if (os.path.isfile(filename)):
        with open(filename, 'r') as f:
            return json.loads(f.read())


def determineUserdataFolder(selected_steam_user_id=None):
    steam_users = get_steam_users()
    steam_user_data_ID = resolve_steam_user_id(selected_steam_user_id, steam_users)

    if not steam_user_data_ID:
        if len(steam_users) == 1:
            steam_user_data_ID = next(iter(steam_users))
        elif len(steam_users) > 1:
            raise ValueError("Multiple Steam users found. Please select one with --steam_user_id.")
        else:
            raise ValueError(f"No Steam userdata folders found in {steamdir_path}")

    return os.path.join(steamdir_path, "userdata", steam_user_data_ID, "config")


# Configure logging
log_handler = logging.StreamHandler(sys.stdout)
log_handler.setFormatter(SafeLogFormatter('%(asctime)s - %(levelname)s - %(message)s'))
logging.basicConfig(level=logging.INFO, handlers=[log_handler], force=True)
logger = logging.getLogger(__name__)

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

APP_CONFIG_DIR = os.path.join(os.environ.get("APPDATA", BASE_DIR), PLUS_APP_CONFIG_DIRNAME)
ORIGINAL_APP_CONFIG_DIR = os.path.join(os.environ.get("APPDATA", BASE_DIR), ORIGINAL_APP_CONFIG_DIRNAME)
CONFIG_JSON_FILENAME = "parameters.json"


def migrate_settings_from_original(app_config_path):
    if os.path.isfile(app_config_path):
        return

    original_config_path = os.path.join(ORIGINAL_APP_CONFIG_DIR, CONFIG_JSON_FILENAME)
    if not os.path.isfile(original_config_path):
        return

    original = readJsonFile(original_config_path) or {}
    migrated = {
        "game_installation_path": original.get("game_installation_path", ""),
        "steamgriddb_api_key": original.get("steamgriddb_api_key", ""),
        "steamdir_path": original.get("steamdir_path", ""),
        "steam_user_id": original.get("steam_user_id", ""),
        "scan_xbox": True,
        "xbox_games_path": "",
        "confirm_matches": True,
        "restart_steam": False,
        "xbox_collection": True,
    }
    saveJsonFile(app_config_path, migrated)
    logger.info(f"Migrated Steam settings from {original_config_path}")


def get_stored_parameters_json_filename():
    os.makedirs(APP_CONFIG_DIR, exist_ok=True)
    app_config_path = os.path.join(APP_CONFIG_DIR, CONFIG_JSON_FILENAME)
    legacy_config_path = os.path.join(BASE_DIR, CONFIG_JSON_FILENAME)
    if os.path.isfile(legacy_config_path) and not os.path.isfile(app_config_path):
        shutil.copy2(legacy_config_path, app_config_path)
        os.remove(legacy_config_path)

    migrate_settings_from_original(app_config_path)
    return app_config_path


storedParametersJSONFilename = get_stored_parameters_json_filename()
logger.info(f"Using parameters file: {storedParametersJSONFilename}")

storedParametersJSON = {}
storedParametersJSON = readJsonFile(storedParametersJSONFilename) or {}

game_installation_path = ""
steamgriddb_api_key = ""
steamdir_path = ""
steam_user_id = ""
scan_xbox = True
xbox_games_path = ""
confirm_matches = True
restart_steam = False
xbox_collection = True
grid_overrides = {}
xbox_collection_appids = []

##Taking them from the JSON if it exists
if storedParametersJSON:
    game_installation_path = storedParametersJSON.get("game_installation_path", "")
    steamgriddb_api_key = storedParametersJSON.get("steamgriddb_api_key", "")
    steamdir_path = storedParametersJSON.get("steamdir_path", "")
    steam_user_id = storedParametersJSON.get("steam_user_id", "")
    scan_xbox = coerce_bool(storedParametersJSON.get("scan_xbox", True), True)
    xbox_games_path = storedParametersJSON.get("xbox_games_path", "")
    confirm_matches = coerce_bool(storedParametersJSON.get("confirm_matches", True), True)
    restart_steam = coerce_bool(storedParametersJSON.get("restart_steam", False), False)
    xbox_collection = coerce_bool(storedParametersJSON.get("xbox_collection", True), True)
    grid_overrides = storedParametersJSON.get("grid_overrides", {}) or {}
    xbox_collection_appids = storedParametersJSON.get("xbox_collection_appids", []) or []
    if "steam_user_id" not in storedParametersJSON:
        storedParametersJSON["steam_user_id"] = ""
        saveJsonFile(storedParametersJSONFilename, storedParametersJSON)

totalGames = 0
currentGame = 0
review_uses_gui = True


def is_steam_running() -> bool:
    try:
        import psutil
    except ImportError:
        return False
    try:
        for process in psutil.process_iter(["name"]):
            if (process.info.get("name") or "").lower() == "steam.exe":
                return True
    except Exception:
        return False
    return False


def warn_if_steam_running():
    if is_steam_running():
        logger.warning(
            "Steam is running. Steam may overwrite shortcuts.vdf when it exits. Restart Steam after this sync, or enable Restart Steam automatically."
        )


def stop_steam(timeout_seconds: float = 30.0) -> bool:
    steam_exe = os.path.join(steamdir_path, "steam.exe")
    if os.path.isfile(steam_exe):
        try:
            subprocess.run([steam_exe, "-shutdown"], check=False, timeout=10)
        except Exception as exc:
            logger.warning(f"Could not ask Steam to shut down cleanly: {exc}")

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not is_steam_running():
            logger.info("Steam has shut down.")
            return True
        time.sleep(0.5)

    try:
        import psutil
        for process in list(psutil.process_iter(["name"])):
            if (process.info.get("name") or "").lower() == "steam.exe":
                process.terminate()
        deadline = time.time() + 8
        while time.time() < deadline:
            if not is_steam_running():
                logger.info("Steam was terminated.")
                return True
            time.sleep(0.4)
    except Exception as exc:
        logger.warning(f"Could not terminate Steam: {exc}")
    logger.error("Steam is still running.")
    return False


def start_steam():
    steam_exe = os.path.join(steamdir_path, "steam.exe")
    if not os.path.isfile(steam_exe):
        logger.error(f"Steam executable not found at {steam_exe}")
        return
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        subprocess.Popen(
            [steam_exe],
            cwd=steamdir_path,
            close_fds=True,
            creationflags=flags,
        )
        logger.info("Started Steam.")
    except Exception as exc:
        logger.error(f"Could not start Steam: {exc}")


def collect_xbox_appids(shortcuts) -> list[int]:
    appids = []
    for shortcut in (shortcuts or {}).get("shortcuts", {}).values():
        if not is_plus_shortcut(shortcut):
            continue
        if XBOX_TAG not in shortcut_tags(shortcut):
            continue
        try:
            appids.append(int(normalize_appid(shortcut["appid"])))
        except (KeyError, TypeError, ValueError):
            continue
    return appids


def save_xbox_collection_appids(appids):
    global xbox_collection_appids
    data = readJsonFile(storedParametersJSONFilename) or {}
    xbox_collection_appids = [int(value) for value in appids]
    data["xbox_collection_appids"] = xbox_collection_appids
    saveJsonFile(storedParametersJSONFilename, data)


def shortcut_tags(shortcut) -> list[str]:
    tags = shortcut.get("tags") or {}
    if isinstance(tags, dict):
        return [str(value) for value in tags.values()]
    if isinstance(tags, list):
        return [str(value) for value in tags]
    return []


def is_plus_shortcut(shortcut) -> bool:
    return OWNERSHIP_TAG in shortcut_tags(shortcut)


def build_shortcut_tags(source: str) -> dict[str, str]:
    tags = {"0": OWNERSHIP_TAG}
    if source == SOURCE_XBOX:
        tags["1"] = XBOX_TAG
    return tags


def read_folder_games() -> list[GameEntry]:
    entries: list[GameEntry] = []
    if not game_installation_path:
        return entries

    try:
        for base_path in split_path_list(game_installation_path):
            if not os.path.isdir(base_path):
                continue
            for subfolder in os.listdir(base_path):
                game_dir = os.path.join(base_path, subfolder)
                if not os.path.isdir(game_dir):
                    continue
                exe_file = find_largest_exe(game_dir)
                entries.append(
                    GameEntry(
                        name=subfolder,
                        search_name=subfolder,
                        exe_path=exe_file or "",
                        start_dir=game_dir,
                        source=SOURCE_FOLDER,
                    )
                )
    except Exception as e:
        logger.error(f"Error reading game installation directory {game_installation_path}: {e}")
        return []
    return entries


def read_current_games() -> list[GameEntry]:
    """Read games from configured folders and optional Xbox Game Pass installs."""
    xbox_entries: list[GameEntry] = []
    if scan_xbox:
        logger.info("Scanning Xbox Game Pass installs...")
        xbox_entries = discover_xbox_games(extra_roots=xbox_games_path)
        logger.info(f"Xbox Game Pass games found: {len(xbox_entries)}")

    folder_entries = read_folder_games()
    current_games = merge_game_entries(xbox_entries, folder_entries)

    global totalGames
    totalGames = len(current_games)
    logger.info(f"Total number of games: {totalGames}")
    return current_games


def generate_appid(game_name, exe_path):
    """Generate a unique appid for the game based on its exe path and name."""
    unique_name = (exe_path + game_name).encode('utf-8')
    legacy_id = zlib.crc32(unique_name) | 0x80000000
    return str(legacy_id)


def getGridImageURLBySize(json, image_type):
    imageSize = {'grid': (600, 900), 'home': (920, 430)}

    ##Locating the image type by size, and taking the first result
    url = [x for x in json['data'] if
           x and x['width'] == imageSize[image_type][0] and x['height'] == imageSize[image_type][1]]
    if (url):
        return url[0]['url']
    else:
        return None


def fetch_steamgriddb_image(game_id, image_type):
    """Fetch a single image (first available) of specified type from SteamGridDB."""
    headers = {
        'Authorization': f'Bearer {steamgriddb_api_key}'
    }
    if image_type == 'hero':
        base_url = f'https://www.steamgriddb.com/api/v2/heroes/game/{game_id}'
    elif image_type == "home":
        base_url = f'https://www.steamgriddb.com/api/v2/grids/game/{game_id}'
    else:
        base_url = f'https://www.steamgriddb.com/api/v2/{image_type}s/game/{game_id}'
    response = requests.get(base_url, headers=headers)
    logger.info(f"Fetching {image_type} for game ID: {game_id}, URL: {base_url}, Status Code: {response.status_code}")

    if response.status_code == 200:
        data = response.json()
        if data['success'] and data['data']:
            if image_type == "home" or image_type == 'grid':
                return getGridImageURLBySize(data, image_type)
            return data['data'][0]['url']  # Return the URL of the first image found

    logger.error(f"Failed to fetch {image_type} for game ID: {game_id}")
    return None


def download_image(url, local_path):
    """Download an image from URL and save it locally."""
    try:
        response = requests.get(url)
        if response.status_code == 200:
            with open(local_path, 'wb') as f:
                f.write(response.content)
            logger.info(f"Downloaded image from {url} to {local_path}")
            return True
    except Exception as e:
        logger.error(f"Failed to download image from {url}: {e}")
    return False


def save_images(appid, game_id):
    """Save grid, hero, and logo images for the game."""
    image_types = ['grid', 'hero', 'logo', 'home']
    for image_type in image_types:
        url = fetch_steamgriddb_image(game_id, image_type)
        if url:
            extension = os.path.splitext(url)[1]

            if image_type == 'grid':
                image_path = os.path.join(grid_folder, f'{appid}p{extension}')
            elif image_type == 'hero':
                image_path = os.path.join(grid_folder, f'{appid}_hero{extension}')
            elif image_type == 'logo':
                image_path = os.path.join(grid_folder, f'{appid}_logo{extension}')
            elif image_type == 'home':
                image_path = os.path.join(grid_folder, f'{appid}{extension}')

            logger.info(f"Saving {image_type} image for appid {appid} from {url} to {image_path}")
            if not os.path.exists(image_path):
                if download_image(url, image_path):
                    logger.info(f"Downloaded {image_type} image for appid {appid} from {url}")


def find_largest_exe(game_dir):
    largest_file = None
    largest_size = 0
    # To avoid the uninstaller or some other exe to be used, mostly in Unity games
    exceptions = ["unins", "unity", "redist"]

    for root, dirs, files in os.walk(game_dir):
        for file in files:
            if file.endswith(".exe") and not any([x in file.lower() for x in exceptions]):
                file_path = os.path.join(root, file)
                file_size = os.path.getsize(file_path)
                if file_size > largest_size:
                    largest_size = file_size
                    largest_file = file_path

    return largest_file

def camel_case_to_split_name(exe_name: str) -> str:
    """Convert camelCase or PascalCase to a space-separated string."""
    split_name = re.sub(r'(?<!^)(?=[A-Z])|(?<=[A-Za-z])(?=\d)', ' ', exe_name).strip()
    return split_name

def send_steam_grid_db_request(exe_name: str):
    headers = {
        'Authorization': f'Bearer {steamgriddb_api_key}'
    }
    escaped_exe_name = quote(exe_name, safe="")
    search_url = f'https://www.steamgriddb.com/api/v2/search/autocomplete/{escaped_exe_name}'
    response = requests.get(search_url, headers=headers)
    logger.info(f"Searching SteamGridDB for {exe_name}, URL: {search_url}, Status Code: {response.status_code}")
    return response

def search_steam_grid_candidates(query: str) -> list[GridCandidate]:
    if not query:
        return []
    response = send_steam_grid_db_request(query)
    if response.status_code != 200:
        return []
    try:
        payload = response.json()
    except ValueError:
        return []
    return rank_candidates(query, extract_candidates(payload))


def retrieve_steam_grid_data(exe_name: str) -> Tuple[str, str]:
    best = pick_best_candidate(exe_name, search_steam_grid_candidates(exe_name))
    if not best:
        return None, None
    logger.info(best.game_name)
    return best.game_id, best.game_name


def quoted_shortcut_path(path: str) -> str:
    return f'"{path}"' if path else ""


def shortcut_icon_path(shortcut) -> str:
    return normalize_path(shortcut.get("icon", ""))


def load_grid_override(start_dir: str) -> GridCandidate | None:
    override = (grid_overrides or {}).get(normalize_path(start_dir))
    if not isinstance(override, dict):
        return None
    game_id = str(override.get("game_id") or "").strip() or None
    game_name = str(override.get("game_name") or "").strip() or None
    if not game_name:
        return None
    return GridCandidate(game_id=game_id or "", game_name=game_name)


def save_grid_override(start_dir: str, game_id: str | None, game_name: str):
    global grid_overrides
    data = readJsonFile(storedParametersJSONFilename) or {}
    overrides = data.get("grid_overrides") or {}
    overrides[normalize_path(start_dir)] = {
        "game_id": str(game_id or ""),
        "game_name": game_name,
    }
    data["grid_overrides"] = overrides
    grid_overrides = overrides
    saveJsonFile(storedParametersJSONFilename, data)


def should_review_match(entry: GameEntry, existing_appname: str | None) -> bool:
    if not confirm_matches:
        return False
    if load_grid_override(entry.start_dir):
        return False
    if existing_appname and titles_look_similar(entry.search_name or entry.name, existing_appname):
        return False
    return True


def resolve_grid_match(entry: GameEntry, existing_appname: str | None, use_gui: bool) -> GridPick | None:
    query = entry.search_name or entry.name
    override = load_grid_override(entry.start_dir)
    if override:
        logger.info(f"Using saved SteamGridDB match: {override.game_name}")
        return GridPick(game_id=override.game_id or None, game_name=override.game_name)

    candidates = search_steam_grid_candidates(query)
    if not candidates:
        split_name = camel_case_to_split_name(query)
        if split_name != query:
            candidates = search_steam_grid_candidates(split_name)

    if not should_review_match(entry, existing_appname):
        best = pick_best_candidate(query, candidates)
        if best:
            return GridPick(game_id=best.game_id, game_name=best.game_name)
        if existing_appname:
            return None
        return GridPick(skip=True)

    logger.info(f"Waiting for SteamGridDB match confirmation for {query}")
    prompt_fn = prompt_grid_match_gui if use_gui else prompt_grid_match_cli
    try:
        pick = prompt_fn(
            discovered_name=query,
            folder_name=entry.name,
            current_appname=existing_appname,
            candidates=candidates,
            search_fn=search_steam_grid_candidates,
        )
    except Exception:
        logger.error("Interactive SteamGridDB prompt failed; using the suggested match.")
        logger.error(traceback.format_exc())
        best = pick_best_candidate(query, candidates)
        if not best:
            return GridPick(skip=True)
        pick = GridPick(game_id=best.game_id, game_name=best.game_name)

    if not pick.skip and pick.game_name:
        save_grid_override(entry.start_dir, pick.game_id, pick.game_name)
    return pick


def update_shortcuts(current_games, steam_user_data_path):
    """Update the Steam shortcuts with new and removed games, and fetch/update images."""

    global grid_folder
    grid_folder = os.path.join(steam_user_data_path, 'grid')  # Folder to store grid images
    # Ensure the grid folder exists
    Path(grid_folder).mkdir(parents=True, exist_ok=True)

    shortcuts_file = Path(steam_user_data_path) / 'shortcuts.vdf'

    try:
        shortcuts = {"shortcuts": {}}

        if shortcuts_file.is_file():
            with open(shortcuts_file, 'rb') as shortcuts_vdf:
                shortcuts = vdf.binary_load(shortcuts_vdf)

        current_games_norm = {entry.normalized_start_dir() for entry in current_games}

        # Remove shortcuts for games no longer in the installation directory
        for idx, params_json in list(shortcuts.get('shortcuts', {}).items()):
            shortcut_path_norm = normalize_path(params_json.get("StartDir", ""))
            game_label = params_json.get("appname") or os.path.basename(shortcut_path_norm)
            if shortcut_path_norm not in current_games_norm and shortcut_path_norm and is_plus_shortcut(params_json):
                logger.info(
                    f"Game {game_label} from path: {shortcut_path_norm} is on steam but not in the installation directory, removing it from shortcuts")
                appid = normalize_appid(params_json["appid"])
                # Remove images associated with the game
                for image_type in ['p', '_hero', '_logo', 'home']:
                    for ext in ['.jpg', '.png']:
                        if (image_type == 'home'):
                            image_path = os.path.join(grid_folder, f'{appid}{ext}')
                        else:
                            image_path = os.path.join(grid_folder, f'{appid}{image_type}{ext}')
                        if os.path.exists(image_path):
                            os.remove(image_path)
                            logger.info(f"Removed {image_type} image for game: {game_label}")

                del shortcuts['shortcuts'][idx]
                logger.info(f"Removed shortcut for game: {game_label}")

        # Reindex shortcuts to sequential numeric keys
        shortcuts['shortcuts'] = {
            str(i): v for i, v in enumerate(shortcuts.get('shortcuts', {}).values())
        }

        existing_by_start_dir = {
            normalize_path(shortcut.get('StartDir', '')): shortcut
            for shortcut in shortcuts['shortcuts'].values()
        }

        global confirm_matches
        confirm_for_run = confirm_matches

        # Add or update games in shortcuts
        for entry in current_games:
            try:
                game_name = entry.search_name or entry.name

                global currentGame
                currentGame += 1
                logger.info("")
                logger.info(f"Current game: {game_name}")
                logger.info(f"Games processed: {currentGame}/{totalGames}")

                matching_shortcut = existing_by_start_dir.get(entry.normalized_start_dir())
                existing_appname = matching_shortcut.get("appname") if matching_shortcut else None
                confirm_matches = confirm_for_run
                if matching_shortcut:
                    logger.info(f"{game_name} already in Steam as {existing_appname}")
                    if entry.icon_path and shortcut_icon_path(matching_shortcut) != normalize_path(entry.icon_path):
                        matching_shortcut["icon"] = quoted_shortcut_path(entry.icon_path)
                        logger.info(f"Updated shortcut icon for {game_name}: {entry.icon_path}")
                    if not load_grid_override(entry.start_dir) and not should_review_match(entry, existing_appname):
                        continue

                exe_file = entry.exe_path
                if not exe_file and entry.source == SOURCE_FOLDER:
                    exe_file = find_largest_exe(entry.start_dir)
                if not matching_shortcut:
                    if exe_file:
                        logger.info(f"Using executable: {exe_file}")
                    else:
                        logger.error(f"No .exe files found for {game_name}. Skipping...")
                        continue

                pick = resolve_grid_match(entry, existing_appname, review_uses_gui)
                if pick and pick.apply_to_rest:
                    confirm_for_run = False
                    confirm_matches = False
                    logger.info("Auto-accepting suggested SteamGridDB matches for remaining games.")

                if pick and pick.skip:
                    if matching_shortcut:
                        logger.info(f"Keeping existing Steam shortcut for {game_name}")
                    else:
                        logger.info(f"Skipped {game_name}")
                    continue

                steam_grid_game_name = pick.game_name if pick else None
                game_id = pick.game_id if pick else None
                if not steam_grid_game_name:
                    if matching_shortcut:
                        steam_grid_game_name = existing_appname
                    else:
                        logger.error(f"No SteamGridDB match for {game_name}. Skipping...")
                        continue

                icon_value = quoted_shortcut_path(entry.icon_path) if entry.icon_path else ""

                if matching_shortcut:
                    updated = False
                    if steam_grid_game_name and matching_shortcut.get("appname") != steam_grid_game_name:
                        matching_shortcut["appname"] = steam_grid_game_name
                        updated = True
                        logger.info(f"Updated Steam name for {game_name} to {steam_grid_game_name}")
                    if entry.icon_path and shortcut_icon_path(matching_shortcut) != normalize_path(entry.icon_path):
                        matching_shortcut["icon"] = icon_value
                        updated = True
                        logger.info(f"Updated shortcut icon for {game_name}: {entry.icon_path}")
                    appid = normalize_appid(matching_shortcut["appid"])
                    if game_id and updated:
                        save_images(appid, game_id)
                    elif entry.icon_path and updated:
                        pass
                    continue

                exe_path = exe_file
                appid = normalize_appid(generate_appid(game_name, exe_path))
                if game_id:
                    save_images(appid, game_id)

                new_entry = {
                    "appid": appid,
                    "appname": steam_grid_game_name,
                    "exe": quoted_shortcut_path(exe_path),
                    "StartDir": quoted_shortcut_path(entry.start_dir),
                    "icon": icon_value,
                    "LaunchOptions": "",
                    "IsHidden": 0,
                    "AllowDesktopConfig": 1,
                    "OpenVR": 0,
                    "Devkit": 0,
                    "DevkitGameID": "",
                    "LastPlayTime": 0,
                    "tags": build_shortcut_tags(entry.source),
                }
                shortcuts['shortcuts'][str(len(shortcuts['shortcuts']))] = new_entry
                existing_by_start_dir[entry.normalized_start_dir()] = new_entry
                logger.info(f"Added shortcut for game: {game_name}")

            except Exception as e:
                logger.error("Error updating shortcuts:")
                logger.error(traceback.format_exc())

        # Save the updated shortcuts file
        with open(shortcuts_file, 'wb') as f:
            vdf.binary_dump(shortcuts, f)
            logger.info("Shortcuts file updated and saved.")
        return collect_xbox_appids(shortcuts)


    except Exception as e:
        logger.error(f"Error updating shortcuts: {e}")
        logger.error(traceback.format_exc())
        return []

def steamid64_id_to_userdata_id(steamid64: str | int) -> str:
    return str(int(steamid64) - STEAM_ID64_BASE)

def userdata_id_to_steamid64(userdata_id: str | int) -> str:
    return str(STEAM_ID64_BASE + int(userdata_id))

def get_steam_persona_name(userdata_id: str) -> str | None:

    login_users_path = Path(steamdir_path) / "config" / "loginusers.vdf"
    if not login_users_path.is_file():
        return None

    data = vdf.load(open(login_users_path, encoding="utf-8"))
    users = data.get("users", {})

    user = users.get(userdata_id, {})
    return user.get("PersonaName") or user.get("AccountName")


def format_steam_user_choice(userdata_id: str, persona_name: str | None) -> str:
    if persona_name:
        return f"{persona_name} ({steamid64_id_to_userdata_id(userdata_id)})"
    return userdata_id


def get_steam_users(selected_steamdir_path=None) -> dict[str, str]:
    global steamdir_path
    current_steamdir_path = selected_steamdir_path or steamdir_path
    userdata_path = os.path.join(current_steamdir_path, "userdata")

    if not current_steamdir_path or not os.path.isdir(userdata_path):
        return {}

    previous_steamdir_path = steamdir_path
    steamdir_path = current_steamdir_path

    try:
        return {
            steam_id: format_steam_user_choice(userdata_id_to_steamid64(steam_id), get_steam_persona_name(userdata_id_to_steamid64(steam_id)))
            for steam_id in os.listdir(userdata_path)
            if steam_id != "0" and steam_id.isdigit()
        }
    finally:
        steamdir_path = previous_steamdir_path


def resolve_steam_user_id(selected_steam_user_id, steam_users=None) -> str | None:
    if not selected_steam_user_id:
        return None

    steam_users = steam_users or get_steam_users()
    if selected_steam_user_id in steam_users:
        return selected_steam_user_id

    matching_users = [
        steam_id for steam_id, label in steam_users.items()
        if label == selected_steam_user_id
    ]
    if len(matching_users) == 1:
        return matching_users[0]

    return None


def GUI(allow_raw_steam_user_id=False):
    parser = GooeyParser(description='Add Non-Steam and Xbox Game Pass games to Steam.')
    selected_steamdir_path = steamdir_path or DEFAULT_STEAMDIR_PATH
    steam_users = get_steam_users(selected_steamdir_path)
    steam_user_id_choices = list(steam_users.values())
    accepted_steam_user_id_choices = steam_user_id_choices
    default_steam_user_id = ""
    resolved_steam_user_id = resolve_steam_user_id(steam_user_id, steam_users)
    if resolved_steam_user_id:
        default_steam_user_id = steam_users[resolved_steam_user_id]
    elif steam_user_id_choices:
        default_steam_user_id = steam_user_id_choices[0]
    else:
        steam_user_id_choices = [default_steam_user_id]
        accepted_steam_user_id_choices = steam_user_id_choices

    if allow_raw_steam_user_id:
        accepted_steam_user_id_choices = list(dict.fromkeys(steam_user_id_choices + list(steam_users.keys())))


    parser.add_argument(
        '--game_installation_path',
        widget='MultiDirChooser',
        metavar='NonSteam Games Folder',
        help='Optional when Xbox Game Pass scanning is enabled. Use semicolons to list multiple folders.',
        action='store',
        default=game_installation_path if game_installation_path else ''
    )

    parser.add_argument(
        '--steamgriddb_api_key',
        metavar='SteamgridDB API Key',
        help='You can get yours in the link below:\nhttps://www.steamgriddb.com/profile/preferences/api\nSubstitute the link with the key after you have generated it',
        default=steamgriddb_api_key if steamgriddb_api_key else "https://www.steamgriddb.com/profile/preferences/api",
        action='store'
    )

    parser.add_argument(
        '--steamdir_path',
        metavar='Steam Installation Path',
        widget='DirChooser',
        help="By default C:\\Program Files (x86)\\Steam",
        default=selected_steamdir_path,
        action='store'
    )

    parser.add_argument(
        '--steam_user_id',
        metavar='Steam User ID',
        widget='Dropdown',
        help="Steam userdata folder ID, found under the Steam userdata folder.",
        default=default_steam_user_id,
        required=len(steam_user_id_choices) > 1,
        choices=accepted_steam_user_id_choices,
        action='store'
    )

    xbox_group = parser.add_argument_group(
        'Xbox Game Pass',
        gooey_options={'show_border': True, 'columns': 1}
    )
    xbox_group.add_argument(
        '--scan_xbox',
        metavar='Scan Xbox Game Pass games',
        help='Discover games from XboxGames folders on every drive and launch them with gamelaunchhelper.exe.',
        widget='Dropdown',
        choices=['yes', 'no'],
        default='yes' if scan_xbox else 'no',
        action='store'
    )
    xbox_group.add_argument(
        '--xbox_games_path',
        metavar='Extra Xbox Games folders',
        widget='MultiDirChooser',
        help='Optional extra XboxGames folders besides auto-detected drive roots.',
        default=xbox_games_path if xbox_games_path else '',
        action='store'
    )
    xbox_group.add_argument(
        '--xbox_collection',
        metavar='Xbox Game Pass collection',
        help='Put every imported Xbox game into one Steam collection named Xbox Game Pass.',
        widget='Dropdown',
        choices=['yes', 'no'],
        default='yes' if xbox_collection else 'no',
        action='store'
    )

    parser.add_argument(
        '--confirm_matches',
        metavar='Confirm SteamGridDB matches',
        help='Ask before adding each game so you can pick the right SteamGridDB title. Saved choices are reused next time.',
        widget='Dropdown',
        choices=['yes', 'no'],
        default='yes' if confirm_matches else 'no',
        action='store'
    )
    parser.add_argument(
        '--restart_steam',
        metavar='Restart Steam automatically',
        help='Quit Steam before writing shortcuts, then start it again so the library reloads. Defaults to no.',
        widget='Dropdown',
        choices=['yes', 'no'],
        default='yes' if restart_steam else 'no',
        action='store'
    )

    return parser.parse_args()


def storeVariablesFromGUI(args):
    global game_installation_path, steamgriddb_api_key, steamdir_path, steam_user_id, scan_xbox, xbox_games_path, confirm_matches, restart_steam, xbox_collection, grid_overrides, xbox_collection_appids

    game_installation_path = args.game_installation_path or ""
    steamgriddb_api_key = args.steamgriddb_api_key
    steamdir_path = args.steamdir_path
    steam_user_id = resolve_steam_user_id(args.steam_user_id) or args.steam_user_id
    scan_xbox = coerce_bool(getattr(args, "scan_xbox", True), True)
    xbox_games_path = getattr(args, "xbox_games_path", "") or ""
    confirm_matches = coerce_bool(getattr(args, "confirm_matches", True), True)
    restart_steam = coerce_bool(getattr(args, "restart_steam", False), False)
    xbox_collection = coerce_bool(getattr(args, "xbox_collection", True), True)

    storedParametersJSON = readJsonFile(storedParametersJSONFilename) or {}
    storedParametersJSON["game_installation_path"] = game_installation_path
    storedParametersJSON["steamgriddb_api_key"] = steamgriddb_api_key
    storedParametersJSON["steamdir_path"] = steamdir_path
    storedParametersJSON["steam_user_id"] = steam_user_id
    storedParametersJSON["scan_xbox"] = scan_xbox
    storedParametersJSON["xbox_games_path"] = xbox_games_path
    storedParametersJSON["confirm_matches"] = confirm_matches
    storedParametersJSON["restart_steam"] = restart_steam
    storedParametersJSON["xbox_collection"] = xbox_collection
    storedParametersJSON["grid_overrides"] = storedParametersJSON.get("grid_overrides", grid_overrides) or {}
    storedParametersJSON["xbox_collection_appids"] = storedParametersJSON.get("xbox_collection_appids", xbox_collection_appids) or []
    grid_overrides = storedParametersJSON["grid_overrides"]
    xbox_collection_appids = storedParametersJSON["xbox_collection_appids"]

    saveJsonFile(storedParametersJSONFilename, storedParametersJSON)


def main(args):
    """Main function to check for new or removed games and update Steam shortcuts accordingly."""
    try:
        storeVariablesFromGUI(args)

        if not steamgriddb_api_key or not steamdir_path:
            logger.error("Some required parameters are missing.")
            return 1

        if not scan_xbox and not game_installation_path:
            logger.error("Provide a Non-Steam games folder or enable Xbox Game Pass scanning.")
            return 1

        if steamgriddb_api_key == "https://www.steamgriddb.com/profile/preferences/api":
            logger.error("SteamGridDB API key is missing.")
            return 1

        if not steam_user_id and not is_default_steamdir_path(steamdir_path):
            logger.error("Custom Steam path was saved. Please restart the application to load the Steam users dropdown.")
            return 1

        if restart_steam and is_steam_running():
            logger.info("Restart Steam is enabled. Shutting Steam down before writing shortcuts...")
            if not stop_steam():
                logger.error("Could not shut down Steam. Aborting so shortcuts.vdf is not overwritten.")
                return 1
        elif not restart_steam:
            warn_if_steam_running()

        logger.info("Reading current games from installation directory...")
        current_games = read_current_games()

        nl = '\n'
        game_summaries = [f"{entry.search_name} [{entry.source}] {entry.start_dir}" for entry in current_games]
        logger.info(f"Current games:{nl}{nl.join(game_summaries) if game_summaries else '(none)'}")

        steam_user_data_path = determineUserdataFolder(args.steam_user_id)

        logger.info("Updating shortcuts and fetching images...")
        xbox_appids = update_shortcuts(current_games, steam_user_data_path) or []

        if xbox_collection:
            try:
                managed = write_xbox_collection(steam_user_data_path, xbox_appids, xbox_collection_appids)
                save_xbox_collection_appids(managed)
                logger.info(f"Updated Steam collection '{XBOX_TAG}' with {len(xbox_appids)} game(s).")
            except Exception:
                logger.error("Failed to update the Xbox Game Pass collection.")
                logger.error(traceback.format_exc())

        if restart_steam:
            start_steam()
        return 0

    except Exception as e:
        logger.error(f"Unexpected error in main function: {e}")
        logger.error(traceback.format_exc())
        return 1


def rewrite_cli_xbox_flags(argv: list[str]) -> list[str]:
    rewritten = []
    for arg in argv:
        if arg == "--scan-xbox":
            rewritten.extend(["--scan_xbox", "yes"])
        elif arg == "--no-scan-xbox":
            rewritten.extend(["--scan_xbox", "no"])
        elif arg.startswith("--scan-xbox="):
            rewritten.extend(["--scan_xbox", arg.split("=", 1)[1]])
        elif arg == "--confirm-matches":
            rewritten.extend(["--confirm_matches", "yes"])
        elif arg == "--no-confirm-matches":
            rewritten.extend(["--confirm_matches", "no"])
        elif arg.startswith("--confirm-matches="):
            rewritten.extend(["--confirm_matches", arg.split("=", 1)[1]])
        elif arg == "--restart-steam":
            rewritten.extend(["--restart_steam", "yes"])
        elif arg == "--no-restart-steam":
            rewritten.extend(["--restart_steam", "no"])
        elif arg.startswith("--restart-steam="):
            rewritten.extend(["--restart_steam", arg.split("=", 1)[1]])
        elif arg == "--xbox-collection":
            rewritten.extend(["--xbox_collection", "yes"])
        elif arg == "--no-xbox-collection":
            rewritten.extend(["--xbox_collection", "no"])
        elif arg.startswith("--xbox-collection="):
            rewritten.extend(["--xbox_collection", arg.split("=", 1)[1]])
        else:
            rewritten.append(arg)
    return rewritten


def run():
    global review_uses_gui
    sys.argv = rewrite_cli_xbox_flags(sys.argv)

    if '--cli' in sys.argv:
        review_uses_gui = False
        if not steamgriddb_api_key or not steamdir_path:
            logger.error("Missing required parameters. Please run with GUI first to set them up.")
            return 1
        if not scan_xbox and not game_installation_path:
            logger.error("Provide a Non-Steam games folder or enable Xbox Game Pass scanning.")
            return 1

        # strip it so argparse doesn't complain
        sys.argv.remove('--cli')
        args = GUI(allow_raw_steam_user_id=True)

    elif '--ignore-gooey' in sys.argv:
        # Gooey uses this when launching the script from the GUI.
        review_uses_gui = True
        sys.argv.remove('--ignore-gooey')
        args = GUI(allow_raw_steam_user_id=True)

    else:
        patch_gooey_output_decoding()
        args = Gooey(
            show_preview_warning=False,
            encoding="utf-8",
            program_name="SyncNon-SteamGamesPlus",
            progress_regex=r"Games processed: (?P<current>\d+)/(?P<total>\d+)$",
            progress_expr="current / total * 100"
        )(GUI)()

    return main(args)


if __name__ == "__main__":
    sys.exit(run())
