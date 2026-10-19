# Priority-Based Data Storage System

Starter application for the OS + DBMS project. It discovers serial ports, lets an operator select one or more ports, receives newline-delimited Arduino records, assigns backend priority, persists records to MySQL, and processes them using a priority queue and dynamically managed worker threads.

## Current scope

- Serial-port discovery using `pyserial`; works with Windows COM ports and serial devices on other operating systems.
- Web UI for selecting one or more available ports, connecting/disconnecting, viewing connection state, and refreshing the available-port list.
- Input format such as `{101,28.4,64.9,554,28.5}`.
- Backend-generated record priority (Arduino does not send priority).
- MySQL tables for modules, records, readings, process state, and attempt history.
- Priority queue, initial worker count of one, configurable worker scaling, and retry-state scaffolding.
- Demo record button to check the database/dashboard without Arduino hardware.

## Requirements

- Python 3.10+
- MySQL 8.x (or compatible MySQL server)
- Arduino UNO and USB data cable for hardware input
- Correct USB/serial driver for your board

## Windows setup (PowerShell)

1. Extract the ZIP and open PowerShell in the project directory.
2. Create and activate a virtual environment:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

   If PowerShell blocks activation, use a permitted execution-policy approach for your environment, or run `.\.venv\Scripts\python.exe` directly.

3. Install dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

4. Create the database and tables. For example, using the MySQL command-line client:

   ```powershell
   mysql -u root -p < database/schema.sql
   ```

   If `mysql` is not on PATH, run the schema file in MySQL Workbench instead.

5. Copy `.env.example` to `.env`, then set your actual database credentials and a unique `FLASK_SECRET_KEY`. Do not commit `.env` to version control.

6. Start the application:

   ```powershell
   python app.py
   ```

7. Open `http://127.0.0.1:5000` in your browser.

## Selecting serial ports

1. Connect the Arduino or other serial devices.
2. Close Arduino IDE Serial Monitor and any other application currently using the port.
3. Click **Refresh ports**.
4. Select one or more ports using the checkboxes.
5. Click **Connect selected ports**.
6. The app checks the ports again and attempts to open each selected port. A port may be listed but fail to open if another process owns it, the driver is missing, or the device was unplugged.
7. The application periodically scans ports and attempts to reconnect selected ports that become available again.
8. Click **Disconnect all** to stop reading from the selected ports.

The app does not silently connect to every discovered port: the operator explicitly selects which ports to open.

## Arduino message protocol

One record per line, terminated with a newline:

```text
{101,28.4,64.9,554,28.5}
{102,25.4,65.4,67.4,4.075}
```

The first field is the module ID. The remaining fields are numeric readings. The order and meaning of readings must be defined consistently for each module. See `arduino/data_generator.ino` for a sample generator.

The backend assigns priority using illustrative rules in `backend/ingestion.py`. These are demo rules only and must be replaced with the project-approved, configurable classification rules. In particular, the sample gas thresholds are not real-world safety thresholds.

## Worker scaling configuration

Settings live in `.env`:

- `MIN_WORKERS`: minimum workers (default 1)
- `MAX_WORKERS`: maximum worker threads (default 4)
- `SCALE_UP_QUEUE_DEPTH`: queue depth that triggers a scale-up decision
- `SCALE_DOWN_QUEUE_DEPTH`: queue depth at which scale-down may be considered
- `MAX_ATTEMPTS`: maximum total processing attempts (default 3)

This is an educational starter implementation, not a production-grade distributed queue. Before relying on it for important data, complete and test durable queue claiming, crash recovery, idempotency, retry persistence, authentication/RBAC/MFA, CSRF protection, and operational hardening. The initial UI is local and should not be exposed to the public internet.

## Troubleshooting

- **No ports shown:** reconnect the board, check Device Manager → Ports (COM & LPT), install the appropriate driver, and refresh.
- **Port listed but cannot connect:** close Serial Monitor or other applications using it; verify the port and baud rate.
- **Unreadable or rejected records:** match baud rate (default 9600), newline-delimited format, and numeric fields.
- **Database unavailable:** verify MySQL is running and `.env` credentials match the server.
- **No records:** use the demo button to test the database path, then check the Arduino serial output format.

## Architecture note

The chosen flow is: receive → validate → assign priority → persist in MySQL → enqueue for processing → process → persist outcome. MySQL is the durable source of truth; the in-memory priority queue is not durable by itself. The reconciliation mechanism must be validated before treating this as fault-tolerant.

---

# Detailed Execution Guide (Windows + PowerShell)

