#ifndef SERVER_SOCKET_HPP
#define SERVER_SOCKET_HPP

#define _WIN32_WINNT 0x0600
#include <winsock2.h>
#include <ws2tcpip.h>
#include <iostream>
#include <vector>
#include <string>
#include <sstream>
#include "../include/nlohmann/json.hpp"
#include "os_priority_queue.hpp"
#include "os_worker_pool.hpp"
#include "json_document_store.hpp"
#include "priority_classifier.hpp"

using json = nlohmann::json;

class ServerSocket {
private:
    int port;
    SOCKET listen_sock;
    CRITICAL_SECTION clients_cs;
    std::vector<SOCKET> clients;
    bool is_running;

    JsonDocumentStore& store;
    OSPriorityQueue& queue;
    OSWorkerPool& pool;
    PriorityClassifier& classifier;

    static DWORD WINAPI AcceptThreadEntry(LPVOID lpParam) {
        auto* self = static_cast<ServerSocket*>(lpParam);
        self->accept_loop();
        return 0;
    }

    static DWORD WINAPI ClientThreadEntry(LPVOID lpParam) {
        struct ClientContext {
            ServerSocket* server;
            SOCKET sock;
        };
        auto* ctx = static_cast<ClientContext*>(lpParam);
        ctx->server->client_loop(ctx->sock);
        delete ctx;
        return 0;
    }

    void accept_loop() {
        while (is_running) {
            sockaddr_in client_addr;
            int addr_len = sizeof(client_addr);
            SOCKET client_sock = accept(listen_sock, (sockaddr*)&client_addr, &addr_len);
            if (client_sock == INVALID_SOCKET) {
                if (!is_running) break;
                continue;
            }

            EnterCriticalSection(&clients_cs);
            clients.push_back(client_sock);
            LeaveCriticalSection(&clients_cs);

            struct ClientContext {
                ServerSocket* server;
                SOCKET sock;
            };
            auto* ctx = new ClientContext{this, client_sock};
            CreateThread(NULL, 0, ClientThreadEntry, ctx, 0, NULL);
        }
    }

    void client_loop(SOCKET sock) {
        char buffer[4096];
        std::string accumulated;

        while (is_running) {
            int bytes_received = recv(sock, buffer, sizeof(buffer) - 1, 0);
            if (bytes_received <= 0) break;

            buffer[bytes_received] = '\0';
            accumulated += buffer;

            size_t newline_pos;
            while ((newline_pos = accumulated.find('\n')) != std::string::npos) {
                std::string line = accumulated.substr(0, newline_pos);
                accumulated.erase(0, newline_pos + 1);

                if (line.empty() || line == "\r") continue;

                json response;
                try {
                    json req = json::parse(line);
                    response = handle_request(req, sock);
                } catch (const std::exception& e) {
                    response = {{"error", e.what()}, {"status", "ERROR"}};
                }

                std::string out_str = response.dump() + "\n";
                send(sock, out_str.c_str(), (int)out_str.length(), 0);
            }
        }

        // Cleanup disconnected client
        EnterCriticalSection(&clients_cs);
        for (auto it = clients.begin(); it != clients.end(); ++it) {
            if (*it == sock) {
                clients.erase(it);
                break;
            }
        }
        LeaveCriticalSection(&clients_cs);
        closesocket(sock);
    }

