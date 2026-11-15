# Priority-Based Data Storage & Telemetry System
### 5th Semester PBL Capstone — Operating Systems & Database Management Systems

A microservice-oriented IoT telemetry and priority-based data storage platform built with a high-performance **C++ Core Engine**, **NoSQL JSON Document Storage** on the local host, **Real-Time Server-Sent Events (SSE)**, **Cloud-Style IAM / RBAC**, and an **AWS Management Console UI**.

---

## 1. System Architecture & Engineering Highlights

```
+-----------------------------------------------------------------------------------+
|                              HARDWARE / SENSOR LAYER                              |
|  1. Physical Arduino UNO / Serial Devices via COM Ports                           |
|  2. Sandboxed Virtual Microcontroller Simulator (in-memory, safe hardware mock)   |
|     Packet Protocol: {module_id, val1, val2, ...} or JSON                         |
+-----------------------------------------+-----------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
|               HIGH-PERFORMANCE C++ MICROSERVICE BACKEND (cpp_backend)             |
|                                                                                   |
|  - Win32 OS Concurrency Primitives:                                               |
|    * CRITICAL_SECTION & CONDITION_VARIABLE for zero-busy-wait synchronization     |
|    * Native OS Threads (CreateThread, WaitForSingleObject)                        |
|                                                                                   |
|  - OS Priority Queue (std::priority_queue):                                       |
|    * Min-heap ranking: HIGH (0) > MEDIUM (1) > LOW (2)                            |
|    * Monotonic sequence-ID tie-breaking guarantees strict FIFO within priority    |
|                                                                                   |
|  - Dynamic Autoscaling Worker Pool:                                               |
|    * Scales workers up on heavy queue depth (>= 10)                               |
|    * Retires workers gracefully with retirement tokens when queue is idle (<= 2)  |
|                                                                                   |
|  - Dynamic Priority Classifier:                                                   |
|    * Evaluates sensor metrics against configurable threshold rules                |
|                                                                                   |
|  - NoSQL JSON Document Store:                                                     |
|    * Thread-safe, direct persistence to ./data_store/storage.json on host         |
|    * Full document CRUD: Single, Batch, and Whole Purge                           |
|                                                                                   |
|  - High-Speed TCP Microservice Socket Server (Winsock2 ws2_32, port 8085)         |
+-----------------------------------------+-----------------------------------------+
                                          | Local TCP Socket (Port 8085)
                                          v
+-----------------------------------------------------------------------------------+
|                     DECOUPLED SSE & SECURITY GATEWAY (Flask)                      |
|                                                                                   |
|  - Cloud-Style Identity & Access Management (IAM) & RBAC:                         |
|    * Roles: ADMIN, OPERATOR, AUDITOR                                              |
|    * Granular action policies & HMAC-SHA256 JWT Authentication                    |
|                                                                                   |
|  - Real-Time Server-Sent Events (/api/events/stream):                             |
|    * Unidirectional, auto-reconnecting HTTP live streaming of telemetry events    |
|                                                                                   |
|  - Pluggable Storage Adapter (Repository Pattern):                                |
|    * Loosely coupled via .env (STORAGE_ENGINE=cpp | json | mysql)                 |
+-----------------------------------------+-----------------------------------------+
                                          | Real-Time SSE Stream + REST
                                          v
+-----------------------------------------------------------------------------------+
|               PRESENTATION LAYER: AWS-STYLE MANAGEMENT CONSOLE                    |
|                                                                                   |
|  - Dashboard: Live queue gauges, active worker meters, priority counters          |
|  - Records Explorer: Single, Batch (multi-select), and Whole (purge) CRUD         |
|  - IAM Console: User lifecycle management, role assignments, audit policies       |
|  - Dynamic Priority Rules: Threshold manager for gas, motion, and temperatures    |
|  - Hardware Manager: Physical COM port connector + Virtual Sandbox simulator      |
|  - Live Event Feed: Terminal-style real-time log viewer                           |
+-----------------------------------------------------------------------------------+
```