This section gives a complete local execution path for the current starter application. Run commands from the project root—the directory containing `app.py`, `requirements.txt`, `.env.example`, and the `database` folder.

> **Important:** The current application is a local educational prototype. It has a Flask interface, serial-port controls, a MySQL-backed record pipeline, and a demo-record endpoint. Do not expose it to the public internet. The serial manager's connect/disconnect lifecycle should be tested carefully; if the serial reader thread crashes, disconnect the port, stop the application, and investigate the traceback before relying on hardware ingestion.

## 1. Open the project directory

Open PowerShell in the extracted project folder. Example:

```powershell
Set-Location "E:\5th sem\PBL\priority_based_data_storage\priority_based_data_storage"
Get-ChildItem
```

Confirm that the output includes `app.py`, `requirements.txt`, `database`, `backend`, `arduino`, and `.env.example`.

## 2. Create and activate a Python virtual environment

Create the environment once:

```powershell
py -m venv venv
```

Activate it:

```powershell
.\venv\Scripts\Activate.ps1
```

The prompt should begin with `(venv)`. If PowerShell blocks activation due to execution policy, do not change the machine-wide policy just for this project. You can invoke the environment's Python directly instead:

```powershell
.\venv\Scripts\python.exe --version
```

If the project already has a working `venv` folder, reuse it rather than creating another environment with a different name.

## 3. Install Python dependencies

With the virtual environment active, run:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Check for dependency errors:

```powershell
python -m pip check
```

If installation fails, keep the full error output. Do not install random package versions to work around an error without checking the project's `requirements.txt` first.

## 4. Start and verify MySQL Server

The MySQL Windows service must be running before the Flask application starts. To inspect services:

```powershell
Get-Service *MySQL*
```

If your service is named `MySQL267`, for example:

```powershell
Get-Service MySQL267
```

If it is stopped, start it from an elevated PowerShell only if your Windows account requires administrator permission:

```powershell
Start-Service MySQL267
```

Use the actual service name shown by `Get-Service`; it can differ between installations.

### Find the MySQL command-line client

If `mysql --version` works, the client is on PATH. If PowerShell says `mysql` is not recognised, locate `mysql.exe`:

```powershell
Get-ChildItem "C:\Program Files\MySQL" -Filter mysql.exe -Recurse -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty FullName
```

For the installation used during the original setup, the path was:

```text
C:\Program Files\MySQL\MySQL Server 26.7\bin\mysql.exe
```

Your installation path may differ. In the commands below, substitute your actual path if it is different. You can define a PowerShell variable to avoid repeatedly typing the full path:

```powershell
$mysql = "C:\Program Files\MySQL\MySQL Server 26.7\bin\mysql.exe"
& $mysql --version
```

### Create the database and tables

The schema file creates the `priority_storage` database and its five core tables: `modules`, `data_records`, `readings`, `processes`, and `processing_attempts`.

**PowerShell note:** Do not use the Unix-style redirection command `mysql -u root -p < database/schema.sql` in PowerShell. Use a pipeline instead:

```powershell
Get-Content .\database\schema.sql -Raw | & $mysql -u root -p
```

Enter the MySQL root password when prompted. Password characters may not appear as you type; this is normal. A successful schema run may return to the prompt without printing a success message.

If you prefer MySQL Workbench, open `database/schema.sql` as a SQL script, connect to the local server, and execute the script. Inspect the output panel for errors.

### Verify the schema

```powershell
& $mysql -u root -p -e "USE priority_storage; SHOW TABLES;"
```

Expected core tables:

```text
modules
data_records
processes
processing_attempts
readings
```

If a table is missing or the command reports an error, resolve that before launching Flask. The current schema does not define `users` or `audit_logs`; do not assume authentication or audit-table features exist in this application revision.

## 5. Configure the `.env` file

Create `.env` if it does not already exist:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
code .env
```

If the `code` command is unavailable, open `.env` manually in your editor. Set the values to match your MySQL installation. The file should contain the variable names shown in `.env.example`, for example:

```dotenv
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=root
DB_PASSWORD=PUT_YOUR_LOCAL_MYSQL_PASSWORD_HERE
DB_NAME=priority_storage
DB_POOL_SIZE=8

FLASK_SECRET_KEY=PUT_A_NEW_RANDOM_SECRET_HERE
FLASK_HOST=127.0.0.1
FLASK_PORT=5000

SERIAL_BAUD=9600

