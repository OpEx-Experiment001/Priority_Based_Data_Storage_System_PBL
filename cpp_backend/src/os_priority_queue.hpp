#ifndef OS_PRIORITY_QUEUE_HPP
#define OS_PRIORITY_QUEUE_HPP

#define _WIN32_WINNT 0x0600
#include <windows.h>
#include <queue>
#include <vector>
#include <cstdint>
#include "../include/nlohmann/json.hpp"

struct PriorityItem {
    int priority_rank; // 0 = HIGH, 1 = MEDIUM, 2 = LOW
    uint64_t sequence_id;
    nlohmann::json record;
};

struct PriorityComparator {
    bool operator()(const PriorityItem& a, const PriorityItem& b) const {
        if (a.priority_rank != b.priority_rank) {
            return a.priority_rank > b.priority_rank; // 0 has highest priority
        }
        return a.sequence_id > b.sequence_id; // FIFO tie-breaker for identical priorities
    }
};

class OSPriorityQueue {
private:
    CRITICAL_SECTION cs;
    CONDITION_VARIABLE cv;
    std::priority_queue<PriorityItem, std::vector<PriorityItem>, PriorityComparator> queue;
    uint64_t next_seq;

public:
    OSPriorityQueue() : next_seq(1) {
        InitializeCriticalSection(&cs);
        InitializeConditionVariable(&cv);
    }

    ~OSPriorityQueue() {
        DeleteCriticalSection(&cs);
    }

    uint64_t push(int priority_rank, const nlohmann::json& record) {
        EnterCriticalSection(&cs);
        uint64_t seq = next_seq++;
        PriorityItem item{priority_rank, seq, record};
        queue.push(item);
        WakeConditionVariable(&cv);
        LeaveCriticalSection(&cs);
        return seq;
    }

    bool pop_wait(PriorityItem& out_item, DWORD timeout_ms = 1000) {
        EnterCriticalSection(&cs);
        while (queue.empty()) {
            if (!SleepConditionVariableCS(&cv, &cs, timeout_ms)) {
                LeaveCriticalSection(&cs);
                return false; // Timed out
            }
        }
        out_item = queue.top();
        queue.pop();
        LeaveCriticalSection(&cs);
        return true;
    }

    size_t size() {
        EnterCriticalSection(&cs);
        size_t s = queue.size();
        LeaveCriticalSection(&cs);
        return s;
    }

    bool empty() {
        EnterCriticalSection(&cs);
        bool e = queue.empty();
        LeaveCriticalSection(&cs);
        return e;
    }
};

#endif // OS_PRIORITY_QUEUE_HPP