---

## 2. Academic Evaluation: OS & DBMS Core Concepts

### Operating Systems (OS) Primitives
- **Bare-Metal Concurrency**: Native Windows OS threads created with `CreateThread` without interpreter locks.
- **Mutual Exclusion & Condition Variables**: High-performance `CRITICAL_SECTION` mutexes and `CONDITION_VARIABLE` signaling preventing busy-waiting and race conditions.
- **Priority Scheduling with FIFO Tie-Breaking**: Implements a min-heap priority scheduler. Records are tagged with `(priority_rank, sequence_id, payload)`, ensuring that high-urgency alarms jump the queue while items of identical priority are serviced in strict FIFO order (preventing starvation).
- **Dynamic Resource Autoscaling**: Worker thread pool automatically expands when queue pressure exceeds the scale-up threshold and contracts gracefully using retirement tokens when traffic normalizes.
- **Sandboxed Hardware Emulation**: In-memory virtual microcontroller simulator protects the host machine's hardware ports while providing continuous sensor telemetry.

### Database Management Systems (DBMS) Primitives
- **NoSQL JSON Document Modeling**: Unstructured/semi-structured sensor payloads are modeled as JSON documents with secondary indexing and metadata attributes.
- **ACID State Transitions**: Documents transition deterministically through a state machine: `PENDING` $\rightarrow$ `QUEUED` $\rightarrow$ `PROCESSING` $\rightarrow$ `COMPLETED` / `RETRY_WAIT` / `FAILED`.
- **Single, Batch, and Whole CRUD**:
  - *Single*: Query, inspect, modify priority, or delete individual records.
  - *Batch*: Multi-select arbitrary records for batch deletion, bulk priority reassignment, or batch requeueing.
  - *Whole Collection*: Purge all records, purge by status, or export entire collection to JSON.
- **Loose Coupling via Repository Pattern**: Storage engine is completely decoupled. By changing one line in `.env` (`STORAGE_ENGINE=cpp`, `STORAGE_ENGINE=json`, or `STORAGE_ENGINE=mysql`), the storage layer can be swapped without touching scheduling or ingestion logic.

---

## 3. Directory Structure

```
priority_based_data_storage/
├── .env                        # Central environment configuration (Storage, Ports, JWT, Workers)
├── .env.example                # Template configuration file
├── app.py                      # Flask SSE Gateway & REST API server
├── requirements.txt            # Python dependencies
│
├── cpp_backend/                # Native C++ Priority Telemetry Engine
│   ├── bin/
│   │   └── priority_engine.exe # Compiled native Windows binary (C++14 / Winsock2)
│   ├── include/
│   │   └── nlohmann/json.hpp   # Modern C++ JSON library
│   └── src/
│       ├── main.cpp            # Entry point for C++ engine daemon
│       ├── os_priority_queue.hpp   # Win32 synchronized min-heap priority queue
│       ├── os_worker_pool.hpp  # Dynamic autoscaling multithreaded worker pool
│       ├── json_document_store.hpp # Local JSON document storage engine
│       ├── priority_classifier.hpp # Dynamic sensor threshold classifier
│       └── server_socket.hpp   # High-throughput TCP JSON microservice server
│
├── backend/                    # Python Gateway & Business Logic
│   ├── config.py               # Centralized configuration loader
│   ├── auth/
│   │   ├── iam.py              # RBAC matrix, Permissions, JWT generation/verification
│   │   └── user_manager.py     # User credentials hashing & user storage
│   ├── storage/
│   │   ├── base.py             # Abstract BaseStorage interface (loose coupling)
│   │   ├── cpp_engine_client.py# TCP microservice client connecting to C++ engine
│   │   ├── json_storage.py     # Pure Python NoSQL document storage implementation
│   │   ├── mysql_storage.py    # MySQL relational fallback implementation
│   │   └── factory.py          # StorageFactory resolving engine from .env
│   ├── ingestion/
│   │   ├── parser.py           # Telemetry packet parser ({101,28.4,...} & JSON)
│   │   └── classifier.py       # Dynamic rule-based priority classifier
│   ├── scheduler/              # Python concurrency equivalents for fallback
│   └── serial/
│       ├── manager.py          # Physical COM port manager with non-blocking lifecycle
│       └── virtual_generator.py# Sandboxed virtual microcontroller simulator
│
├── static/
│   ├── css/
│   │   └── aws_style.css       # AWS Management Console dark theme stylesheet
│   └── js/
│       ├── sse_client.js       # Auto-reconnecting real-time Server-Sent Events client
│       └── app.js              # AWS Console Single-Page Application (SPA)
│
├── templates/
│   └── index.html              # AWS-style Single-Page Dashboard layout
│
├── data_store/                 # Local host document storage directory
│   ├── storage.json            # JSON document database (records, rules, attempts)
│   └── users.json              # IAM user accounts database
│
├── arduino/
│   └── data_generator/
│       └── data_generator.ino  # Arduino sketch for physical microcontroller input
│
└── tests/
    ├── test_system.py          # Unit & integration tests for IAM, NoSQL, Queue, Rules
    └── test_cpp_engine.py      # Integration test verifying the compiled C++ Engine binary
```

