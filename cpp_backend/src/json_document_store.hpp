#ifndef JSON_DOCUMENT_STORE_HPP
#define JSON_DOCUMENT_STORE_HPP

#define _WIN32_WINNT 0x0600
#include <windows.h>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>
#include <chrono>
#include <iomanip>
#include <sstream>
#include "../include/nlohmann/json.hpp"

using json = nlohmann::json;

class JsonDocumentStore {
private:
    CRITICAL_SECTION cs;
    std::string file_path;
    json doc_root;
    uint64_t next_id;

    std::string current_iso_time() {
        auto now = std::chrono::system_clock::now();
        auto in_time_t = std::chrono::system_clock::to_time_t(now);
        std::stringstream ss;
        ss << std::put_time(std::gmtime(&in_time_t), "%Y-%m-%dT%H:%M:%SZ");
        return ss.str();
    }

    void load_from_disk() {
        std::ifstream file(file_path);
        if (file.is_open()) {
            try {
                file >> doc_root;
            } catch (...) {
                doc_root = json::object();
            }
            file.close();
        } else {
            doc_root = json::object();
        }

        if (!doc_root.contains("records") || !doc_root["records"].is_object()) {
            doc_root["records"] = json::object();
        }
        if (!doc_root.contains("attempts") || !doc_root["attempts"].is_array()) {
            doc_root["attempts"] = json::array();
        }
        if (!doc_root.contains("rules") || !doc_root["rules"].is_object()) {
            doc_root["rules"] = json::object();
            seed_default_rules();
        }

        // Determine next ID
        next_id = 1;
        for (auto& el : doc_root["records"].items()) {
            try {
                uint64_t id_val = std::stoull(el.key());
                if (id_val >= next_id) next_id = id_val + 1;
            } catch (...) {}
        }
    }

    void save_to_disk() {
        // Ensure directory exists
        size_t last_slash = file_path.find_last_of("\\/");
        if (last_slash != std::string::npos) {
            std::string dir = file_path.substr(0, last_slash);
            CreateDirectoryA(dir.c_str(), NULL);
        }
        std::ofstream file(file_path);
        if (file.is_open()) {
            file << doc_root.dump(2);
            file.close();
        }
    }

    void seed_default_rules() {
        doc_root["rules"]["rule-gas-high"] = {
            {"rule_id", "rule-gas-high"},
            {"module_id", 101},
            {"sensor_index", 2},
            {"sensor_name", "Gas PPM"},
            {"operator", ">="},
            {"threshold", 800.0},
            {"priority", "HIGH"},
            {"description", "Hazardous toxic gas concentration spike"}
        };
        doc_root["rules"]["rule-gas-med"] = {
            {"rule_id", "rule-gas-med"},
            {"module_id", 101},
            {"sensor_index", 2},
            {"sensor_name", "Gas PPM"},
            {"operator", ">="},
            {"threshold", 600.0},
            {"priority", "MEDIUM"},
            {"description", "Elevated gas concentration warning"}
        };
    }

public:
    JsonDocumentStore(const std::string& path) : file_path(path), next_id(1) {
        InitializeCriticalSection(&cs);
        load_from_disk();
        save_to_disk();
    }

    ~JsonDocumentStore() {
        EnterCriticalSection(&cs);
        save_to_disk();
        LeaveCriticalSection(&cs);
        DeleteCriticalSection(&cs);
    }

    std::string persist_record(json& rec) {
        EnterCriticalSection(&cs);
        std::string data_id = std::to_string(next_id++);
        std::string now_iso = current_iso_time();

        json doc = {
            {"data_id", data_id},
            {"module_id", rec.value("module_id", 101)},
            {"source_port", rec.value("source_port", "CPP_SERIAL")},
            {"raw_message", rec.value("raw_message", "")},
            {"received_at", rec.value("received_at", now_iso)},
            {"values", rec.value("values", json::array())},
            {"priority", rec.value("priority", "LOW")},
            {"status", "PENDING"},
            {"attempt_count", 0},
            {"max_attempts", 3},
            {"last_error", nullptr},
            {"submitted_at", now_iso},
            {"started_at", nullptr},
            {"completed_at", nullptr},
            {"next_retry_at", nullptr}
        };

        doc_root["records"][data_id] = doc;
        save_to_disk();
        rec["data_id"] = data_id;
        LeaveCriticalSection(&cs);
        return data_id;
    }

