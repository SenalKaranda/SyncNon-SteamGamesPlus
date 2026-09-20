import tempfile
import unittest
from pathlib import Path

from steam_collections import (
    COLLECTION_ID,
    COLLECTION_NAME,
    collection_appid,
    merge_managed_appids,
    resolve_cloud_storage_path,
    upsert_xbox_collection,
    write_xbox_collection,
)


class SteamCollectionTests(unittest.TestCase):
    def test_merge_replaces_removed_games_and_keeps_manual_ones(self):
        existing = [10, 20, 30]
        previous = [20, 30]
        current = [30, 40]
        self.assertEqual(merge_managed_appids(existing, previous, current), [10, 30, 40])

    def test_appid_is_unsigned_32_bit(self):
        self.assertEqual(collection_appid("-794967296"), 3500000000 & 0xffffffff)
        self.assertEqual(collection_appid(3500000000), 3500000000)

    def test_upsert_creates_named_collection(self):
        data, managed = upsert_xbox_collection([], [111, 222], [], timestamp=1700000000)
        self.assertEqual(managed, [111, 222])
        self.assertEqual(len(data), 1)
        key, payload = data[0]
        self.assertEqual(key, f"user-collections.{COLLECTION_ID}")
        self.assertEqual(payload["timestamp"], 1700000000)
        self.assertIn(COLLECTION_NAME, payload["value"])

    def test_upsert_reuses_existing_collection_by_name(self):
        existing = [[
            "user-collections.uc-manual",
            {
                "key": "user-collections.uc-manual",
                "timestamp": 1,
                "value": '{"id":"uc-manual","name":"Xbox Game Pass","added":[9,11],"removed":[]}',
                "version": "10",
            },
        ]]
        data, managed = upsert_xbox_collection(existing, [11, 12], [11], timestamp=50)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0][0], "user-collections.uc-manual")
        self.assertEqual(managed, [9, 11, 12])
        self.assertGreater(int(data[0][1]["version"]), 10)

    def test_write_and_namespace_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            cloud = config_dir / "cloudstorage"
            cloud.mkdir(parents=True)
            (cloud / "cloud-storage-namespaces.json").write_text("[[1,\"1\"],[3,\"9\"]]", encoding="utf-8")
            path = resolve_cloud_storage_path(str(config_dir))
            self.assertEqual(path.name, "cloud-storage-namespace-3.json")
            managed = write_xbox_collection(str(config_dir), [5], [])
            self.assertEqual(managed, [5])
            self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