MIN_WORKERS=1
MAX_WORKERS=4
SCALE_UP_QUEUE_DEPTH=20
SCALE_DOWN_QUEUE_DEPTH=3
MAX_ATTEMPTS=3
SIMULATED_PROCESS_SECONDS=0.15
```

Replace the two placeholder values. Do not leave `DB_PASSWORD` as `replace-with-your-password`. The database username/password must be valid for the MySQL server, and `DB_NAME` must be `priority_storage` unless you deliberately changed the schema and application configuration together.

Generate a new Flask secret in PowerShell:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

Copy the generated value into `FLASK_SECRET_KEY`. Treat it as a password: do not publish it, commit `.env`, or paste it into chat. If a secret has been exposed, generate a new one and replace it.

Save `.env`. Confirm that `.env` is excluded from version control; never include real credentials in README examples.

## 6. Test the database connection before starting Flask

The project's database API is a `Database` class in `backend/db.py`; it does not expose a standalone `get_connection()` function. From the project root, run:

```powershell
python -c "from dotenv import load_dotenv; load_dotenv(); from backend.db import Database; db=Database(); db.ping(); print('MySQL connection successful')"
```

Expected output:

```text
MySQL connection successful
```

Creating `Database()` also runs the application's database initialisation logic. If the command fails, read the entire traceback. Common causes include an incorrect password, a stopped MySQL service, the wrong port, a missing dependency, or an incompatible database/server version. Do not continue until the connection test succeeds.

## 7. Start the Flask application

From the project root with the virtual environment active:

```powershell
python app.py
```

Keep this terminal open. If startup succeeds, Flask will print a local address. Open the following in your browser:

- Main interface: `http://127.0.0.1:5000/`
- Health check: `http://127.0.0.1:5000/api/health`
- Status summary: `http://127.0.0.1:5000/api/status`
- Available serial ports: `http://127.0.0.1:5000/api/ports`
- Recent records: `http://127.0.0.1:5000/api/records`

The health endpoint should return HTTP 200 with a response similar to `{"status":"ok","database":"connected"}`. The status and ports endpoints should return JSON. If the application uses a different port because `FLASK_PORT` was changed, use the port printed in the terminal.

## 8. Test the database and dashboard without Arduino hardware

Use the **demo record** control in the web interface, if displayed. It sends a sample record through the application's database and scheduler path without requiring a physical board.

The current demo endpoint accepts a JSON body containing a module ID and numeric readings. To test it from PowerShell while Flask is running, open a second PowerShell terminal in the project directory and run:

```powershell
$body = @{ module_id = 101; values = @(28.4, 64.9, 554, 28.5) } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:5000/api/demo-record" -ContentType "application/json" -Body $body
```

Then inspect recent records:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/records"
```

You can also open `http://127.0.0.1:5000/api/status` in the browser. Confirm that the new record appears and that its process status changes as the scheduler runs. If the demo request returns an error, inspect the Flask terminal for the traceback and check the MySQL connection first.

## 9. Upload the Arduino data-generator sketch

1. Open Arduino IDE.
2. Open `arduino/data_generator.ino` from the project directory.
3. Connect the Arduino UNO using a USB data cable that supports data transfer.
4. In Arduino IDE, select the correct board and COM port.
5. Upload the sketch and wait for the upload-success message.
6. The example sketch sends newline-terminated numeric records at the configured baud rate (normally `9600`).
7. Close Arduino IDE Serial Monitor before connecting the same COM port in the web application. Only one process can normally own a serial port at a time.

The protocol is one record per line. Example:

```text
{101,28.4,64.9,554,28.5}
```

The first value is the module ID; the remaining values are readings. The backend assigns priority. Do not append `HIGH`, `MEDIUM`, or `LOW` to the Arduino message unless the parser and protocol have deliberately been changed.

Reading positions must have a consistent meaning for each module. Document the field order and units before interpreting the data as real sensor measurements. The example values and priority thresholds are for demonstration only.

## 10. Connect a serial port through the web interface

1. Keep `python app.py` running.
2. Connect the Arduino and check its assigned COM port in Windows Device Manager under **Ports (COM & LPT)** if needed.
3. Close Arduino IDE Serial Monitor and any other application using that port.
4. Open `http://127.0.0.1:5000/` and click **Refresh ports**.
5. Select the correct COM port (for example, `COM3`). Do not select unrelated ports.
6. Click **Connect selected ports**.
7. Confirm that the port is shown as connected and that incoming records appear on the dashboard or `/api/records`.
8. To stop input, use **Disconnect all** in the interface.

