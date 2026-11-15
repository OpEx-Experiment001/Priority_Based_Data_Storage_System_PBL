Ran test C++ compiler and standard library · Exited with code 1
Created test_cpp.cpp
Ran compile and run basic C++ test
Edited test_cpp.cpp
Ran test std::thread in MinGW
Edited test_cpp.cpp
Ran compile and run Win32 OS C++ thread test
Edited test_cpp.cpp
Ran compile and run Win32 OS thread test with NT6 define
Ran clean up test files
Created test_winsock.cpp
Ran test Winsock2 compilation
Ran clean up test files
Ran download nlohmann json single-header · Exited with code 1
Ran download nlohmann json header via curl
Created test_json.cpp
Ran compile and test nlohmann json
Ran clean test files
Ran setup C++ directory structure
Created os_priority_queue.hpp
Created json_document_store.hpp
Created priority_classifier.hpp
Created os_worker_pool.hpp
Created server_socket.hpp
Created main.cpp
Ran compile C++ Priority Telemetry Engine
Ran verify compiled C++ binary
Created cpp_engine_client.py
Edited factory.py
Edited .env
Ran list contents of backend directory
Ran move legacy backend files to legacy folder
Ran ensure __init__.py files in all backend packages
Ran run automated test suite · Exited with code 1
Analyzed json_storage.py:L1-60
Edited json_storage.py
Edited user_manager.py
Edited test_system.py
Ran run automated test suite again
Created test_cpp_engine.py
Ran run C++ engine integration test · Exited with code 1
Analyzed server_socket.hpp:L60-100
Analyzed server_socket.hpp:L120-170
Edited server_socket.hpp
Edited server_socket.hpp
Edited server_socket.hpp
Edited server_socket.hpp
Ran recompile C++ Priority Engine
Analyzed test_cpp_engine.py:L30-60
Edited test_cpp_engine.py
Ran run C++ engine integration test
Ran run all test suites · Exited with code 1
Edited test_cpp_engine.py
Edited cpp_engine_client.py
Ran run all test suites
Edited README.md
Ran check git status
Analyzed .gitignore:L1-55
Edited .gitignore
Ran verify all test suites pass

# Rebuilt Priority-Based Data Storage & Telemetry System

The entire application has been redesigned and rebuilt from scratch into a **microservice architecture** featuring a **native C++ priority engine**, a **local NoSQL JSON document store**, **real-time WebSockets**, **cloud-style IAM & RBAC**, and an **AWS Management Console UI**.

---

## 1. What Has Been Built