    json get_record(const std::string& data_id) {
        EnterCriticalSection(&cs);
        json res = nullptr;
        if (doc_root["records"].contains(data_id)) {
            res = doc_root["records"][data_id];
        }
        LeaveCriticalSection(&cs);
        return res;
    }

    json list_records(int limit = 50, int offset = 0, const std::string& status = "", const std::string& priority = "") {
        EnterCriticalSection(&cs);
        std::vector<json> filtered;
        for (auto& el : doc_root["records"].items()) {
            json item = el.value();
            if (!status.empty() && item.value("status", "") != status) continue;
            if (!priority.empty() && item.value("priority", "") != priority) continue;
            filtered.push_back(item);
        }

        // Newest first (reverse numerical order of data_id)
        std::sort(filtered.begin(), filtered.end(), [](const json& a, const json& b) {
            try {
                return std::stoull(a["data_id"].get<std::string>()) > std::stoull(b["data_id"].get<std::string>());
            } catch (...) { return false; }
        });

        json result = json::array();
        int total = (int)filtered.size();
        for (int i = offset; i < total && (int)result.size() < limit; ++i) {
            result.push_back(filtered[i]);
        }
        LeaveCriticalSection(&cs);
        return result;
    }

    int count_records(const std::string& status = "", const std::string& priority = "") {
        EnterCriticalSection(&cs);
        int cnt = 0;
        for (auto& el : doc_root["records"].items()) {
            json item = el.value();
            if (!status.empty() && item.value("status", "") != status) continue;
            if (!priority.empty() && item.value("priority", "") != priority) continue;
            cnt++;
        }
        LeaveCriticalSection(&cs);
        return cnt;
    }

    bool update_record_status(const std::string& data_id, const std::string& status, int attempt_count = -1,
                             const std::string& error = "", bool started = false, bool completed = false,
                             const std::string& retry_at = "") {
        EnterCriticalSection(&cs);
        if (!doc_root["records"].contains(data_id)) {
            LeaveCriticalSection(&cs);
            return false;
        }

        doc_root["records"][data_id]["status"] = status;
        if (!error.empty()) doc_root["records"][data_id]["last_error"] = error;
        else if (status == "COMPLETED") doc_root["records"][data_id]["last_error"] = nullptr;

        if (attempt_count >= 0) doc_root["records"][data_id]["attempt_count"] = attempt_count;
        if (started) doc_root["records"][data_id]["started_at"] = current_iso_time();
        if (completed) doc_root["records"][data_id]["completed_at"] = current_iso_time();
        if (!retry_at.empty()) doc_root["records"][data_id]["next_retry_at"] = retry_at;

        save_to_disk();
        LeaveCriticalSection(&cs);
        return true;
    }

    bool update_record(const std::string& data_id, const json& updates) {
        EnterCriticalSection(&cs);
        if (!doc_root["records"].contains(data_id)) {
            LeaveCriticalSection(&cs);
            return false;
        }
        for (auto& it : updates.items()) {
            if (it.key() != "data_id") {
                doc_root["records"][data_id][it.key()] = it.value();
            }
        }
        save_to_disk();
        LeaveCriticalSection(&cs);
        return true;
    }

    bool delete_record(const std::string& data_id) {
        EnterCriticalSection(&cs);
        if (doc_root["records"].contains(data_id)) {
            doc_root["records"].erase(data_id);
            save_to_disk();
            LeaveCriticalSection(&cs);
            return true;
        }
        LeaveCriticalSection(&cs);
        return false;
    }

