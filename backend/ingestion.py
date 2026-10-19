import re

MESSAGE_RE = re.compile(r"^\s*\{\s*(.*?)\s*\}\s*$")

def parse_message(line, source_port):
    match = MESSAGE_RE.match(line)
    if not match:
        raise ValueError("Expected a message in brace/comma format, e.g. {101,28.4,64.9,554,28.5}")
    parts = [part.strip() for part in match.group(1).split(",")]
    if len(parts) < 2:
        raise ValueError("Message must contain a module ID and at least one reading")
    try:
        module_id = int(parts[0])
        values = [float(v) for v in parts[1:]]
    except ValueError as exc:
        raise ValueError("Module ID must be an integer and all readings must be numeric") from exc
    if module_id < 1:
        raise ValueError("Module ID must be positive")
    if any(not (-1e12 < v < 1e12) for v in values):
        raise ValueError("A reading is outside the supported numeric range")
    return {"module_id": module_id, "values": values, "raw_message": line.strip(), "source_port": source_port}

def classify_priority(module_id, values):
    # Demonstration rules only. Replace with configurable, project-approved rules.
    # Example: module 101's third reading is treated as a simulated gas value.
    if module_id == 101 and len(values) >= 3 and values[2] >= 800:
        return "HIGH"
    if module_id == 101 and len(values) >= 3 and values[2] >= 600:
        return "MEDIUM"
    return "LOW"
