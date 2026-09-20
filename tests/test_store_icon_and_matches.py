import tempfile
import unittest
from pathlib import Path

from steamgrid_match import (
    GridCandidate,
    extract_candidates,
    pick_best_candidate,
    prompt_grid_match_cli,
    rank_candidates,
    score_title_match,
    titles_look_similar,
)
from xbox_games import discover_xbox_games, find_store_icon


def write_bytes(path: Path, size: int, data: bytes = b"P") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data * size)


class StoreIconTests(unittest.TestCase):
    def test_prefers_storeicon_over_storelogo(self):
        with tempfile.TemporaryDirectory() as tmp:
            content = Path(tmp)
            write_bytes(content / "StoreLogo.png", 200)
            write_bytes(content / "StoreIcon.png", 80)
            self.assertEqual(Path(find_store_icon(str(content))), content / "StoreIcon.png")

    def test_prefers_ico_and_larger_scale_variant(self):
        with tempfile.TemporaryDirectory() as tmp:
            content = Path(tmp)
            write_bytes(content / "StoreIcon.scale-100.png", 50)
            write_bytes(content / "StoreIcon.ico", 10)
            self.assertEqual(Path(find_store_icon(str(content))).name, "StoreIcon.ico")

    def test_uses_microsoft_game_config_storelogo(self):
        with tempfile.TemporaryDirectory() as tmp:
            content = Path(tmp)
            assets = content / "Assets"
            write_bytes(assets / "Logo.png", 40)
            (content / "MicrosoftGame.config").write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<Game>
  <ShellVisuals DefaultDisplayName="Halo Infinite" StoreLogo="Assets\\Logo.png" />
</Game>
""",
                encoding="utf-8",
            )
            self.assertEqual(Path(find_store_icon(str(content))), assets / "Logo.png")

    def test_discover_sets_icon_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            content = Path(tmp) / "XboxGames" / "Absolum" / "Content"
            write_bytes(content / "gamelaunchhelper.exe", 16)
            write_bytes(content / "StoreIcon.png", 32)
            games = discover_xbox_games(extra_roots=str(Path(tmp) / "XboxGames"), drive_roots=[])
            self.assertEqual(len(games), 1)
            self.assertEqual(Path(games[0].icon_path), content / "StoreIcon.png")


class SteamGridMatchTests(unittest.TestCase):
    def test_extract_and_rank_avoids_modfoundry_for_minecraft(self):
        payload = {
            "success": True,
            "data": [
                {"id": 1, "name": "ModFoundry - Mod Maker for Minecraft"},
                {"id": 2, "name": "Minecraft: Bedrock Edition"},
                {"id": 3, "name": "Minecraft"},
            ],
        }
        candidates = extract_candidates(payload)
        best = pick_best_candidate("Minecraft for Windows", candidates)
        self.assertIsNotNone(best)
        self.assertIn("Minecraft", best.game_name)
        self.assertNotIn("ModFoundry", best.game_name)
        self.assertGreater(
            score_title_match("Minecraft for Windows", "Minecraft: Bedrock Edition"),
            score_title_match("Minecraft for Windows", "ModFoundry - Mod Maker for Minecraft"),
        )
        self.assertFalse(titles_look_similar("Minecraft for Windows", "ModFoundry - Mod Maker for Minecraft"))

    def test_cli_prompt_selects_numbered_match(self):
        candidates = rank_candidates(
            "Minecraft for Windows",
            [
                GridCandidate("1", "ModFoundry - Mod Maker for Minecraft"),
                GridCandidate("2", "Minecraft: Bedrock Edition"),
                GridCandidate("3", "Minecraft"),
            ],
        )
        answers = iter(["1"])
        pick = prompt_grid_match_cli(
            "Minecraft for Windows",
            "Minecraft for Windows",
            "ModFoundry - Mod Maker for Minecraft",
            candidates,
            search_fn=lambda _query: candidates,
            input_fn=lambda _prompt: next(answers),
            print_fn=lambda *_args, **_kwargs: None,
        )
        self.assertFalse(pick.skip)
        self.assertEqual(pick.game_name, "Minecraft: Bedrock Edition")

    def test_cli_prompt_custom_search(self):
        first = [GridCandidate("1", "ModFoundry - Mod Maker for Minecraft")]
        second = [GridCandidate("9", "Minecraft: Bedrock Edition")]

        def search_fn(query: str):
            if "bedrock" in query.lower():
                return second
            return first

        answers = iter(["Minecraft Bedrock", "1"])
        pick = prompt_grid_match_cli(
            "Minecraft for Windows",
            "Minecraft for Windows",
            None,
            first,
            search_fn=search_fn,
            input_fn=lambda _prompt: next(answers),
            print_fn=lambda *_args, **_kwargs: None,
        )
        self.assertEqual(pick.game_id, "9")
        self.assertEqual(pick.game_name, "Minecraft: Bedrock Edition")


if __name__ == "__main__":
    unittest.main()
