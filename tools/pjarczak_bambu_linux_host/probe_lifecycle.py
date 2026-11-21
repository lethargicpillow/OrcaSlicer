#!/usr/bin/env python3
"""Exercise the standalone host RPC and isolate proprietary plug-in teardown crashes.

Each invocation uses a fresh host subprocess. Scenarios:
  ping        - framing and handshake, no network agent
  bare        - create -> destroy
  configured  - create -> config/log/country -> destroy (no network start)
  started     - create -> config/log/country -> start/login -> destroy
  certified   - create -> set_cert_file -> destroy
  started-certified - create -> config/log/country -> set_cert_file -> start/login -> destroy
  discovery   - initialize agent -> subscribe to SSDP -> listen -> stop -> destroy

The host must initialize certificates during create_agent. The 'bare'
scenario verifies immediate safe destruction even without an explicit
set_cert_file RPC. The 'certified' scenarios additionally exercise the
explicit certificate setter used by OrcaSlicer.
No debugger or printer is required.
"""
import argparse
import json
import os
import select
import struct
import subprocess
import sys
import time
from pathlib import Path

MAGIC = 0x52424A50
HEADER = struct.Struct("<IIII")
MAX_REPLY = 8 * 1024 * 1024


def read_exact(fd, length, seconds):
    buf = bytearray()
    deadline = time.monotonic() + seconds
    while len(buf) < length:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("RPC reply timed out")
        readable, _, _ = select.select([fd], [], [], remaining)
        if not readable:
            raise TimeoutError("RPC reply timed out")
        data = os.read(fd, length - len(buf))
        if not data:
            raise EOFError("host closed RPC stream")
        buf.extend(data)
    return bytes(buf)


def call(proc, request_id, method, payload, timeout):
    print("CALL " + method, flush=True)
    request = json.dumps({"method": method, "payload": payload}).encode("utf-8")
    proc.stdin.write(HEADER.pack(MAGIC, 1, request_id, len(request)) + request)
    proc.stdin.flush()
    magic, kind, reply_id, length = HEADER.unpack(
        read_exact(proc.stdout.fileno(), HEADER.size, timeout))
    if (magic, kind, reply_id) != (MAGIC, 2, request_id):
        raise ValueError("invalid RPC header: " + repr((magic, kind, reply_id)))
    if length > MAX_REPLY:
        raise ValueError("RPC payload exceeds the test size limit")
    reply = json.loads(read_exact(proc.stdout.fileno(), length, timeout))
    if not isinstance(reply, dict):
        raise ValueError("RPC response is not a JSON object")
    public = {k: reply[k] for k in (
        "ok", "value", "error", "network_loaded", "source_loaded",
        "network_status", "source_status", "network_actual_abi_version",
    ) if k in reply}
    print("RESULT " + repr(public), flush=True)
    return reply


def require_zero(reply, method):
    if reply.get("ok") is not True or reply.get("value") != 0:
        raise RuntimeError(method + " failed: " + repr(reply))


