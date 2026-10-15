"""Build pinned .mrpack archives with Python's standard library; never launch a game."""

import argparse
import functools
import hashlib
import json
import re
import shutil
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
HASHES = ("sha1", "sha256", "sha512")
DOWNLOAD_HOSTS = {"github.com", "cdn.modrinth.com"}
OVERRIDE_DIRS = ("overrides", "client-overrides", "server-overrides")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode("utf-8")


def safe_relative(value):
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or ":" in value
        or any(part in ("", ".", "..") for part in value.split("/"))
    ):
        raise ValueError(f"Unsafe archive path: {value!r}")
    return path


def check_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in DOWNLOAD_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or any(character.isspace() for character in url)
    ):
        raise ValueError(f"Not an approved public download URL: {url}")


def open_url(url):
    request = urllib.request.Request(
        url, headers={"User-Agent": "Tellus-Explorer-Modpack/0.1 (Python standard library)"}
    )
    response = urllib.request.urlopen(request, timeout=90)
    if urllib.parse.urlsplit(response.url).scheme != "https":
        response.close()
        raise ValueError("Refusing an insecure download redirect")
    return response


@functools.lru_cache(maxsize=None)
def remote_json(url):
    with open_url(url) as response:
        return json.load(response)


def file_hashes(path):
    digests = {name: hashlib.new(name) for name in HASHES}
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            for digest in digests.values():
                digest.update(block)
    return {name: digest.hexdigest() for name, digest in digests.items()}


def checked_file(path, expected_size, expected_hashes):
    if path.stat().st_size != expected_size:
        raise ValueError(f"File size mismatch: {path}")
    actual = file_hashes(path)
    for algorithm, expected in expected_hashes.items():
        if algorithm in actual and actual[algorithm] != expected.lower():
            raise ValueError(f"{algorithm} mismatch: {path}")
    return actual


def cached_download(entry, cache):
    relative = safe_relative(entry["path"])
    destination = cache / entry["id"] / relative.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        temporary = destination.with_name(destination.name + ".download")
        try:
            check_url(entry["downloads"][0])
            with open_url(entry["downloads"][0]) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
            checked_file(temporary, entry["fileSize"], entry["hashes"])
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    hashes = checked_file(destination, entry["fileSize"], entry["hashes"])
    return destination, hashes


def validate_spec(spec):
    identifiers = set()
    for entry in spec["files"]:
        if not re.fullmatch(r"[a-z0-9-]+", entry["id"]) or entry["id"] in identifiers:
            raise ValueError(f"Invalid or duplicate file id: {entry['id']}")
        identifiers.add(entry["id"])
        if not entry["loaders"] or not set(entry["loaders"]) <= set(spec["loaders"]):
            raise ValueError(f"Unknown loader for {entry['id']}")
        if entry["directory"] not in ("mods", "shaderpacks"):
            raise ValueError(f"Unsupported destination for {entry['id']}")
        if set(entry["env"]) != {"client", "server"} or not set(entry["env"].values()) <= {
            "required", "optional", "unsupported"
        }:
            raise ValueError(f"Invalid environment for {entry['id']}")


def resolve_entry(spec, entry):
    source = entry["source"]
    result = {
        key: entry[key] for key in ("id", "loaders", "env", "source")
    }
    if source["type"] == "github":
        tag = urllib.parse.quote(source["tag"], safe="")
        release = remote_json(
            f"https://api.github.com/repos/{source['repository']}/releases/tags/{tag}"
        )
        if release["draft"] or release["tag_name"] != source["tag"]:
            raise ValueError(f"Not a published pinned release: {entry['id']}")
        asset = next(
            item for item in release["assets"] if item["name"] == source["asset"]
        )
        digest = asset.get("digest", "")
        if not digest.startswith("sha256:"):
            raise ValueError(f"GitHub SHA-256 digest missing for {entry['id']}")
        filename = asset["name"]
        result.update(
            downloads=[asset["browser_download_url"]],
            fileSize=asset["size"],
            hashes={"sha256": digest.removeprefix("sha256:")},
            metadata={"release": release["html_url"]},
        )
    elif source["type"] == "modrinth":
        version = remote_json(f"https://api.modrinth.com/v2/version/{source['version']}")
        if spec["minecraft"] not in version["game_versions"]:
            raise ValueError(f"Wrong Minecraft version for {entry['id']}")
        required_loaders = {"iris"} if entry["directory"] == "shaderpacks" else set(entry["loaders"])
        if not required_loaders <= set(version["loaders"]):
            raise ValueError(f"Wrong loader for {entry['id']}")
        primary = next(item for item in version["files"] if item["primary"])
        filename = primary["filename"]
        result.update(
            downloads=[primary["url"]],
            fileSize=primary["size"],
            hashes={name: primary["hashes"][name] for name in ("sha1", "sha512")},
            metadata={
                "projectId": version["project_id"],
                "versionId": version["id"],
                "version": version["version_number"],
                "dependencies": version["dependencies"],
            },
        )
    else:
        raise ValueError(f"Unknown source type for {entry['id']}")
    if safe_relative(filename).name != filename:
        raise ValueError("A source filename must be a basename")
    result["path"] = f"{entry['directory']}/{filename}"
    for url in result["downloads"]:
        check_url(url)
    return result


