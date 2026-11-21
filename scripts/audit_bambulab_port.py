#!/usr/bin/env python3
"""Static safety gates for Orca 2.6 BambuNetwork RPC port.

This is source-level validation, not a runtime / compiler / printer test.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
read = lambda name: (ROOT / name).read_text(encoding="utf-8")
upstream = read("src/slic3r/Utils/bambu_networking.hpp")
host_abi = read("shared/pjarczak_linux_plugin_bridge_core/CurrentBambuNetworking.hpp")
loader = read("src/slic3r/Utils/BBLNetworkPlugin.cpp")
forwarder = read("src/slic3r/Utils/PJarczakLinuxBridge/PJarczakBambuNetworkForwarderExports.cpp")
host = read("tools/pjarczak_bambu_linux_host/LinuxPluginHost.cpp")
cmake = read("src/slic3r/CMakeLists.txt")


def canon(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"//[^\n]*", "", source)
    return re.sub(r"\s+", "", source)


def struct_body(source: str, name: str) -> str:
    m = re.search(r"\bstruct\s+" + re.escape(name) + r"\s*\{", source)
    if not m:
        raise ValueError(f"missing struct {name}")
    depth = 1
    i = m.end()
    j = i
    while j < len(source) and depth:
        depth += (source[j] == "{") - (source[j] == "}")
        j += 1
    if depth:
        raise ValueError(f"unterminated struct {name}")
    return canon(source[i : j - 1])


project_tasks = read("src/libslic3r/ProjectTask.hpp")
task_compat = read("src/slic3r/Utils/PJarczakLinuxBridge/PJarczakLinuxBridgeCompat.hpp")
bridge_cmake = read("src/slic3r/Utils/PJarczakLinuxBridge/CMakeLists.txt")

errors = []

# A native bridge must use Orca's real task object; only the independent
# Linux host gets a lightweight mirror. Missing tail fields cause buffer
# overwrites when a modern Bambu plug-in populates the task in place.
for field in ("design_title", "title", "instance_title", "create_client"):
    if not re.search(r"\bstd::string\s+" + re.escape(field) + r"\s*;", project_tasks):
        errors.append(f"unknown current BBLModelTask field: {field}")
    if not re.search(r"\bstd::string\s+" + re.escape(field) + r"\s*;", task_compat):
        errors.append(f"missing host BBLModelTask field: {field}")
if 'PJARCZAK_LINUX_BRIDGE_LIGHTWEIGHT_TASKS=1 BAMBU_EXPORTS' in bridge_cmake:
    errors.append("Windows bridge must use real Orca BBLModelTask")
if 'target_compile_definitions(pjarczak_bambu_networking_bridge PRIVATE PJARCZAK_LINUX_BRIDGE_LIGHTWEIGHT_TASKS=1)' in bridge_cmake:
    errors.append("macOS bridge must use real Orca BBLModelTask")


for name in (
    "PrintParams",
    "PrintParams_0203",
    "PrintParams_Legacy",
    "TaskQueryParams",
    "FilamentQueryParams",
    "FilamentDeleteParams",
    "AmsSyncItem",
    "AmsSyncParams",
    "PublishParams",
):
    try:
        if struct_body(upstream, name) != struct_body(host_abi, name):
            errors.append(f"C++ ABI divergence: {name}")
    except ValueError as exc:
        errors.append(str(exc))

imports = set(re.findall(
    r'\bget_function\s*\(\s*"(bambu_network_[A-Za-z0-9_]+)"\s*\)',
    loader,
))
exports = set(re.findall(
    r'\bPJBRIDGE_EXPORT\b[^\n]*?\b(bambu_network_[A-Za-z0-9_]+)\s*\(',
    forwarder,
))
if not imports or not exports:
    errors.append("empty imported/exported function inventory")
for method in sorted(imports - exports):
    errors.append(f"missing bridge export: {method}")

rpc_calls = set(re.findall(r'"((?:net|src)\.[a-z0-9_]+)"', forwarder))
host_calls = set(re.findall(r'method\s*==\s*"((?:net|src)\.[a-z0-9_]+)"', host))
for method in sorted(rpc_calls - host_calls):
    errors.append(f"unhandled host RPC: {method}")

if 'option(ORCA_BAMBU_NETWORK_BRIDGE "Build the experimental BambuNetwork RPC bridge" OFF)' not in cmake:
    errors.append("bridge CMake option is not explicitly OFF by default")

if 'std::string dev_model' not in forwarder:
    errors.append("forwarder bind is missing current dev_model argument")
if 'params.value("dev_model", std::string())' not in host:
    errors.append("host bind is missing current dev_model argument")
if '-static-libstdc++' in read("tools/pjarczak_bambu_linux_host/CMakeLists.txt"):
    errors.append("Linux host must dynamically link libstdc++ when loading plug-in")

print(f"BambuNetwork imports={len(imports)}, exports={len(exports)}, RPC calls={len(rpc_calls)}")
print(f"Host handled RPC={len(host_calls)}, ABI structs compared=9")
for error in errors:
    print("FAIL:", error)
if errors:
    raise SystemExit(1)
print("PASS: static symbol, RPC, ABI-layout snapshot, and build-option checks")
print("NOTE: compile and real-printer validation are still required")