def main():
    root = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scenario", choices=("ping", "bare", "configured", "started",
                                            "certified", "started-certified", "discovery"),
                    default="ping")
    ap.add_argument("--discover-seconds", type=float, default=12.0,
                    help="how long to poll LAN discovery events (default: 12 s)")
    ap.add_argument("--require-events", action="store_true",
                    help="fail if no SSDP event is received; requires a reachable printer")
    ap.add_argument("--abi", choices=("abi1", "abi0"), default="abi1")
    ap.add_argument("--plugin-dir", type=Path, default=root / "bambu-plugin")
    ap.add_argument("--cert-dir", type=Path, default=root / "resources/cert")
    ap.add_argument("--binary", type=Path)
    ap.add_argument("--timeout", type=float, default=15.0)
    args = ap.parse_args()
    if args.discover_seconds < 0.5 or args.discover_seconds > 120:
        ap.error("--discover-seconds must be between 0.5 and 120")
    if args.require_events and args.scenario != "discovery":
        ap.error("--require-events only applies to --scenario discovery")

    binary = args.binary or (root / "build/bambulab-host/tools/pjarczak_bambu_linux_host"
                             / ("pjarczak_bambu_linux_host_" + args.abi))
    binary = binary.expanduser().resolve()
    if not binary.is_file():
        ap.error("host binary not found: " + str(binary))

    cert_dir = args.cert_dir.expanduser().resolve()
    if args.scenario != "ping" and not (cert_dir / "slicer_base64.cer").is_file():
        ap.error("required certificate missing: " + str(cert_dir / "slicer_base64.cer"))

    probe_dir = root / "probe-data"
    probe_dir.mkdir(parents=True, exist_ok=True)
    log_path = probe_dir / ("lifecycle_" + args.scenario + "_" + args.abi + ".stderr.log")

    env = os.environ.copy()
    if args.scenario != "ping":
        plugin_dir = args.plugin_dir.expanduser().resolve()
        for name in ("libbambu_networking.so", "libBambuSource.so"):
            if not (plugin_dir / name).is_file():
                ap.error("required plugin missing: " + str(plugin_dir / name))
        env.update({
            "PJARCZAK_BAMBU_PLUGIN_DIR": str(plugin_dir),
            "PJARCZAK_BAMBU_NETWORK_SO": str(plugin_dir / "libbambu_networking.so"),
            "PJARCZAK_BAMBU_SOURCE_SO": str(plugin_dir / "libBambuSource.so"),
            "PJARCZAK_BAMBU_CERT_DIR": str(cert_dir),
            "PJARCZAK_EXPECTED_BAMBU_NETWORK_VERSION": "02.08.04",
            "LD_LIBRARY_PATH": str(plugin_dir) + (
                ":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else ""),
        })
    else:
        for key in ("PJARCZAK_BAMBU_PLUGIN_DIR", "PJARCZAK_BAMBU_NETWORK_SO",
                    "PJARCZAK_BAMBU_SOURCE_SO", "PJARCZAK_BAMBU_CERT_DIR"):
            env.pop(key, None)

    status = 0
    with log_path.open("wb") as stderr:
        proc = subprocess.Popen([str(binary)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=stderr, env=env)
        seq = 0

        def rpc(method, payload=None):
            nonlocal seq
            seq += 1
            return call(proc, seq, method, payload or {}, args.timeout)

        try:
            require_ping = rpc("bridge.ping")
            if require_ping.get("ok") is not True or require_ping.get("value") != "pong":
                raise RuntimeError("bridge.ping did not return pong")
            handshake = rpc("bridge.handshake")
            if handshake.get("ok") is not True or handshake.get("protocol_version") != 1:
                raise RuntimeError("unsupported bridge protocol")
            if args.scenario != "ping":
                if handshake.get("network_loaded") is not True:
                    raise RuntimeError("network module unavailable: " + repr(handshake))
                actual = str(handshake.get("network_actual_abi_version", ""))
                if not actual.startswith("02.08.04"):
                    raise RuntimeError("unexpected plug-in version: " + actual)
                result = rpc("net.create_agent", {
                    "log_dir": str(probe_dir),
                    "country_code": "PL",
                })
                if result.get("ok") is not True:
                    raise RuntimeError("net.create_agent failed")
                agent = result.get("value")
                if not isinstance(agent, int) or agent <= 0:
                    raise RuntimeError("invalid agent handle")
                params = {"agent": agent}
                if args.scenario in ("configured", "started", "started-certified", "discovery"):
                    for method, fields in (
                        ("net.set_config_dir", {"config_dir": str(probe_dir)}),
                        ("net.init_log", {}),
                        ("net.set_country_code", {"country_code": "PL"}),
                    ):
                        require_zero(rpc(method, {**params, **fields}), method)
                if args.scenario in ("certified", "started-certified", "discovery"):
                    require_zero(rpc("net.set_cert_file", {
                        **params,
                        "folder": str(cert_dir),
                        "filename": "slicer_base64.cer",
                    }), "net.set_cert_file")
                if args.scenario in ("started", "started-certified", "discovery"):
                    require_zero(rpc("net.start", params), "net.start")
                    login = rpc("net.is_user_login", params)
                    if login.get("ok") is not True or not isinstance(login.get("value"), bool):
                        raise RuntimeError("net.is_user_login failed")
                if args.scenario == "discovery":
                    require_zero(rpc("net.set_on_ssdp_msg_fn", params),
                                 "net.set_on_ssdp_msg_fn")
                    started = rpc("net.start_discovery", {
                        **params, "start": True, "sending": False,
                    })
                    if started.get("ok") is not True or started.get("value") is not True:
                        raise RuntimeError("net.start_discovery returned failure: " + repr(started))
                    count = 0
                    names = {}
                    deadline = time.monotonic() + args.discover_seconds
                    try:
                        while time.monotonic() < deadline:
                            reply = rpc("bridge.poll_events", {"limit": 128})
                            if reply.get("ok") is not True or not isinstance(reply.get("events"), list):
                                raise RuntimeError("bridge.poll_events failed: " + repr(reply))
                            for event in reply["events"]:
                                if not isinstance(event, dict):
                                    continue
                                name = event.get("name", "unknown")
                                names[name] = names.get(name, 0) + 1
                                if event.get("agent") == agent and name == "on_ssdp_msg":
                                    count += 1
                            time.sleep(min(0.5, max(0, deadline - time.monotonic())))
                    finally:
                        stopped = rpc("net.start_discovery", {
                            **params, "start": False, "sending": False,
                        })
                        if stopped.get("ok") is not True:
                            raise RuntimeError("stopping SSDP failed: " + repr(stopped))
                    print("LAN discovery: {} SSDP events in {:.1f}s".format(
                        count, args.discover_seconds), flush=True)
                    print("EVENT TYPES: " + repr(names), flush=True)
                    if count == 0:
                        print("No SSDP device event: network reception not verified; "
                              "the RPC lifecycle can still pass.", flush=True)
                    if args.require_events and count == 0:
                        raise RuntimeError("no SSDP device events observed")
                require_zero(rpc("net.destroy_agent", params), "net.destroy_agent")
        except (OSError, ValueError, EOFError, TimeoutError, RuntimeError) as exc:
            status = 1
            print("FAILED: " + repr(exc) + ", process=" + str(proc.poll()), flush=True)
        finally:
            if proc.stdin and not proc.stdin.closed:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            try:
                code = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                code = proc.wait()
                status = 1
                print("Host had to be killed after shutdown timeout", flush=True)
            if code != 0:
                status = 1
            print("HOST EXIT CODE: " + str(code), flush=True)
            print("STDERR LOG: " + str(log_path), flush=True)
    if status == 0:
        print("PASS: " + args.scenario + " / " + args.abi, flush=True)
    return status


if __name__ == "__main__":
    sys.exit(main())