def validate_dependencies(spec, entries):
    for loader in spec["loaders"]:
        selected = [entry for entry in entries if loader in entry["loaders"]]
        paths = set()
        for entry in selected:
            path = str(safe_relative(entry["path"])).casefold()
            if path in paths:
                raise ValueError(f"Duplicate path for {loader}: {path}")
            paths.add(path)
            for dependency in entry["metadata"].get("dependencies", []):
                if dependency["dependency_type"] != "required":
                    continue
                matches = [
                    candidate for candidate in selected
                    if candidate["metadata"].get("projectId") == dependency["project_id"]
                    and (
                        dependency["version_id"] is None
                        or candidate["metadata"].get("versionId") == dependency["version_id"]
                    )
                ]
                if not matches:
                    raise ValueError(f"Missing dependency for {loader}: {entry['id']} {dependency}")


def spec_hash(root):
    return hashlib.sha256((root / "pack.json").read_bytes()).hexdigest()


def resolve(root, spec):
    validate_spec(spec)
    entries = []
    for entry in spec["files"]:
        resolved = resolve_entry(spec, entry)
        _, hashes = cached_download(resolved, root / ".cache")
        resolved["hashes"] = hashes
        entries.append(resolved)
        print(f"Verified {resolved['id']}: {resolved['path']}", flush=True)
    validate_dependencies(spec, entries)
    lock = {"lockVersion": 1, "specSha256": spec_hash(root), "files": entries}
    (root / "pack.lock.json").write_bytes(json_bytes(lock))
    return lock


def validate_lock(root, spec, lock):
    validate_spec(spec)
    if lock["lockVersion"] != 1 or lock["specSha256"] != spec_hash(root):
        raise ValueError("Pack specification changed; run resolve and review the new lock")
    if [entry["id"] for entry in lock["files"]] != [entry["id"] for entry in spec["files"]]:
        raise ValueError("Locked files do not match the specification")
    for entry, requested in zip(lock["files"], spec["files"]):
        for field in ("loaders", "env", "source"):
            if entry[field] != requested[field]:
                raise ValueError(f"Lock does not match {entry['id']}: {field}")
        for name in HASHES:
            if not re.fullmatch(rf"[0-9a-f]{{{hashlib.new(name).digest_size * 2}}}", entry["hashes"][name]):
                raise ValueError(f"Invalid {name} for {entry['id']}")
        if entry["fileSize"] <= 0 or not entry["downloads"]:
            raise ValueError(f"Invalid download for {entry['id']}")
        if safe_relative(entry["path"]).parent != PurePosixPath(requested["directory"]):
            raise ValueError(f"Unexpected destination for {entry['id']}")
        for url in entry["downloads"]:
            check_url(url)
    validate_dependencies(spec, lock["files"])


def override_files(root):
    for directory in OVERRIDE_DIRS:
        folder = root / directory
        if folder.is_symlink():
            raise ValueError(f"Override folders must not be symlinks: {folder}")
        if not folder.exists():
            continue
        for path in sorted(folder.rglob("*")):
            if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
                raise ValueError(f"Override escapes its source folder: {path}")
            if path.is_file():
                name = path.relative_to(root).as_posix()
                safe_relative(name)
                yield name, path.read_bytes()


