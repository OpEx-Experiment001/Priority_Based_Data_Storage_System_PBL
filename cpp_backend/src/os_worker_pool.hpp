#ifndef OS_WORKER_POOL_HPP
#define OS_WORKER_POOL_HPP

#define _WIN32_WINNT 0x0600
#include <windows.h>
#include <vector>
#include <string>
#include <atomic>
#include <functional>
#include "os_priority_queue.hpp"
#include "json_document_store.hpp"

class OSWorkerPool {
public:
    using EventBroadcaster = std::function<void(const std::string&, const json&)>;

private:
    JsonDocumentStore& store;
    OSPriorityQueue& queue;
    EventBroadcaster broadcast_fn;

    int min_workers;
    int max_workers;
    int scale_up_depth;
    int scale_down_depth;

    CRITICAL_SECTION pool_cs;
    std::vector<HANDLE> worker_threads;
    std::atomic<bool> is_running;
    std::atomic<int> active_workers;
    std::atomic<int> retire_tokens;
    HANDLE autoscaler_thread;

    static DWORD WINAPI WorkerThreadEntry(LPVOID lpParam) {
        auto* self = static_cast<OSWorkerPool*>(lpParam);
        self->worker_loop();
        return 0;
    }

    static DWORD WINAPI AutoscalerThreadEntry(LPVOID lpParam) {
        auto* self = static_cast<OSWorkerPool*>(lpParam);
        self->autoscale_loop();
        return 0;
    }

    void spawn_worker() {
        EnterCriticalSection(&pool_cs);
        if (active_workers >= max_workers) {
            LeaveCriticalSection(&pool_cs);
            return;
        }
        active_workers++;
        HANDLE hThread = CreateThread(NULL, 0, WorkerThreadEntry, this, 0, NULL);
        worker_threads.push_back(hThread);
        LeaveCriticalSection(&pool_cs);

        if (broadcast_fn) {
            broadcast_fn("system_event", {
                {"type", "WORKER_SPAWNED"},
                {"active_workers", active_workers.load()}
            });
        }
    }

    void worker_loop() {
        while (is_running) {
            PriorityItem item;
            bool popped = queue.pop_wait(item, 1000);
            if (!popped) {
                // Timeout; check for scale-down retirement token
                if (retire_tokens > 0 && active_workers > min_workers) {
                    retire_tokens--;
                    active_workers--;
                    if (broadcast_fn) {
                        broadcast_fn("system_event", {
                            {"type", "WORKER_RETIRED"},
                            {"active_workers", active_workers.load()}
                        });
                    }
                    return; // Thread terminates cleanly
                }
                continue;
            }

            std::string data_id = item.record.value("data_id", "");
            if (data_id.empty()) continue;

            json current_rec = store.get_record(data_id);
            int attempt = (current_rec != nullptr) ? current_rec.value("attempt_count", 0) + 1 : 1;

            store.update_record_status(data_id, "PROCESSING", attempt, "", true, false);
            if (broadcast_fn) {
                broadcast_fn("record_processing", {
                    {"data_id", data_id},
                    {"priority", item.record.value("priority", "LOW")}
                });
            }

            // Simulated processing work
            Sleep(100);

            store.update_record_status(data_id, "COMPLETED", attempt, "", false, true);
            store.log_attempt(data_id, attempt, "COMPLETED", "", "", "");

            if (broadcast_fn) {
                broadcast_fn("record_completed", {
                    {"data_id", data_id},
                    {"attempt", attempt},
                    {"priority", item.record.value("priority", "LOW")}
                });
            }
        }
        active_workers--;
    }

    void autoscale_loop() {
        while (is_running) {
            Sleep(2000);
            size_t depth = queue.size();
            int current = active_workers.load();

            if (depth >= (size_t)scale_up_depth && current < max_workers) {
                spawn_worker();
                if (broadcast_fn) {
                    broadcast_fn("system_event", {
                        {"type", "AUTOSCALE_UP"},
                        {"depth", depth},
                        {"active_workers", active_workers.load()}
                    });
                }
            } else if (depth <= (size_t)scale_down_depth && current > min_workers) {
                retire_tokens++;
            }
        }
    }

public:
    OSWorkerPool(JsonDocumentStore& s, OSPriorityQueue& q, EventBroadcaster broadcaster = nullptr)
        : store(s), queue(q), broadcast_fn(broadcaster),
          min_workers(2), max_workers(6),
          scale_up_depth(10), scale_down_depth(2),
          is_running(false), active_workers(0), retire_tokens(0),
          autoscaler_thread(NULL) {
        InitializeCriticalSection(&pool_cs);
    }

    ~OSWorkerPool() {
        stop();
        DeleteCriticalSection(&pool_cs);
    }

    void start() {
        is_running = true;
        for (int i = 0; i < min_workers; ++i) {
            spawn_worker();
        }
        autoscaler_thread = CreateThread(NULL, 0, AutoscalerThreadEntry, this, 0, NULL);
    }

    void stop() {
        is_running = false;
        if (autoscaler_thread) {
            WaitForSingleObject(autoscaler_thread, 2000);
            CloseHandle(autoscaler_thread);
            autoscaler_thread = NULL;
        }
    }

    int get_active_workers() const {
        return active_workers.load();
    }
};

#endif // OS_WORKER_POOL_HPP