### 1.1 Native C++ High-Performance Backend Engine (`cpp_backend/`)
- **Direct OS Multithreading & Synchronization**: Uses native Win32 kernel primitives (`CRITICAL_SECTION`, `CONDITION_VARIABLE`, `CreateThread`, `WaitForSingleObject`) rather than high-level scripting abstractions, providing true multi-core parallel execution.
- **OS Priority Queue (`OSPriorityQueue`)**: Implemented via a thread-safe min-heap (`std::priority_queue`) with monotonic sequence-ID tie-breaking. Guarantees that high-urgency alarms jump ahead of routine telemetry while preserving strict FIFO ordering within the same priority tier.
- **Dynamic Autoscaling Worker Pool (`OSWorkerPool`)**: Monitors queue pressure in real time. It spawns additional native worker threads when the queue exceeds the scale-up threshold ($\ge 10$) and retires idle workers gracefully using retirement tokens when traffic subsides ($\le 2$).
- **Dynamic Priority Classifier (`PriorityClassifier`)**: Evaluates incoming telemetry against customizable threshold rules dynamically without hardcoding.
- **High-Throughput TCP Microservice Server (`ServerSocket`)**: A high-performance Winsock2 server (`ws2_32`) listening on port `8085` that handles telemetry ingestion, CRUD commands, and real-time event broadcasting.
- **Compiled Binary**: Pre-compiled as a native Windows executable at [cpp_backend/bin/priority_engine.exe](file:///e:/5th%20sem/PBL/priority_based_data_storage/priority_based_data_storage/cpp_backend/bin/priority_engine.exe).

---

### 1.2 Local NoSQL JSON Document Storage (`data_store/storage.json`)
- **Host-Machine Persistence**: Telemetry records, worker attempt audit logs, and priority rules are stored as human-readable JSON documents in [data_store/storage.json](file:///e:/5th%20sem/PBL/priority_based_data_storage/priority_based_data_storage/data_store/storage.json) on this host machine.
- **No External Database Server Required**: Runs entirely self-contained without needing a separate MySQL service running, eliminating external setup overhead.
- **State Machine Transitions**: Every record transitions through a deterministic state machine: `PENDING` $\rightarrow$ `QUEUED` $\rightarrow$ `PROCESSING` $\rightarrow$ `COMPLETED` / `RETRY_WAIT` / `FAILED`.

---

### 1.3 Loosely Coupled Architecture via `.env` (Repository Pattern)
All service connections and engine selections are centralized in [.env](file:///e:/5th%20sem/PBL/priority_based_data_storage/priority_based_data_storage/.env):
```ini
# Storage Engine selection: 'cpp' (C++ Core), 'json' (Python Document Store), or 'mysql'
STORAGE_ENGINE=cpp
CPP_ENGINE_PORT=8085
JSON_STORAGE_PATH=./data_store/storage.json

# Security & IAM
JWT_SECRET=priority_storage_local_jwt_secret_9921_safe
ADMIN_DEFAULT_PASSWORD=Admin@123

# Concurrency & Autoscaling
MIN_WORKERS=2
MAX_WORKERS=6
SCALE_UP_QUEUE_DEPTH=10
SCALE_DOWN_QUEUE_DEPTH=2
```
To switch to a different database (e.g., MySQL or a cloud database in the future), change `STORAGE_ENGINE` in `.env`—the priority scheduler, worker threads, and API routes remain completely untouched.

---

### 1.4 Cloud-Style IAM & RBAC Engine (`backend/auth/`)
- **Roles**:
  - `ADMIN`: Full CRUD privileges (single, batch, whole purge for records, users, and rules).
  - `OPERATOR`: Operational privileges (telemetry ingestion, batch actions, port management).
  - `AUDITOR`: Read-only inspection privileges.
- **JWT Authentication**: Cryptographically signed HMAC-SHA256 tokens issued on login, validated across all REST endpoints and WebSocket frames.
- **Pre-Seeded Accounts**:
  - `admin` (Password: `Admin@123`)
  - `operator` (Password: `Operator@123`)
  - `auditor` (Password: `Auditor@123`)

---

### 1.5 AWS Management Console UI (`static/` & `templates/index.html`)
- **AWS Dark-Themed Console Layout**: Navigation bar, live WebSocket connection status dot (`LIVE WS CONNECTED`), role badge, and sidebar navigation.
- **Full CRUD Capabilities**:
  - **Single CRUD**: View details, modify priority, or delete individual records.
  - **Batch CRUD**: Multi-select checkboxes on table rows $\rightarrow$ **Batch Delete**, **Batch Set High/Low**, and **Batch Requeue**.
  - **Whole / Bulk CRUD**: **Purge Completed**, **Purge Entire Database**, or **Export JSON**.
- **IAM Console**: User creation modal, role assignment dropdown, and single/batch user revocation.
- **Priority Rules Manager**: UI for creating dynamic threshold rules (e.g. Module 101 Gas $\ge 750 \rightarrow$ `HIGH`).
- **Sandboxed Virtual Hardware Simulator**: In-memory simulator that generates realistic sensor packets without accessing physical COM hardware, keeping the host machine's hardware ports completely safe.

---

## 2. Project Structure Overview

```
priority_based_data_storage/
├── .env                        # Loose-coupling configuration (.env driven)
├── app.py                      # Flask WebSocket Gateway & REST server
├── requirements.txt            # Python dependencies
│
├── cpp_backend/                # Native C++ Backend Microservice
│   ├── bin/priority_engine.exe # Pre-compiled native C++ engine
│   ├── include/nlohmann/       # Single-header modern C++ JSON
│   └── src/
│       ├── main.cpp            # C++ Engine daemon entry point
│       ├── os_priority_queue.hpp # Win32 min-heap priority queue
│       ├── os_worker_pool.hpp  # Dynamic autoscaling worker thread pool
│       ├── json_document_store.hpp # Host JSON document storage engine
│       ├── priority_classifier.hpp # Dynamic sensor threshold classifier
│       └── server_socket.hpp   # High-throughput TCP JSON server
│
├── backend/
│   ├── auth/
│   │   ├── iam.py              # RBAC matrix, Permissions, JWT auth
│   │   └── user_manager.py     # Password hashing & user CRUD
│   ├── storage/
│   │   ├── base.py             # Abstract BaseStorage interface
│   │   ├── cpp_engine_client.py# TCP client connecting to C++ engine
│   │   ├── json_storage.py     # Python NoSQL storage implementation
│   │   ├── mysql_storage.py    # MySQL fallback implementation
│   │   └── factory.py          # StorageFactory resolving engine from .env
│   ├── ingestion/
│   │   ├── parser.py           # Telemetry packet parser ({101,28.4,...} & JSON)
│   │   └── classifier.py       # Dynamic rule-based priority classifier
│   └── serial/
│       ├── manager.py          # Physical COM port manager (non-blocking)
│       └── virtual_generator.py# Sandboxed virtual microcontroller simulator
│
├── static/
│   ├── css/aws_style.css       # AWS Management Console stylesheet
│   └── js/
│       ├── ws_client.js        # Auto-reconnecting WebSocket client
│       └── app.js              # AWS Console Single-Page App (SPA)
│
├── templates/index.html        # AWS Console single-page template
├── data_store/                 # Local host document storage
│   ├── storage.json            # JSON document database
│   └── users.json              # IAM accounts database
│
└── tests/
    ├── test_system.py          # Unit tests for IAM, NoSQL, Queue, Rules
    └── test_cpp_engine.py      # Integration test for the C++ binary
```

---

## 3. How to Run & Verify

### Step 1: Run the Automated Verification Suite
Open PowerShell in the project directory:
```powershell
.\venv\Scripts\Activate.ps1
python -m unittest discover tests
```
*Result: 10/10 tests pass across IAM, NoSQL CRUD, priority queueing, and the native C++ engine socket RPC.*

### Step 2: Start the Application
```powershell
python app.py
```
*(The gateway automatically launches the pre-compiled C++ Priority Engine on port `8085` and serves the web console at `http://127.0.0.1:5000`.)*

### Step 3: Access the Console
Open your browser at [http://127.0.0.1:5000/](http://127.0.0.1:5000/):
- Log in with `admin` / `Admin@123`.
- Click **⚡ Ingest Burst (5)** or toggle the **Virtual Microcontroller Simulator** to watch live priority classification, queue processing, and real-time WebSocket updates.

<!-- GOAL_COMPLETE -->