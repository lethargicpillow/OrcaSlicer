# Bambu X1E LAN FTPS Storage integration

This experimental branch is based on the working Linux BambuNetwork RPC
bridge. The printer's Storage tab gains a local implicit-FTPS transport;
the existing cloud and Bambu_Tunnel transports remain unchanged.

Proven on the user's X1E: port 990, user `bblp`, LAN Access Code
authentication, `/` contains .3mf models, and `/timelapse/` plus
`/ipcam/` can be listed separately via FTPS.

## Source and attribution

Transport implementation adapted from tofublock/OrcaSlicer,
`feat-x1c-lan-ftps` at commit 41cc2bb193be,
three scoped commits:
813fdc1a8aa8 (Bambu_Tunnel abstraction),
26a997f6845f (FTPS implementation),
41cc2bb193be (input validation, bounded queues and backoff).
Original implementation tested on X1C, not X1E.

X1E is identified by the firmware model code `C13`.
The X1E bypasses the old `ipcam.file.local` capability check and
prefers FTPS when LAN Only is active.

No proprietary libraries are copied into this source branch.
FTPS credentials are sourced from the existing access code, never
requested or stored separately. The FTPS URL is redacted from
PrinterFileSystem connection logs. The upstream implementation disables
libcurl certificate validation because the printer certificate is
self-signed; use on a trusted LAN only.

## Validation required before merge

1. Build: `cmake --build build --config Release --target OrcaSlicer --parallel 2`.
2. Run with the existing Bambu plugin and `PJARCZAK_LINUX_BRIDGE_ENABLED=1`.
3. Check Device / Storage > External: Models and Timelapse lists load
   on the X1E without toggling LAN Only or connecting Bambu Cloud.
4. Verify thumbnail, model download, video download and refresh.
5. Verify deletion only with a disposable test file.
6. Verify closing the Storage tab during a stalled download, reconnecting
   the LAN, restarting the printer, and closing OrcaSlicer.
7. Recheck Bambu LAN control, AMS and video; inspect logs for leaked
   `bblp:accesscode@` and indefinite GUI hangs.

Important: `/ipcam/` was confirmed accessible with curl, but this
transport maps the existing Timelapse and Model tabs; the separate
Video button is hidden by current Orca. Supporting `/ipcam/` in the
same UI requires a separate follow-up change and runtime validation.