    json handle_request(const json& req, SOCKET caller_sock = INVALID_SOCKET) {
        std::string action = req.value("action", "");

        if (action == "PING") {
            return {{"status", "PONG"}, {"server", "CPP_PRIORITY_ENGINE"}};
        }
        else if (action == "GET_STATUS") {
            return {
                {"status", "OK"},
                {"storage_engine", "JSON (C++ Document Engine)"},
                {"queue_depth", queue.size()},
                {"active_workers", pool.get_active_workers()},
                {"total_records", store.count_records()},
                {"priority_counts", store.get_priority_counts()}
            };
        }
        else if (action == "INGEST") {
            json rec = req.value("record", json::object());
            int mod_id = rec.value("module_id", 101);
            std::vector<double> vals;
            if (rec.contains("values") && rec["values"].is_array()) {
                for (auto& v : rec["values"]) vals.push_back(v.get<double>());
            }

            std::string prio = rec.value("priority", "");
            if (prio.empty()) {
                prio = classifier.classify(mod_id, vals);
                rec["priority"] = prio;
            }

            std::string data_id = store.persist_record(rec);
            store.update_record_status(data_id, "QUEUED");
            
            int rank = (prio == "HIGH") ? 0 : ((prio == "MEDIUM") ? 1 : 2);
            queue.push(rank, rec);

            broadcast("record_ingested", {
                {"data_id", data_id},
                {"module_id", mod_id},
                {"priority", prio},
                {"values", vals},
                {"queue_depth", queue.size()}
            }, caller_sock);

            return {{"status", "INGESTED"}, {"data_id", data_id}, {"priority", prio}};
        }
        else if (action == "GET_RECORDS") {
            int limit = req.value("limit", 100);
            int offset = req.value("offset", 0);
            std::string status = req.value("status", "");
            std::string priority = req.value("priority", "");
            return {
                {"status", "OK"},
                {"records", store.list_records(limit, offset, status, priority)},
                {"total", store.count_records(status, priority)}
            };
        }
        else if (action == "GET_RECORD") {
            std::string id = req.value("data_id", "");
            json item = store.get_record(id);
            if (item != nullptr) return {{"status", "OK"}, {"record", item}};
            return {{"status", "NOT_FOUND"}};
        }
        else if (action == "UPDATE_RECORD") {
            std::string id = req.value("data_id", "");
            json updates = req.value("updates", json::object());
            bool ok = store.update_record(id, updates);
            return {{"status", ok ? "OK" : "FAILED"}};
        }
        else if (action == "DELETE_RECORD") {
            std::string id = req.value("data_id", "");
            bool ok = store.delete_record(id);
            return {{"status", ok ? "DELETED" : "NOT_FOUND"}};
        }
        else if (action == "BATCH_DELETE") {
            std::vector<std::string> ids;
            if (req.contains("data_ids")) {
                for (auto& id : req["data_ids"]) ids.push_back(id.get<std::string>());
            }
            int deleted = store.batch_delete_records(ids);
            return {{"status", "OK"}, {"deleted_count", deleted}};
        }
        else if (action == "BATCH_PRIORITY") {
            std::vector<std::string> ids;
            std::string prio = req.value("priority", "LOW");
            if (req.contains("data_ids")) {
                for (auto& id : req["data_ids"]) ids.push_back(id.get<std::string>());
            }
            int updated = store.batch_update_priority(ids, prio);
            return {{"status", "OK"}, {"updated_count", updated}};
        }
        else if (action == "PURGE_RECORDS") {
            std::string status = req.value("status", "");
            int purged = store.purge_records(status);
            return {{"status", "OK"}, {"purged_count", purged}};
        }
        else if (action == "GET_RULES") {
            return {{"status", "OK"}, {"rules", store.get_rules()}};
        }
        else if (action == "SAVE_RULE") {
            json r = req.value("rule", json::object());
            std::string rid = store.save_rule(r);
            return {{"status", "SAVED"}, {"rule_id", rid}};
        }
        else if (action == "DELETE_RULE") {
            std::string rid = req.value("rule_id", "");
            bool ok = store.delete_rule(rid);
            return {{"status", ok ? "DELETED" : "NOT_FOUND"}};
        }

        return {{"error", "Unknown action"}, {"status", "INVALID_REQUEST"}};
    }

public:
    ServerSocket(int p, JsonDocumentStore& s, OSPriorityQueue& q, OSWorkerPool& wp, PriorityClassifier& pc)
        : port(p), listen_sock(INVALID_SOCKET), is_running(false),
          store(s), queue(q), pool(wp), classifier(pc) {
        InitializeCriticalSection(&clients_cs);
    }

    ~ServerSocket() {
        stop();
        DeleteCriticalSection(&clients_cs);
    }

    bool start() {
        WSADATA wsaData;
        if (WSAStartup(MAKEWORD(2, 2), &wsaData) != 0) {
            std::cerr << "WSAStartup failed." << std::endl;
            return false;
        }

        listen_sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (listen_sock == INVALID_SOCKET) {
            WSACleanup();
            return false;
        }

        int opt = 1;
        setsockopt(listen_sock, SOL_SOCKET, SO_REUSEADDR, (const char*)&opt, sizeof(opt));

        sockaddr_in server_addr;
        server_addr.sin_family = AF_INET;
        server_addr.sin_addr.s_addr = INADDR_ANY;
        server_addr.sin_port = htons(port);

        if (bind(listen_sock, (sockaddr*)&server_addr, sizeof(server_addr)) == SOCKET_ERROR) {
            closesocket(listen_sock);
            WSACleanup();
            return false;
        }

        if (listen(listen_sock, SOMAXCONN) == SOCKET_ERROR) {
            closesocket(listen_sock);
            WSACleanup();
            return false;
        }

        is_running = true;
        CreateThread(NULL, 0, AcceptThreadEntry, this, 0, NULL);
        std::cout << "[C++ Engine] Microservice Server listening on TCP port " << port << std::endl;
        return true;
    }

    void stop() {
        if (!is_running) return;
        is_running = false;
        if (listen_sock != INVALID_SOCKET) {
            closesocket(listen_sock);
            listen_sock = INVALID_SOCKET;
        }
        EnterCriticalSection(&clients_cs);
        for (SOCKET s : clients) {
            closesocket(s);
        }
        clients.clear();
        LeaveCriticalSection(&clients_cs);
        WSACleanup();
    }

    void broadcast(const std::string& event_type, const json& payload, SOCKET exclude_sock = INVALID_SOCKET) {
        json msg = {
            {"event", event_type},
            {"data", payload}
        };
        std::string out_str = msg.dump() + "\n";

        EnterCriticalSection(&clients_cs);
        for (SOCKET s : clients) {
            if (s != exclude_sock) {
                send(s, out_str.c_str(), (int)out_str.length(), 0);
            }
        }
        LeaveCriticalSection(&clients_cs);
    }
};

#endif // SERVER_SOCKET_HPP
