#ifndef PRIORITY_CLASSIFIER_HPP
#define PRIORITY_CLASSIFIER_HPP

#include <string>
#include <vector>
#include "json_document_store.hpp"

class PriorityClassifier {
private:
    JsonDocumentStore& store;

public:
    PriorityClassifier(JsonDocumentStore& s) : store(s) {}

    std::string classify(int module_id, const std::vector<double>& values) {
        json rules = store.get_rules();

        // Check dynamic rules
        for (const auto& r : rules) {
            int r_mod = r.value("module_id", 0);
            if (r_mod != module_id) continue;

            int idx = r.value("sensor_index", -1);
            if (idx < 0 || idx >= (int)values.size()) continue;

            double val = values[idx];
            double threshold = r.value("threshold", 0.0);
            std::string op = r.value("operator", ">=");
            std::string prio = r.value("priority", "LOW");

            bool match = false;
            if ((op == ">=" || op == "GTE") && val >= threshold) match = true;
            else if ((op == ">" || op == "GT") && val > threshold) match = true;
            else if ((op == "<=" || op == "LTE") && val <= threshold) match = true;
            else if ((op == "<" || op == "LT") && val < threshold) match = true;
            else if ((op == "==" || op == "EQ") && std::abs(val - threshold) < 1e-6) match = true;

            if (match) return prio;
        }

        // Fallback default rules
        if (module_id == 101 && values.size() >= 3) {
            double gas = values[2];
            if (gas >= 800.0) return "HIGH";
            if (gas >= 600.0) return "MEDIUM";
        }
        return "LOW";
    }
};

#endif // PRIORITY_CLASSIFIER_HPP

