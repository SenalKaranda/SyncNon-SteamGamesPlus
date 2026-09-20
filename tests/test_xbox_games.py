import inspect
import tempfile
import unittest
from pathlib import Path

from game_entry import GameEntry, SOURCE_FOLDER, SOURCE_XBOX, merge_game_entries
from xbox_games import (
    discover_xbox_games,
    find_gamelaunchhelper,
    find_xbox_roots,
    parse_gaming_root,
    read_display_name,
)


def write_gaming_root(path: Path, folder_name: str = "XboxGames") -> None:
    payload = b"RGBX" + (1).to_bytes(4, "little") + folder_name.encode("utf-16le") + b"\x00\x00"
    path.write_bytes(payload)


def write_exe(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"M" * size)


class XboxGamesTests(unittest.TestCase):
    def test_parse_gaming_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            gaming_root = Path(tmp) / ".GamingRoot"
            write_gaming_root(gaming_root)
            self.assertEqual(parse_gaming_root(str(gaming_root)), "XboxGames")
            self.assertIsNone(parse_gaming_root(str(Path(tmp) / "missing.GamingRoot")))

    def test_forza_layout_uses_helper_not_largest_exe(self):
        with tempfile.TemporaryDirectory() as tmp:
            drive = Path(tmp)
            write_gaming_root(drive / ".GamingRoot")
            content = drive / "XboxGames" / "Forza Horizon 6" / "Content"
            helper = content / "gamelaunchhelper.exe"
            game_exe = content / "ForzaHorizon6.exe"
            write_exe(helper, 64)
            write_exe(game_exe, 1024 * 1024)

            games = discover_xbox_games(drive_roots=[str(drive)])
            self.assertEqual(len(games), 1)
            self.assertEqual(games[0].source, SOURCE_XBOX)
            self.assertEqual(Path(games[0].exe_path), helper)
            self.assertEqual(Path(games[0].start_dir), content)
            self.assertNotEqual(Path(games[0].exe_path), game_exe)
            self.assertEqual(games[0].search_name, "Forza Horizon 6")

    def test_skips_folders_without_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            drive = Path(tmp)
            incomplete = drive / "XboxGames" / "Broken Game" / "Content"
            write_exe(incomplete / "BrokenGame.exe", 4096)
            games = discover_xbox_games(drive_roots=[str(drive)])
            self.assertEqual(games, [])

    def test_empty_xboxgames_folder_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            drive = Path(tmp)
            write_gaming_root(drive / ".GamingRoot")
            (drive / "XboxGames").mkdir()
            games = discover_xbox_games(drive_roots=[str(drive)])
            self.assertEqual(games, [])

    def test_display_name_from_microsoft_game_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            content = Path(tmp) / "XboxGames" / "fh6" / "Content"
            write_exe(content / "gamelaunchhelper.exe", 32)
            (content / "MicrosoftGame.config").write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<Game configVersion="1">
  <ShellVisuals DefaultDisplayName="Forza Horizon 6" />
</Game>
""",
                encoding="utf-8",
            )
            games = discover_xbox_games(extra_roots=str(Path(tmp) / "XboxGames"), drive_roots=[])
            self.assertEqual(len(games), 1)
            self.assertEqual(games[0].search_name, "Forza Horizon 6")
            self.assertEqual(read_display_name(str(content), "fh6"), "Forza Horizon 6")

    def test_display_name_with_xml_namespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            start_dir = Path(tmp)
            (start_dir / "MicrosoftGame.config").write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<Game xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10">
  <ShellVisuals DefaultDisplayName="Sea of Thieves" />
</Game>
""",
                encoding="utf-8",
            )
            self.assertEqual(read_display_name(str(start_dir), "sot"), "Sea of Thieves")

    def test_recursive_helper_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / "Custom Root" / "Some Game" / "Binaries"
            helper = nested / "gamelaunchhelper.exe"
            write_exe(helper, 16)
            found = find_gamelaunchhelper(str(Path(tmp) / "Custom Root" / "Some Game"))
            self.assertEqual(Path(found), helper)

    def test_xbox_games_folder_with_space(self):
        with tempfile.TemporaryDirectory() as tmp:
            drive = Path(tmp)
            content = drive / "Xbox Games" / "Halo Infinite" / "Content"
            write_exe(content / "gamelaunchhelper.exe", 24)
            roots = find_xbox_roots(drive_roots=[str(drive)])
            self.assertTrue(any(Path(root).name.lower() == "xbox games" for root in roots))
            games = discover_xbox_games(drive_roots=[str(drive)])
            self.assertEqual(len(games), 1)
            self.assertEqual(games[0].name, "Halo Infinite")

    def test_dedup_prefers_xbox_when_folder_scan_overlaps(self):
        with tempfile.TemporaryDirectory() as tmp:
            game_dir = Path(tmp) / "XboxGames" / "Forza Horizon 6"
            content = game_dir / "Content"
            helper = content / "gamelaunchhelper.exe"
            write_exe(helper, 32)
            write_exe(content / "ForzaHorizon6.exe", 5000)

            xbox_entries = discover_xbox_games(extra_roots=str(Path(tmp) / "XboxGames"), drive_roots=[])
            folder_entries = [
                GameEntry(
                    name="Forza Horizon 6",
                    search_name="Forza Horizon 6",
                    exe_path=str(content / "ForzaHorizon6.exe"),
                    start_dir=str(game_dir),
                    source=SOURCE_FOLDER,
                )
            ]
            merged = merge_game_entries(xbox_entries, folder_entries)
            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0].source, SOURCE_XBOX)
            self.assertEqual(Path(merged[0].exe_path).name.lower(), "gamelaunchhelper.exe")

    def test_xbox_module_does_not_use_largest_exe_heuristic(self):
        import xbox_games
        source = inspect.getsource(xbox_games)
        self.assertNotIn("find_largest_exe", source)

    def test_extra_roots_semicolon_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_a = Path(tmp) / "CustomA"
            root_b = Path(tmp) / "CustomB"
            write_exe(root_a / "Game A" / "Content" / "gamelaunchhelper.exe", 16)
            write_exe(root_b / "Game B" / "Content" / "gamelaunchhelper.exe", 16)
            extra = f"{root_a};{root_b}"
            games = discover_xbox_games(extra_roots=extra, drive_roots=[])
            names = sorted(game.name for game in games)
            self.assertEqual(names, ["Game A", "Game B"])


if __name__ == "__main__":
    unittest.main()
