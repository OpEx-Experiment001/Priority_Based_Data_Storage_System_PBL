#define _WIN32_WINNT 0x0600
#include <windows.h>
#include <iostream>
#include <string>
#include "os_priority_queue.hpp"
#include "json_document_store.hpp"
#include "priority_classifier.hpp"
#include "os_worker_pool.hpp"
#include "server_socket.hpp"

BOOL WINAPI ConsoleHandler(DWORD signal) {
    if (signal == CTRL_C_EVENT || signal == CTRL_CLOSE_EVENT) {
        std::cout << "\n[C++ Engine] Shutting down gracefully..." << std::endl;
        ExitProcess(0);
    }
    return TRUE;
}

int main(int argc, char* argv[]) {
    SetConsoleCtrlHandler(ConsoleHandler, TRUE);

    int port = 8085;
    std::string db_path = "./data_store/storage.json";

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--port" && i + 1 < argc) {
            port = std::stoi(argv[++i]);
        } else if (arg == "--db" && i + 1 < argc) {
            db_path = argv[++i];
        }
    }

    std::cout << "=======================================================" << std::endl;
    std::cout << "   HIGH-PERFORMANCE C++ PRIORITY TELEMETRY ENGINE     " << std::endl;
    std::cout << "   OS Concurrency: Win32 PriorityQueue + ThreadPool   " << std::endl;
    std::cout << "   Document Store: " << db_path << std::endl;
    std::cout << "   TCP Microservice Port: " << port << std::endl;
    std::cout << "=======================================================" << std::endl;

    JsonDocumentStore store(db_path);
    OSPriorityQueue queue;
    PriorityClassifier classifier(store);

    ServerSocket* server_ptr = nullptr;

    auto broadcaster = [&server_ptr](const std::string& ev, const json& data) {
        if (server_ptr) {
            server_ptr->broadcast(ev, data);
        }
    };

    OSWorkerPool pool(store, queue, broadcaster);
    pool.start();
    std::cout << "[C++ Engine] Worker Thread Pool initialized." << std::endl;

    ServerSocket server(port, store, queue, pool, classifier);
    server_ptr = &server;

    if (!server.start()) {
        std::cerr << "[C++ Engine] Failed to start socket server on port " << port << std::endl;
        return 1;
    }

    std::cout << "[C++ Engine] Priority Engine running. Ready for telemetry packets." << std::endl;

    // Keep main thread alive
    while (true) {
        Sleep(5000);
    }

    return 0;
}

