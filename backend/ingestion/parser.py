import json
import re
from typing import Any, Dict

BRACE_PATTERN = re.compile(r"^\s*\{\s*(.*?)\s*\}\s*$")

def parse_telemetry_message(line: str, source_port: str = "SERIAL") -> Dict[str, Any]:
    """
    Parses incoming telemetry from serial or network streams.
    Accepts both comma-separated brace strings like:
      {101,28.4,64.9,554,28.5}
    and standard JSON objects like:
      {"module_id": 101, "values": [28.4, 64.9, 554, 28.5]}
    """
    raw_str = line.strip()
    if not raw_str:
        raise ValueError("Empty telemetry packet received")

    # 1. Attempt JSON parsing
    if raw_str.startswith("{") and '"' in raw_str:
        try:
            data = json.loads(raw_str)
            if "module_id" in data and "values" in data:
                module_id = int(data["module_id"])
                values = [float(v) for v in data["values"]]
                if module_id <= 0:
                    raise ValueError("Module ID must be a positive integer")
                return {
                    "module_id": module_id,
                    "values": values,
                    "raw_message": raw_str,
                    "source_port": source_port
                }
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    # 2. Attempt Arduino brace-delimited format: {101,28.4,64.9,554,28.5}
    match = BRACE_PATTERN.match(raw_str)
    if not match:
        raise ValueError(f"Invalid packet syntax: expected brace format like '{{101,28.4,64.9,...}}' or JSON")

    tokens = [t.strip() for t in match.group(1).split(",")]
    if len(tokens) < 2:
        raise ValueError("Packet must contain a module ID followed by at least one numeric sensor reading")

    try:
        module_id = int(tokens[0])
        values = [float(t) for t in tokens[1:]]
    except ValueError as exc:
        raise ValueError("Module ID must be an integer and all sensor readings must be numeric") from exc

    if module_id <= 0:
        raise ValueError("Module ID must be positive")

    for v in values:
        if not (-1e9 < v < 1e9):
            raise ValueError(f"Sensor reading {v} is outside acceptable range (-1e9, 1e9)")

    return {
        "module_id": module_id,
        "values": values,
        "raw_message": raw_str,
        "source_port": source_port
    }