def archive_bytes(archive, name, data):
    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data)


def pack_overrides(root):
    overrides = list(override_files(root))
    for source, target in (
        ("README.md", "README-TELLUS.md"),
        ("CREDITS.md", "CREDITS.md"),
        ("LICENSE", "LICENSE-TELLUS-PACK.txt"),
    ):
        document = root / source
        if document.exists():
            overrides.append((f"overrides/{target}", document.read_bytes()))
    return overrides


def pack_filename(spec, loader):
    return f"Tellus-Explorer-{loader}-{spec['minecraft']}-{spec['version']}.mrpack"


def pack_index(spec, lock, loader):
    dependency = spec["loaders"][loader]
    return {
        "formatVersion": 1,
        "game": "minecraft",
        "versionId": f"{spec['version']}-{loader}",
        "name": f"{spec['name']} ({loader.capitalize() if loader == 'fabric' else 'NeoForge'})",
        "summary": spec["summary"],
        "files": [
            {key: entry[key] for key in ("path", "hashes", "env", "downloads", "fileSize")}
            for entry in lock["files"] if loader in entry["loaders"]
        ],
        "dependencies": {"minecraft": spec["minecraft"], dependency["id"]: dependency["version"]},
    }


def build(root, spec, lock, output):
    validate_lock(root, spec, lock)
    output.mkdir(parents=True, exist_ok=True)
    overrides = pack_overrides(root)
    built = []
    for loader in spec["loaders"]:
        index = pack_index(spec, lock, loader)
        path = output / pack_filename(spec, loader)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            archive_bytes(archive, "modrinth.index.json", json_bytes(index))
            for name, data in overrides:
                archive_bytes(archive, name, data)
        built.append(path)
        print(f"Built {path.name}: {len(index['files'])} pinned downloads")
    checksums = "".join(f"{file_hashes(path)['sha256']}  {path.name}\n" for path in built)
    (output / "SHA256SUMS.txt").write_text(checksums, encoding="ascii", newline="\n")
    return built


def audit(root, spec, lock, output):
    validate_lock(root, spec, lock)
    overrides = dict(pack_overrides(root))
    checksums = []
    for loader in spec["loaders"]:
        path = output / pack_filename(spec, loader)
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            expected = {"modrinth.index.json", *overrides}
            if len(names) != len(set(names)) or set(names) != expected:
                raise ValueError(f"Unexpected archive contents: {path.name}")
            if archive.testzip() is not None:
                raise ValueError(f"Corrupt archive: {path.name}")
            if json.loads(archive.read("modrinth.index.json")) != pack_index(spec, lock, loader):
                raise ValueError(f"Manifest differs from the lock: {path.name}")
            for name, content in overrides.items():
                if archive.read(name) != content:
                    raise ValueError(f"Override differs from source: {path.name} {name}")
        checksums.append(f"{file_hashes(path)['sha256']}  {path.name}\n")
        print(f"Audited {path.name}", flush=True)
    if (output / "SHA256SUMS.txt").read_text(encoding="ascii") != "".join(checksums):
        raise ValueError("SHA256SUMS.txt differs from the generated archives")


def check_tag(spec, tag):
    if tag != f"v{spec['version']}":
        raise ValueError(f"Release tag must be v{spec['version']}, not {tag!r}")


def verify(root, spec, lock):
    validate_lock(root, spec, lock)
    for entry in lock["files"]:
        path, _ = cached_download(entry, root / ".cache")
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            if bad:
                raise ValueError(f"Corrupt archive member in {path}: {bad}")
        print(f"Verified {entry['id']}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("resolve", "build", "verify", "audit", "check-tag"))
    parser.add_argument("tag", nargs="?")
    args = parser.parse_args()
    spec = read_json(ROOT / "pack.json")
    if args.command == "check-tag":
        check_tag(spec, args.tag)
        print(f"Release tag matches {spec['version']}")
    elif args.command == "resolve":
        resolve(ROOT, spec)
    else:
        lock = read_json(ROOT / "pack.lock.json")
        if args.command == "build":
            build(ROOT, spec, lock, ROOT / "dist")
        elif args.command == "audit":
            audit(ROOT, spec, lock, ROOT / "dist")
        else:
            verify(ROOT, spec, lock)


if __name__ == "__main__":
    main()
