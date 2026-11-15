import threading
from typing import List, Dict, Any

class PriorityClassifier:
    """
    Evaluates incoming telemetry against dynamic rules stored in the database.
    Assigns HIGH, MEDIUM, or LOW priority.
    """

    def __init__(self, storage):
        self.storage = storage
        self._lock = threading.RLock()

    def classify(self, module_id: int, values: List[float]) -> str:
        rules = self.storage.get_rules()
        
        # Sort rules so HIGH priorities are evaluated first
        priority_weight = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        sorted_rules = sorted(rules, key=lambda r: priority_weight.get(r.get("priority", "LOW"), 2))

        for rule in sorted_rules:
            if int(rule.get("module_id", 0)) != int(module_id):
                continue
            idx = int(rule.get("sensor_index", 0))
            if idx < 0 or idx >= len(values):
                continue
            
            val = values[idx]
            threshold = float(rule.get("threshold", 0.0))
            op = rule.get("operator", ">=")

            triggered = False
            if op in (">=", "GTE") and val >= threshold:
                triggered = True
            elif op in (">", "GT") and val > threshold:
                triggered = True
            elif op in ("<=", "LTE") and val <= threshold:
                triggered = True
            elif op in ("<", "LT") and val < threshold:
                triggered = True
            elif op in ("==", "EQ") and abs(val - threshold) < 1e-6:
                triggered = True

            if triggered:
                return rule.get("priority", "LOW")

        # Fallback default evaluation if no dynamic rule matched
        if module_id == 101 and len(values) >= 3:
            gas_ppm = values[2]
            if gas_ppm >= 800: return "HIGH"
            if gas_ppm >= 600: return "MEDIUM"

        return "LOW"

