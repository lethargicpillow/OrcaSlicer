#include "LinuxPluginHost.hpp"
#include "../../src/slic3r/Utils/PJarczakLinuxBridge/PJarczakLinuxSoBridgeRpcProtocol.hpp"

#include <cstdlib>
#include <iostream>
#include <mutex>
#include <string>
#include <vector>
#include <fstream>
#include <unistd.h>
#include <cstdio>
#include <thread>
#include <filesystem>
#include <system_error>

using namespace Slic3r::PJarczakLinuxBridge;

namespace {
int run_probe_auth()
{
    LinuxPluginHost host;
    auto hs = host.handle("bridge.handshake", nlohmann::json::object());
    if (!hs.value("network_loaded", false))
        return 2;

    const std::string log_dir = std::getenv("PJARCZAK_BAMBU_PROBE_LOG_DIR") ? std::getenv("PJARCZAK_BAMBU_PROBE_LOG_DIR") : std::string(".");
    const std::string country = std::getenv("PJARCZAK_BAMBU_COUNTRY_CODE") ? std::getenv("PJARCZAK_BAMBU_COUNTRY_CODE") : std::string("PL");
    // The proprietary plug-in requires a certificate folder even to destroy
    // a freshly created agent. A version/ABI handshake alone is not enough.
    const char* configured_cert_dir = std::getenv("PJARCZAK_BAMBU_PROBE_CERT_DIR");
    std::filesystem::path cert_dir;
    if (configured_cert_dir && *configured_cert_dir) {
        cert_dir = configured_cert_dir;
    } else if (std::filesystem::exists(std::filesystem::path("resources/cert") / "slicer_base64.cer")) {
        cert_dir = std::filesystem::absolute("resources/cert");
    } else {
        std::error_code ec;
        const auto self = std::filesystem::read_symlink("/proc/self/exe", ec);
        if (!ec)
            cert_dir = self.parent_path();
    }
    if (cert_dir.empty() || !std::filesystem::is_regular_file(cert_dir / "slicer_base64.cer")) {
        std::cerr << "missing slicer_base64.cer: set PJARCZAK_BAMBU_PROBE_CERT_DIR to the cert folder\n";
        return 9;
    }
    const std::string cert_dir_string = cert_dir.string();
    if (::setenv("PJARCZAK_BAMBU_CERT_DIR", cert_dir_string.c_str(), 1) != 0)
        return 12;
    std::error_code create_ec;
    std::filesystem::create_directories(log_dir, create_ec);
    if (create_ec) {
        std::cerr << "cannot create probe log directory: " << create_ec.message() << '\n';
        return 10;
    }
    auto created = host.handle("net.create_agent", {{"log_dir", log_dir}, {"country_code", country}});
    if (!created.value("ok", false))
        return 3;
    const auto agent = created.value("value", 0LL);
    if (agent <= 0)
        return 4;

    auto step = [&](const char* method, nlohmann::json payload) -> bool {
        payload["agent"] = agent;
        auto r = host.handle(method, payload);
        return r.value("ok", false) && r.value("value", -1) == 0;
    };

    // Set the certificate before any operation that may destroy the agent.
    // Returning normally from a failed set_cert_file could invoke a crashing
    // plug-in destructor; exit this isolated probe without C++ static teardown.
    if (!step("net.set_cert_file", {{"folder", cert_dir.string()}, {"filename", "slicer_base64.cer"}})) {
        std::cerr << "net.set_cert_file failed; refusing unsafe destroy_agent\n";
        std::_Exit(11);
    }
    // Probe cleanup as well as setup. Otherwise the plugin can leave workers
    // running and abort during process teardown.
    if (!step("net.set_config_dir", {{"config_dir", log_dir}})) {
        (void)step("net.destroy_agent", nlohmann::json::object());
        return 5;
    }
    if (!step("net.init_log", nlohmann::json::object())) {
        (void)step("net.destroy_agent", nlohmann::json::object());
        return 6;
    }
    (void)step("net.set_country_code", {{"country_code", country}});
    const bool started = step("net.start", nlohmann::json::object());

    auto is_login = started ? host.handle("net.is_user_login", {{"agent", agent}})
                            : nlohmann::json::object();
    const bool destroyed = step("net.destroy_agent", nlohmann::json::object());
    if (!started || !is_login.value("ok", false))
        return 7;
    if (!destroyed)
        return 8;

    return 0;
}
}

int main(int argc, char** argv)
{
    std::ios::sync_with_stdio(false);

    if (argc > 1 && std::string(argv[1]) == "--probe-auth")
        return run_probe_auth();

    const int rpc_fd = ::dup(STDOUT_FILENO);
    if (rpc_fd < 0)
        return 100;
    std::fflush(stdout);
    ::dup2(STDERR_FILENO, STDOUT_FILENO);
    std::ofstream rpc_out(std::string("/proc/self/fd/") + std::to_string(rpc_fd), std::ios::binary | std::ios::out);
    if (!rpc_out.good())
        return 101;

    LinuxPluginHost host;
    std::mutex out_mutex;

    while (true) {
        RawRpcFrame raw;
        std::string err;
        if (!read_raw_frame(std::cin, raw, err))
            break;

        RpcFrame req;
        if (!read_request_frame(raw, req, err)) {
            std::lock_guard<std::mutex> lock(out_mutex);
            write_json_frame(rpc_out, RpcFrameType::json_response, 0, {{"ok", false}, {"error", err}});
            continue;
        }

        std::vector<unsigned char> request_binary;
        if (req.payload.value("__binary_request", false)) {
            RawRpcFrame binary_raw;
            if (!read_raw_frame(std::cin, binary_raw, err) ||
                binary_raw.type != RpcFrameType::binary_data ||
                binary_raw.id != req.id) {
                std::lock_guard<std::mutex> lock(out_mutex);
                write_json_frame(rpc_out, RpcFrameType::json_response, req.id, {{"ok", false}, {"error", "missing binary request payload"}});
                continue;
            }
            request_binary = std::move(binary_raw.payload);
        }

        std::thread([&host, &rpc_out, &out_mutex, req_id = req.id, req_method = req.method, req_payload = req.payload, request_binary = std::move(request_binary)]() mutable {
            LinuxPluginHost::set_thread_request_binary(std::move(request_binary));
            nlohmann::json resp = host.handle(req_method, req_payload);

            std::vector<unsigned char> reply_binary;
            const bool has_reply_binary = LinuxPluginHost::consume_thread_reply_binary(reply_binary);

            std::lock_guard<std::mutex> lock(out_mutex);
            write_json_frame(rpc_out, RpcFrameType::json_response, req_id, resp);
            if (has_reply_binary)
                write_raw_frame(rpc_out, RpcFrameType::binary_data, req_id, reply_binary.data(), reply_binary.size());
        }).detach();
    }

    return 0;
}