---

## 4. Quick Start & Execution Guide (Windows)

### Step 1: Activate Virtual Environment
Open PowerShell in the project directory:

```powershell
Set-Location "E:\5th sem\PBL\priority_based_data_storage\priority_based_data_storage"
.\venv\Scripts\Activate.ps1
```

### Step 2: (Optional) Recompile the C++ Engine
The C++ binary `cpp_backend/bin/priority_engine.exe` is already pre-compiled. If you make changes to the C++ code, recompile with:

```powershell
g++ -std=c++14 -O2 cpp_backend/src/main.cpp -lws2_32 -o cpp_backend/bin/priority_engine.exe
```

### Step 3: Run the Automated Verification Suite
Run the full test suite covering IAM, NoSQL CRUD, Priority Queueing, and C++ microservice socket communication:

```powershell
python -m unittest discover tests
```

Expected output:
```text
Ran 10 tests in 4.610s
OK
```

### Step 4: Launch the Application
Start the server:

```powershell
python app.py
```

The gateway automatically detects the configured `STORAGE_ENGINE=cpp` and launches the C++ Priority Engine microservice in the background!

### Step 5: Open the AWS Console
Open your browser at:
```
http://127.0.0.1:5000/
```

- **Pre-seeded Accounts**:
  - `admin` (Password: `Admin@123`) — Full CRUD access across records, users, rules, and hardware.
  - `operator` (Password: `Operator@123`) — Operational access for telemetry ingestion and batch actions.
  - `auditor` (Password: `Auditor@123`) — Read-only auditing access.

---

## 5. Using the Console Features

1. **Dashboard**: View real-time active worker counts, queue depth, and priority breakdown. Click **⚡ Ingest Burst (5)** to send simulated packets through the C++ engine.
2. **Records Explorer (Single, Batch, Whole CRUD)**:
   - Check arbitrary row checkboxes $\rightarrow$ Click **Batch Delete**, **Set High**, or **Batch Requeue**.
   - Click **Purge Completed** or **Purge Whole Database** to clean up records.
   - Click **⬇ Export JSON** to download the database directly from disk.
3. **IAM & RBAC**: Click **+ Create IAM User** to provision new accounts with assigned roles. Revoke accounts with a single click.
4. **Priority Rules**: Configure dynamic thresholds (e.g. Module 101 Gas PPM $\ge 750 \rightarrow$ `HIGH`).
5. **Hardware & Ports**: Connect to physical COM ports or toggle the **Sandboxed Virtual Microcontroller Simulator** to stream continuous telemetry safely.
6. **Live Event Stream**: Watch real-time Server-Sent Events (SSE) streaming as the C++ worker pool processes jobs.
