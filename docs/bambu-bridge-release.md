# OrcaSlicer Bambu Bridge: release and update policy

## Current validated platform

Linux x86_64, Bambu Lab X1E, LAN Only and Developer Mode, proprietary Linux
BambuNetwork 02.08.04.60. Windows WSL2 and macOS Lima install paths exist but
have NOT been validated as turnkey installers.

## AppImage release workflow

Run Actions > **Bambu Bridge Linux AppImage** on
`feature/bambulab-network`. With an empty `release_tag`, the output is an
Actions AppImage artifact. With a semver tag, CI creates/updates a **draft**
GitHub Release, not a published release. Test the draft's AppImage on a real
X1E before publishing.

An AppImage build must include the library
`libpjarczak_bambu_networking_bridge.so`, the
`pjarczak_bambu_linux_host` dispatcher, and ABI0/ABI1 Host binaries.
Its launcher enables `PJARCZAK_LINUX_BRIDGE_ENABLED=1` unless explicitly
overridden with `=0`.

The proprietary `libbambu_networking.so` and `libBambuSource.so` are NOT
redistributed. The user must obtain a compatible Linux plugin via a lawful
plugin installation flow, and install it in their OrcaSlicer data directory
under `plugins/`. A fresh installation without that plugin is not a usable
Bambu bridge installation; do not describe it as turnkey.

## Updating the application

Bridge-enabled builds check this fork's GitHub Releases API, not the upstream
official update server. The built-in update dialog links to a release; it does
not silently install a new AppImage. Use SemVer release tags and update
`SoftFever_VERSION` in `version.inc` for each published version so existing
semver comparison can detect newer builds. A user-configured
`version_check_url` takes precedence.

## Upstream

The weekly workflow on the default branch watches official stable OrcaSlicer
releases, creates an issue for each previously unseen tag, and deliberately
does not merge upstream automatically. Review compatibility, rebuild, test
LAN, AMS, camera, slice/send/print, plugin lifecycle, and only then publish
a new fork release.

## CI limitations

The automated build validates packaging and normal Orca tests but cannot
prove network connectivity to a physical X1E. Windows and macOS stock
installers must not be published as if they contained a working Linux
bridge runtime.

## Local AppImage smoke test and existing development data

The previously working development GUI used `--datadir` with
`~/OrcaSlicer-bambulab-test/.orca-bridge-test`. A normal AppImage invocation
uses `~/.config/OrcaSlicer` (unless XDG_CONFIG_HOME overrides it). It cannot
see the development printer presets and user-installed BambuNetwork payload.
For a non-migrating comparison run:

```bash
ZINK_DISABLE_OVERRIDE=1 ./OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.6.0-dev.AppImage \
  --datadir "$HOME/OrcaSlicer-bambulab-test/.orca-bridge-test"
```

The AppImage includes the open-source bridge and ABI Host executables but
**not** proprietary `libbambu_networking.so` or `libBambuSource.so`.
Those must be present under `<datadir>/plugins/`. Do not silently move or
overwrite the development data directory.

On NVIDIA/Wayland, automatically forcing Mesa/Zink is unsafe for some host
WebKitGTK/graphics combinations. The AppImage launcher now only enables
Zink with `ZINK_FORCE_OVERRIDE=1` (and not with `ZINK_DISABLE_OVERRIDE=1`).
For an existing AppImage use `ZINK_DISABLE_OVERRIDE=1` to avoid its automatic
override; `WEBKIT_DISABLE_COMPOSITING_MODE=1` may help isolate WebKitGTK
crashes but is not a blanket solution. Prefer `journalctl` and
`coredumpctl info` to opening crashing graphical core-dump viewers.
