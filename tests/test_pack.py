import copy
import hashlib
import io
import json
import sys
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import pack


class PackTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.spec = {
            "name": "Fixture",
            "version": "0.1.0-alpha.1",
            "summary": "Test fixture, not a real pack.",
            "minecraft": "26.3",
            "loaders": {
                "fabric": {"id": "fabric-loader", "version": "0.19.5"},
                "neoforge": {"id": "neoforge", "version": "26.3.0.76"},
            },
            "files": [],
        }
        self.entries = []
        for loader in ("fabric", "neoforge"):
            requested = {
                "id": f"fixture-{loader}",
                "loaders": [loader],
                "directory": "mods",
                "env": {"client": "required", "server": "unsupported"},
                "source": {"type": "modrinth", "version": f"fixture-{loader}"},
            }
            self.spec["files"].append(requested)
            self.entries.append({
                **{key: requested[key] for key in ("id", "loaders", "env", "source")},
                "path": f"mods/fixture-{loader}.jar",
                "fileSize": 10,
                "downloads": [f"https://cdn.modrinth.com/fixture-{loader}.jar"],
                "hashes": {name: "0" * (hashlib.new(name).digest_size * 2) for name in pack.HASHES},
                "metadata": {
                    "projectId": "fixture-project",
                    "versionId": f"fixture-{loader}",
                    "dependencies": [],
                },
            })
        (self.root / "pack.json").write_bytes(pack.json_bytes(self.spec))
        self.lock = {
            "lockVersion": 1,
            "specSha256": pack.spec_hash(self.root),
            "files": self.entries,
        }

    def test_rejects_unsafe_archive_paths(self):
        for value in ("../escape", "mods/../escape", "/absolute", "C:/absolute", "mods\\bad", "mods//bad", "./bad"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                pack.safe_relative(value)
        self.assertEqual(str(pack.safe_relative("mods/safe.jar")), "mods/safe.jar")

    def test_download_urls_are_public_https_and_allowlisted(self):
        for url in (
            "http://github.com/file.jar",
            "https://example.com/file.jar",
            "https://user:password@github.com/file.jar",
            "https://github.com/file with spaces.jar",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                pack.check_url(url)
        pack.check_url("https://github.com/owner/repo/releases/download/v1/mod.jar")

    def test_spec_change_requires_resolving_again(self):
        (self.root / "pack.json").write_bytes(pack.json_bytes({**self.spec, "version": "0.2.0"}))
        with self.assertRaisesRegex(ValueError, "specification changed"):
            pack.validate_lock(self.root, self.spec, self.lock)

    def test_lock_environment_cannot_diverge_from_spec(self):
        changed = copy.deepcopy(self.lock)
        changed["files"][0]["env"]["server"] = "required"
        with self.assertRaisesRegex(ValueError, "Lock does not match"):
            pack.validate_lock(self.root, self.spec, changed)

    def test_missing_hash_is_not_a_valid_lock(self):
        changed = copy.deepcopy(self.lock)
        changed["files"][0]["hashes"]["sha512"] = "invalid"
        with self.assertRaisesRegex(ValueError, "Invalid sha512"):
            pack.validate_lock(self.root, self.spec, changed)

    def test_case_insensitive_duplicate_destinations_are_rejected(self):
        duplicate = copy.deepcopy(self.entries[0])
        duplicate["path"] = duplicate["path"].upper()
        with self.assertRaisesRegex(ValueError, "Duplicate path"):
            pack.validate_dependencies(self.spec, self.entries + [duplicate])

    def test_required_dependencies_must_match_on_each_loader(self):
        self.entries[0]["metadata"]["dependencies"] = [{
            "dependency_type": "required",
            "project_id": "missing",
            "version_id": None,
        }]
        with self.assertRaisesRegex(ValueError, "Missing dependency for fabric"):
            pack.validate_dependencies(self.spec, self.entries)

    def test_dependency_version_cannot_be_substituted(self):
        self.entries[0]["metadata"]["dependencies"] = [{
            "dependency_type": "required",
            "project_id": "fixture-project",
            "version_id": "wrong-version",
        }]
        with self.assertRaisesRegex(ValueError, "Missing dependency"):
            pack.validate_dependencies(self.spec, self.entries)

    def test_builds_have_only_their_own_loader_files(self):
        paths = pack.build(self.root, self.spec, self.lock, self.root / "dist")
        for loader, path in zip(self.spec["loaders"], paths):
            with zipfile.ZipFile(path) as archive:
                index = json.loads(archive.read("modrinth.index.json"))
            self.assertEqual([entry["path"] for entry in index["files"]], [f"mods/fixture-{loader}.jar"])
            self.assertEqual(index["dependencies"]["minecraft"], "26.3")
            self.assertIn(self.spec["loaders"][loader]["id"], index["dependencies"])

    def test_archive_is_reproducible_and_preserves_override_layers(self):
        for directory in ("overrides", "client-overrides"):
            target = self.root / directory / "config"
            target.mkdir(parents=True)
            (target / "fixture.txt").write_text(directory, encoding="ascii")
        (self.root / "CREDITS.md").write_text("Fixture credits", encoding="ascii")
        first = pack.build(self.root, self.spec, self.lock, self.root / "one")
        second = pack.build(self.root, self.spec, self.lock, self.root / "two")
        self.assertEqual([path.read_bytes() for path in first], [path.read_bytes() for path in second])
        with zipfile.ZipFile(first[0]) as archive:
            self.assertEqual(archive.read("client-overrides/config/fixture.txt"), b"client-overrides")
            self.assertEqual(archive.read("overrides/CREDITS.md"), b"Fixture credits")
            self.assertIsNone(archive.testzip())
        checksums = (self.root / "one" / "SHA256SUMS.txt").read_text(encoding="ascii")
        self.assertIn(pack.file_hashes(first[0])["sha256"], checksums)

    def test_download_is_checked_before_it_enters_cache(self):
        content = b"download fixture"
        entry = {
            "id": "download-fixture",
            "path": "mods/fixture.jar",
            "downloads": ["https://cdn.modrinth.com/fixture.jar"],
            "fileSize": len(content),
            "hashes": {"sha512": hashlib.sha512(content).hexdigest()},
        }
        with patch.object(pack, "open_url", return_value=io.BytesIO(content)):
            path, hashes = pack.cached_download(entry, self.root / "cache")
        self.assertEqual(path.read_bytes(), content)
        self.assertEqual(hashes["sha256"], hashlib.sha256(content).hexdigest())
        with patch.object(pack, "open_url") as network:
            pack.cached_download(entry, self.root / "cache")
            network.assert_not_called()
        path.write_bytes(b"broken")
        with self.assertRaisesRegex(ValueError, "mismatch"):
            pack.cached_download(entry, self.root / "cache")

    def test_corrupt_download_does_not_leave_an_installable_file(self):
        entry = {
            "id": "bad-fixture",
            "path": "mods/fixture.jar",
            "downloads": ["https://cdn.modrinth.com/fixture.jar"],
            "fileSize": 3,
            "hashes": {"sha512": "0" * 128},
        }
        with patch.object(pack, "open_url", return_value=io.BytesIO(b"bad")):
            with self.assertRaisesRegex(ValueError, "sha512 mismatch"):
                pack.cached_download(entry, self.root / "cache")
        self.assertEqual(list((self.root / "cache").rglob("*.*")), [])

    def test_modrinth_source_rejects_wrong_minecraft_version(self):
        metadata = {"game_versions": ["1.21.1"], "loaders": ["fabric"]}
        with patch.object(pack, "remote_json", return_value=metadata):
            with self.assertRaisesRegex(ValueError, "Wrong Minecraft version"):
                pack.resolve_entry(self.spec, self.spec["files"][0])

    def test_modrinth_source_rejects_wrong_loader(self):
        metadata = {"game_versions": ["26.3"], "loaders": ["neoforge"]}
        with patch.object(pack, "remote_json", return_value=metadata):
            with self.assertRaisesRegex(ValueError, "Wrong loader"):
                pack.resolve_entry(self.spec, self.spec["files"][0])

    def test_audit_accepts_exact_build(self):
        output = self.root / "dist"
        pack.build(self.root, self.spec, self.lock, output)
        pack.audit(self.root, self.spec, self.lock, output)

    def test_audit_rejects_extra_bundled_file(self):
        output = self.root / "dist"
        paths = pack.build(self.root, self.spec, self.lock, output)
        with zipfile.ZipFile(paths[0], "a") as archive:
            archive.writestr("overrides/unrequested.txt", "not part of the pack")
        with self.assertRaisesRegex(ValueError, "Unexpected archive contents"):
            pack.audit(self.root, self.spec, self.lock, output)

    def test_audit_rejects_changed_override(self):
        shared = self.root / "overrides"
        shared.mkdir()
        (shared / "fixture.txt").write_text("before", encoding="ascii")
        output = self.root / "dist"
        pack.build(self.root, self.spec, self.lock, output)
        (shared / "fixture.txt").write_text("after", encoding="ascii")
        with self.assertRaisesRegex(ValueError, "Override differs from source"):
            pack.audit(self.root, self.spec, self.lock, output)

    def test_audit_rejects_wrong_checksums(self):
        output = self.root / "dist"
        pack.build(self.root, self.spec, self.lock, output)
        (output / "SHA256SUMS.txt").write_text("wrong\n", encoding="ascii")
        with self.assertRaisesRegex(ValueError, "SHA256SUMS.txt differs"):
            pack.audit(self.root, self.spec, self.lock, output)

    def test_release_tag_must_match_pack_version(self):
        pack.check_tag(self.spec, "v0.1.0-alpha.1")
        for tag in (None, "v0.1.0", "latest", "v0.1.0-alpha.2"):
            with self.subTest(tag=tag), self.assertRaisesRegex(ValueError, "Release tag must be"):
                pack.check_tag(self.spec, tag)


class RealPackDefaultsTests(unittest.TestCase):
    def test_requested_scope_is_present_without_expeditions(self):
        spec = pack.read_json(ROOT / "pack.json")
        pack.validate_spec(spec)
        identifiers = {entry["id"] for entry in spec["files"]}
        self.assertIn("mighty-architectury-fabric", identifiers)
        self.assertIn("mighty-architectury-neoforge", identifiers)
        self.assertIn("iris-fabric", identifiers)
        self.assertIn("iris-neoforge", identifiers)
        self.assertIn("bliss", identifiers)
        self.assertIn("complementary-reimagined", identifiers)
        self.assertFalse(any("expeditions" in identifier for identifier in identifiers))

    def test_shaders_are_downloaded_on_clients_only_and_disabled_at_start(self):
        spec = pack.read_json(ROOT / "pack.json")
        shaders = [entry for entry in spec["files"] if entry["directory"] == "shaderpacks"]
        self.assertEqual(len(shaders), 2)
        for entry in shaders:
            self.assertEqual(entry["env"], {"client": "required", "server": "unsupported"})
        properties = (ROOT / "client-overrides" / "config" / "iris.properties").read_text(encoding="ascii")
        self.assertIn("enableShaders=false", properties.splitlines())

    def test_dh_config_is_valid_and_prevents_upstream_auto_replacement(self):
        with (ROOT / "client-overrides" / "config" / "DistantHorizons.toml").open("rb") as stream:
            config = tomllib.load(stream)
        self.assertEqual(config["_version"], 5)
        self.assertEqual(config["common"]["worldGenerator"]["generatorPlan"], "SURFACE_ONLY")
        self.assertFalse(config["client"]["advanced"]["autoUpdater"]["enableAutoUpdater"])
        self.assertFalse(config["client"]["advanced"]["autoUpdater"]["enableSilentUpdates"])


if __name__ == "__main__":
    unittest.main()