A port appearing in the list only means that Windows reports the device; it does not guarantee that the port can be opened. Connection can fail if the device was unplugged, its driver is unavailable, or another application has the port open.

### If a serial reader thread crashes

If the Flask terminal shows an exception in a thread named similar to `serial-COM3`, especially an exception inside `conn.readline()` or PySerial's Windows read implementation:

1. Click **Disconnect all** if the interface is responsive.
2. Stop Flask with `Ctrl+C` in its terminal.
3. Close Arduino Serial Monitor and any other software using the COM port.
4. Unplug and reconnect the Arduino if necessary, then confirm the port in Device Manager.
5. Restart Flask and test the port again once.
6. If the exception repeats, save the complete traceback and inspect `backend/serial_manager.py`. Do not treat a successful HTTP response from `/api/ports/connect` as proof that the reader thread remained healthy.

A reader thread crashing during concurrent read/close is a lifecycle/concurrency issue that should be fixed and tested in the serial manager; repeatedly reconnecting is not a durable fix.

## 11. Understand the record lifecycle

The intended data flow is:

1. A serial reader receives a complete newline-terminated message.
2. The parser validates the message format and extracts the module ID and numeric values.
3. The backend assigns a priority using the configured/demo classifier.
4. The database stores the record and its individual readings and creates a process-state row.
5. The scheduler enqueues the record for processing.
6. A worker processes it and updates the process status/attempt history.
7. The dashboard/API exposes the stored record and status.

The MySQL database stores durable record data, but the in-memory priority queue itself is not durable. Crash recovery, atomic task claiming, duplicate-processing prevention, and retry behaviour require further validation before the application can be described as fault-tolerant.

## 12. Stop the application

1. Use **Disconnect all** in the web interface before stopping serial ingestion.
2. Return to the PowerShell terminal running Flask.
3. Press `Ctrl+C` once and wait for the process to stop.
4. If it does not stop, inspect the relevant Python process before terminating anything. Avoid killing unrelated Python applications.
5. MySQL can remain running as a Windows service; stop it only if you specifically need to shut down the database server.

## 13. Quick verification checklist

Before a demonstration, confirm each item:

- [ ] The MySQL Windows service is running.
- [ ] `database/schema.sql` has executed successfully.
- [ ] `SHOW TABLES` lists all five core tables.
- [ ] `.env` has the correct database settings and a unique Flask secret.
- [ ] The Python virtual environment is active and `pip check` reports no broken requirements.
- [ ] The database connection test prints `MySQL connection successful`.
- [ ] `python app.py` starts without a traceback.
- [ ] `/api/health` reports the database as connected.
- [ ] The demo-record request stores and displays a record.
- [ ] Arduino IDE Serial Monitor is closed before connecting the selected COM port.
- [ ] Serial ingestion works and the reader thread remains alive.
- [ ] Disconnecting and reconnecting a port has been tested safely.
- [ ] `.env` and other secrets are not included in a ZIP, public repository, or submission.

## 14. Quick troubleshooting reference

| Symptom | Checks / action |
|---|---|
| `mysql` is not recognised | Use the full path to `mysql.exe`, or add its `bin` directory to PATH. |
| Access denied for MySQL user | Verify `DB_USER` and `DB_PASSWORD` in `.env`; do not paste credentials into logs or chat. |
| Cannot connect to MySQL | Check the Windows service, `DB_HOST`, `DB_PORT`, and server error output. |
| `ImportError` for `get_connection` | The current API is `Database` and `Database.connection()`; use the connection-test command in Section 6. |
| `/api/health` returns 503 | Read its response and the Flask terminal traceback; verify database credentials and service state. |
| No serial ports are listed | Check Device Manager, USB cable, board driver, and refresh the port list. |
| Port cannot be opened | Close Serial Monitor/other applications and confirm the correct COM port. |
| Serial thread crashes during `readline()` | Stop ingestion and Flask, save the traceback, and investigate the serial-manager read/close lifecycle. |
| Demo record fails | Check the Flask terminal, MySQL status, and whether module/record/process inserts succeeded. |
| No record appears | Check the API response, selected COM port, baud rate, newline delimiter, and message format. |

## 15. Known limitations

This execution guide does not imply that the application is production-ready. The current code should still be evaluated for serial-thread lifecycle safety, durable scheduling and recovery, retry classification, duplicate-processing prevention, complete validation, authentication/authorization requirements, CSRF protection, database migrations, automated integration tests, backup/recovery, and performance. Run only on a trusted local machine until the relevant security controls have been implemented and tested.

