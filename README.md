# Tellus Explorer

A small Minecraft 26.3 exploration and creative-building modpack, assembled from
exact published files. Version **0.1.0-alpha.2**.

**Tellus Expeditions is deliberately excluded.** Its source repository remains
private. This pack contains none of its binaries, source, gameplay changes,
world presets or prototypes.

Source repository: [TimStewartJ/Tellus-Modpack](https://github.com/TimStewartJ/Tellus-Modpack).
GitHub Actions validates changes and publishes prereleases only from matching
version tags. No Modrinth listing is configured. Package integrity is not a
claim that the combined game stack has passed a fresh-world play test.

## Contents

| Component | Fabric | NeoForge |
| --- | --- | --- |
| Minecraft / Java | 26.3 / 25 | 26.3 / 25 |
| Loader | 0.19.5 | 26.3.0.76 |
| Tellus | 0.8.4-fork.15 | 0.8.4-fork.15 |
| Distant Horizons, Tellus fork | 3.3.4-tellus-fork.7 | 3.3.4-tellus-fork.7 |
| The Mighty Architectury | 2.1.0 | 2.1.0 |
| Sodium | 0.9.2 | 0.9.2 |
| Iris | 1.11.6 | 1.11.7 |
| Fabric API | 0.160.7+26.3 | Not needed |
| Bliss | 2.1.2 | 2.1.2 |
| Complementary Reimagined | r5.9.3 | r5.9.3 |

Iris uses the matching published build for each loader; both require the exact
Sodium 0.9.2 builds pinned here. Mighty Architectury no longer needs
Architectury API. No additional optimization mods are included without
demonstrated need and compatibility evidence.

## Install and start

1. In Prism Launcher, choose **Add Instance / Import**, and select the Fabric or
   NeoForge `.mrpack` from `dist`. Import into a **new** instance.
2. Let the launcher download the pinned files. Select **Java 25** in the
   launcher's Java settings. Memory is a launcher setting, not something the
   portable `.mrpack` can enforce. A 6 GiB heap is a starting suggestion on a
   machine with sufficient free RAM, not a measured minimum.
3. Create a **fresh test world**, select the **Earth** world type, then
   Customize. Unlike Expeditions, this pack does not automatically select Earth
   or change Tellus' world defaults.
4. Tellus' shipped default is 1:30 near Everest. Move the spawn to a suitable
   low-altitude location before creating the world if desired. Real-height 1:1
   worlds are available through Customize but are more demanding.
5. Use Creative mode for flying/building. Press **G** for Mighty Architectury.
   Printing needs appropriate Creative/operator permissions; adding the mod
   does not grant permission to build on somebody else's server.

Tellus downloads geographic inputs as you explore and requires internet access,
including after pack installation. Expect additional disk and network usage.
There are no bundled Earth tiles, generated worlds or prebuilt DH caches.

## Shaders and performance

Both shader ZIPs are downloaded from their authors' official Modrinth files
without modifications. **Shaders start disabled.** Choose Bliss or Complementary
Reimagined in **Options / Video Settings / Shader Packs**. They are alternatives,
not two shaders to run simultaneously.

- **Bliss** is the scenic option already used in the existing Tellus play stack.
- **Complementary Reimagined** offers a different, vanilla-inspired appearance.
  Its exact combination with these forks is not yet game-tested.

The conservative starting settings are 6-chunk vanilla render and simulation
distances, a 512-chunk DH radius, medium DH horizontal/vertical quality, and four
DH workers with a 0.75 runtime ratio. Distant generation is surface-only, using
Tellus' fast LOD integration rather than generating full distant chunks.
DH's upstream auto-updater is disabled so it cannot silently replace the fork.

These settings are **not benchmark results** and do not fix known fast-travel
terrain-streaming stalls. Increase quality only after checking the shader-free
baseline. If distant terrain disappears or a shader-pass error occurs, disable
shaders first. Rendering, frame times and building-tool/shader interactions need
an authorized in-game check on each loader.

Only a small, manually authored subset of settings ships. No personal keybinds,
account files, server addresses, hardware fingerprints, saves, logs, test agents
or local caches were copied from the play instance.

## Source layout

```text
pack.json                 Human-reviewed exact source selections
pack.lock.json            Generated URLs, sizes, hashes and dependency metadata
client-overrides\         Small client configuration layer
overrides\                Shared getting-started text
tools\pack.py             Standard-library resolver, verifier and pack builder
tests\test_pack.py        Offline regression tests
.github\workflows\pack.yml   Validation and tag-triggered GitHub prereleases
dist\                     Generated .mrpack files and SHA256SUMS.txt (ignored)
.cache\                   Downloaded verification inputs (ignored)
```

The generated `.mrpack` archives contain the index and override files, not
third-party mod jars or shader ZIPs. The launcher downloads those files itself:
Tellus and DH from exact public GitHub release asset URLs, and the rest from
the official Modrinth CDN. Hashes pin the actual bytes. A copy of this guide,
the credits and the packaging license is included in each installation.

The client/server environment flags keep client-only graphics files off servers
and mark Mighty Architectury as optional on servers. **This release is a client
pack; it does not provide a validated dedicated-server distribution.**

## Rebuild

Python **3.11 or newer**, with no pip packages:

```powershell
Set-Location -LiteralPath 'E:\Tellus-Modpack'
python -m unittest discover -s tests -v
python tools\pack.py verify
python tools\pack.py build
python tools\pack.py audit
```

`verify` downloads missing cached inputs if needed, checks SHA-1, SHA-256 and
SHA-512 against the lock, and checks each ZIP/JAR for CRC errors. It does not
install or execute downloaded code. `build` is offline: it uses the checked-in
lock, never "latest", and produces deterministic archives and SHA-256 sums.
`audit` checks the generated manifests, override bytes and checksums against
the source and lock.

To intentionally update a component, edit its exact source selection in
`pack.json`, increment the pack version, then:

```powershell
python tools\pack.py resolve
python -m unittest discover -s tests -v
python tools\pack.py verify
python tools\pack.py build
python tools\pack.py audit
```

`resolve` uses public APIs without credentials. It verifies GitHub release
SHA-256 digests and Modrinth SHA-1/SHA-512 hashes before writing the new lock,
checks declared Minecraft/loader compatibility for Modrinth files, and checks
their required Modrinth dependencies for each loader. Review the source and lock
diff and obtain permission before running game tests or publishing.

Do not use a launcher's update-all function to replace individual forks.
Pre-alpha supports only fresh ephemeral worlds and the current selected stack;
no save migration or old-protocol compatibility is provided.

## Automated releases

Pushes to `main`, pull requests and manual workflow runs execute the offline
tests, verify every locked upstream download, build both packs, audit the
archives and retain the exports as GitHub Actions artifacts. They do not
publish a release or run Minecraft.

After the intended version and lock are committed and publication is approved:

```powershell
git tag v0.1.0-alpha.2
git push origin v0.1.0-alpha.2
```

That tag must exactly match `pack.json`. The same workflow then publishes the
already-verified `.mrpack` files and `SHA256SUMS.txt` as a GitHub **prerelease**.
Publishing uses GitHub's short-lived workflow token, with write permission only
on the publishing job; no personal token is stored in the repository. The
workflow does not resolve newer dependency versions or create tags for you.

Modrinth publishing is not configured. It can be added after creating the pack
project and storing an appropriately scoped token as a GitHub Actions secret.
Neither publishing route automatically replaces an existing Prism instance.

## Credits and license

See [CREDITS.md](CREDITS.md) for authors, official project pages and license
references. Packaging code and original configurations are MIT-licensed;
referenced mods, shaders, Minecraft and geographic data keep their own licenses.
The pack maintainer is responsible for integration issues in this combination.