    int batch_delete_records(const std::vector<std::string>& ids) {
        EnterCriticalSection(&cs);
        int deleted = 0;
        for (const auto& id : ids) {
            if (doc_root["records"].contains(id)) {
                doc_root["records"].erase(id);
                deleted++;
            }
        }
        if (deleted > 0) save_to_disk();
        LeaveCriticalSection(&cs);
        return deleted;
    }

    int batch_update_priority(const std::vector<std::string>& ids, const std::string& priority) {
        EnterCriticalSection(&cs);
        int updated = 0;
        for (const auto& id : ids) {
            if (doc_root["records"].contains(id)) {
                doc_root["records"][id]["priority"] = priority;
                updated++;
            }
        }
        if (updated > 0) save_to_disk();
        LeaveCriticalSection(&cs);
        return updated;
    }

    int purge_records(const std::string& status = "") {
        EnterCriticalSection(&cs);
        int purged = 0;
        if (status.empty()) {
            purged = (int)doc_root["records"].size();
            doc_root["records"] = json::object();
        } else {
            std::vector<std::string> to_remove;
            for (auto& el : doc_root["records"].items()) {
                if (el.value().value("status", "") == status) {
                    to_remove.push_back(el.key());
                }
            }
            for (const auto& k : to_remove) {
                doc_root["records"].erase(k);
                purged++;
            }
        }
        save_to_disk();
        LeaveCriticalSection(&cs);
        return purged;
    }

    std::vector<json> fetch_pending_records(int limit = 100) {
        EnterCriticalSection(&cs);
        std::vector<json> pending;
        for (auto& el : doc_root["records"].items()) {
            json item = el.value();
            std::string st = item.value("status", "");
            if (st == "PENDING" || st == "RETRY_WAIT") {
                pending.push_back(item);
                if ((int)pending.size() >= limit) break;
            }
        }
        LeaveCriticalSection(&cs);
        return pending;
    }

    void log_attempt(const std::string& data_id, int attempt_num, const std::string& outcome,
                     const std::string& error, const std::string& started, const std::string& ended) {
        EnterCriticalSection(&cs);
        doc_root["attempts"].push_back({
            {"data_id", data_id},
            {"attempt_number", attempt_num},
            {"outcome", outcome},
            {"error_message", error},
            {"started_at", started},
            {"ended_at", ended}
        });
        save_to_disk();
        LeaveCriticalSection(&cs);
    }

    json get_priority_counts() {
        EnterCriticalSection(&cs);
        int high = 0, med = 0, low = 0;
        for (auto& el : doc_root["records"].items()) {
            std::string p = el.value().value("priority", "LOW");
            if (p == "HIGH") high++;
            else if (p == "MEDIUM") med++;
            else low++;
        }
        LeaveCriticalSection(&cs);
        return {{"HIGH", high}, {"MEDIUM", med}, {"LOW", low}};
    }

    json get_rules() {
        EnterCriticalSection(&cs);
        json arr = json::array();
        for (auto& el : doc_root["rules"].items()) {
            arr.push_back(el.value());
        }
        LeaveCriticalSection(&cs);
        return arr;
    }

    std::string save_rule(const json& rule_data) {
        EnterCriticalSection(&cs);
        std::string rid = rule_data.value("rule_id", "");
        if (rid.empty()) rid = "rule-" + std::to_string(std::chrono::system_clock::now().time_since_epoch().count());
        
        json r = rule_data;
        r["rule_id"] = rid;
        doc_root["rules"][rid] = r;
        save_to_disk();
        LeaveCriticalSection(&cs);
        return rid;
    }

    bool delete_rule(const std::string& rule_id) {
        EnterCriticalSection(&cs);
        if (doc_root["rules"].contains(rule_id)) {
            doc_root["rules"].erase(rule_id);
            save_to_disk();
            LeaveCriticalSection(&cs);
            return true;
        }
        LeaveCriticalSection(&cs);
        return false;
    }
};

#endif // JSON_DOCUMENT_STORE_HPP

