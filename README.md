# SyncNon-SteamGamesPlus
A fork of [IGnGr/SyncNon-SteamGames](https://github.com/IGnGr/SyncNon-SteamGames), itself a modified version of GameSync from [Maikeru86](https://github.com/Maikeru86/GameSync).

Automatically add Non-Steam games **and Xbox Game Pass games** to Steam with images from SteamGridDB.

![GUI](images/GUI.png)

## Features
- Reads games from a specified installation directory.
- Auto-detects Xbox Game Pass installs from `XboxGames` / `Xbox Games` folders on every drive.
- Launches Game Pass titles with `gamelaunchhelper.exe` (not the largest game executable).
- Uses `StoreIcon` from each game's `Content` folder as the Steam shortcut icon (`StoreLogo` if StoreIcon is missing).
- Optional per-game SteamGridDB confirmation, so a search like "Minecraft for Windows" can be mapped to **Minecraft: Bedrock Edition** instead of a similarly named workshop tool.
- Optional Steam restart after a sync (off on first launch).
- Puts imported Xbox games into one Steam collection named **Xbox Game Pass**.
- Generates unique AppIDs for non-Steam games.
- Fetches grid, hero, and logo images from SteamGridDB.
- Adds new games to Steam shortcuts.
- Removes shortcuts for games that are no longer installed, but only if they were originally created by SyncNon-SteamGamesPlus.
- Finds largest `.exe` in ordinary non-Steam game folders and adds that as the game executable.
- Logging
- Changed the shown name in Steam of the games to be the name from steamgrid instead of the executable name
- Added GUI
- Automatically uses the only available Steam userdata folder, or lets you select the Steam user ID when multiple accounts are detected.
- Storage of the variables in `%APPDATA%\SyncNonSteamGamesPlus\parameters.json`
- Changed slightly the messages to be logged, now it informs if a titles is being skipped when it's already present in steam.
- Added exceptions to the names of executables to be found, to avoid using the wrong one in Unity games
- Multi folder support
- CLI support with the argument `--cli` thanks to [`@pwilinchery`](https://github.com/IGnGr/SyncNon-SteamGames/pull/2)

## Xbox Game Pass
PC Game Pass games installed through the Xbox app usually look like this:

```
C:\XboxGames\Forza Horizon 6\Content\gamelaunchhelper.exe
```

The Xbox app also places a hidden `.GamingRoot` marker at the root of each drive that has games. Plus scans those markers, then `XboxGames` and `Xbox Games` on every drive.

Steam shortcuts for these titles:
- **Target:** `gamelaunchhelper.exe`
- **Start In:** the `Content` folder next to that helper
- **Icon:** `StoreIcon` from that `Content` folder
- **Tags:** `SyncNon-SteamPlus` and `Xbox Game Pass`
- **Collection:** all of those titles are added to a Steam library collection named `Xbox Game Pass` (disable **Xbox Game Pass collection** if you do not want this)

Enable **Restart Steam automatically** if you want Plus to quit Steam before writing shortcuts, then start it again. That option is off the first time you run the app. Otherwise restart Steam yourself after a sync so it reloads `shortcuts.vdf`. If Steam is running during the sync and auto-restart is off, Plus logs a warning because Steam can overwrite that file on exit.

### v1.0 limitation
Locked Microsoft Store / UWP installs under `C:\Program Files\WindowsApps` are not imported. Those games do not expose a usable `gamelaunchhelper.exe` in an `XboxGames` folder.

## Limitations:
- For ordinary non-Steam folders, the executable is located by size, which is not ideal. At this moment, the user must change the executable in Steam directly if the wrong one has been chosen.


## Requirements
- NonSteam Games Folder: Path to the directory where your Non-Steam games are installed. Optional if Xbox Game Pass scanning is enabled.
- SteamGridDB API Key: You have to generate one for yourself here: https://www.steamgriddb.com/profile/preferences/api
- Steam Installation path: Path where Steam is installed in your system.
### Using source

- Install the required libraries
```py
pip install -r requirements.txt
```

If installation of Gooey fails on Linux, please run following command to install required dependencies (for Debian 13 Trixie):
```bash
sudo apt install libgtk-3-dev python-config
```

- Execute the script `sync_non_steam_games.py` or `SyncNon-SteamGamesPlus.py`

## Usage
- Download the packaged version from "Releases"
- Make sure the SteamGridDB API field is filled in. Add a Non-Steam games folder and/or leave Xbox Game Pass scanning enabled.
- If multiple Steam accounts are found in the Steam `userdata` folder, select the Steam user ID to update.
- Run it
- Restart Steam to see the new shortcuts

For CLI usage, pass `--steam_user_id` when multiple Steam userdata folders exist:
```bash
python sync_non_steam_games.py --cli --steam_user_id 12345678
python sync_non_steam_games.py --cli --no-scan-xbox
python sync_non_steam_games.py --cli --scan-xbox --xbox_games_path "D:\CustomXboxGames"
python sync_non_steam_games.py --cli --confirm-matches
python sync_non_steam_games.py --cli --no-confirm-matches
python sync_non_steam_games.py --cli --restart-steam
python sync_non_steam_games.py --cli --no-xbox-collection
```

Set **Confirm SteamGridDB matches** to yes to pick the right title for each new or mismatched game. Choices are saved in `parameters.json` and reused on later runs. For a game that was already imported under the wrong name, leave confirmation enabled, choose the correct SteamGridDB result, and restart Steam.

If you previously used the original SyncNon-SteamGames app, Plus copies your Steam path, API key, and user ID into `%APPDATA%\SyncNonSteamGamesPlus\parameters.json` on first run.

## Tips
If your library is huge and you are having difficulties locating the games, here is how you can find them easily  until Valve provides a filter in desktop mode (Only big picture has a filter for non-steam games):
- Enable the option to show only installed games
- Create 4 dynamics library: 
    - Single Player
    - Multi Player
    - Co op
    - Steam Trading Cards

    This should narrow the games in your "Uncategorized" section to be basically the ones we're looking for
- Create a new library on "Non-Steam" steam and add them
- Congratulations! your games are categorized and easily accessible

## Release notes
The GitHub release workflow is triggered by pushing a git tag in `vX.Y.Z` format, for example `v1.0.0`.

Example:
```bash
git tag v1.0.0
git push origin v1.0.0
```

Make sure `CHANGELOG.md` contains a matching heading like `## [v1.0.0]` before pushing the tag.


## 3rd Party libraries
- "Gooey" for the GUI (https://github.com/chriskiehl/Gooey)
- "Pyinstaller" for building the executable (https://github.com/pyinstaller/pyinstaller)
- "vdf" to read Valve's config files https://pypi.org/project/vdf/
- "requests" to handle the network side of thing  https://pypi.org/project/requests/
