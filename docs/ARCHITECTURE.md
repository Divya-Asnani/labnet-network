# LabNet Architecture & Protocol Specification

## 1. Executive Overview

**LabNet** is a multi-protocol networking platform designed for university research laboratories. It provides real-time communication, reliable file transfer, heartbeat monitoring, and network visualization across virtual machines in a dedicated VMware virtual LAN (`192.168.66.0/24`).

Unlike simulated networking dashboards, LabNet operates over **genuine low-level BSD sockets** (`socket.AF_INET`, `socket.SOCK_STREAM` and `socket.SOCK_DGRAM`) running directly on the Linux kernel. A Flask application acts as a visualization and control layer, bridging web browser interfaces to raw TCP/UDP networking sockets via local WebSockets (`Flask-SocketIO`).

---

## 2. Network Topology & VM Roles

```
               VMware Virtual LAN: 192.168.66.0/24
=================================================================
             │                                   │
             │                                   │
    ┌─────────────────┐                 ┌─────────────────┐
    │   VM1: SERVER   │                 │   VM2: CLIENT   │
    │ 192.168.66.128  │                 │ 192.168.66.130  │
    └─────────────────┘                 └─────────────────┘
```

| Node | Default IP Address | Role / Services Hosted |
| :--- | :--- | :--- |
| **VM1 (Server)** | `192.168.66.128` | Hosts TCP Chat Server (:5001), TCP File Server (:2121), TCP Echo (:5000), UDP Server (:6000), Flask Portal (:5050) |
| **VM2 (Client)** | `192.168.66.130` | Workstation running CLI clients (`tcp_chat_client.py`, `tcp_file_client.py`), web browsers, and UDP heartbeats |

---

## 3. Port Allocation Directory

| Port | Transport Protocol | Service / Component | Specification |
| :--- | :--- | :--- | :--- |
| **5001** | **TCP** | `server/tcp_chat_server.py` | Multithreaded broadcast chat server; newline-delimited JSON wire protocol |
| **2121** | **TCP** | `server/tcp_file_server.py` | Dedicated streaming file transfer server; chunked (64 KB) transfer with SHA-256 verification |
| **5000** | **TCP** | `server/tcp_server.py` | General TCP echo / benchmark service; persistent multi-message support |
| **6000** | **UDP** | `server/udp_server.py` | Datagram echo service and periodic node heartbeat receiver |
| **5050** | **TCP / HTTP** | `web/app.py` | Flask Web Portal & Socket.IO gateway; bridges browsers to raw sockets |

---

## 4. End-to-End Architectural Flow

The web interface on any VM communicates with the local Flask process via WebSocket. The Flask process maintains persistent or on-demand BSD TCP sockets to the LabNet backend servers over the physical/virtual network:

```
[ Browser (VM1 / VM2) ]
        ▲
        │  WebSocket (Flask-SocketIO)
        ▼
[ Flask Gateway (web/app.py:5050) ]
        ▲
        │  Raw TCP Sockets (socket.AF_INET, SOCK_STREAM)
        ▼
[ LabNet Socket Servers ]
   ├── Port 5001: Multithreaded TCP Chat Server
   ├── Port 2121: Chunked TCP File Server
   ├── Port 5000: Persistent TCP Echo Server
   └── Port 6000: UDP Heartbeat & Echo Server
```

---

## 5. Protocol Wire Specifications

### 5.1. Real-Time TCP Chat Protocol (:5001)

TCP is a byte-stream protocol without inherent message boundaries. To prevent stream fragmentation and concatenation issues, LabNet Chat uses **newline-delimited JSON (`\n`)** framing.

#### Client to Server Messages:

1. **Join Handshake:**
```json
{
  "type": "join",
  "username": "Researcher",
  "device_ip": "192.168.66.130"
}\n
```

2. **Chat Message Broadcast:**
```json
{
  "type": "chat",
  "sender": "Researcher",
  "text": "Initiating synchronized data acquisition run.",
  "timestamp": "2026-11-17 22:30:00"
}\n
```

#### Server to Client Broadcasts:

1. **System Announcement:**
```json
{
  "type": "system",
  "sender": "System",
  "text": "Researcher connected from ('192.168.66.130', 49822). Online clients: 3",
  "timestamp": "2026-11-17 22:30:01"
}\n
```

2. **Broadcast Chat Message:**
```json
{
  "id": "7cc7350e-9260-48c9-88e3-87965dac73f4",
  "type": "chat",
  "sender": "Researcher",
  "device_ip": "192.168.66.130",
  "text": "Initiating synchronized data acquisition run.",
  "timestamp": "2026-11-17 22:30:00"
}\n
```

---

### 5.2. TCP File Transfer Protocol (:2121)

File transfer operates over a dedicated TCP stream with three distinct phases: **Metadata Handshake**, **Binary Chunk Streaming**, and **Cryptographic Integrity Verification**.

```
Client                                                  Server (:2121)
  │                                                           │
  │─── 1. Handshake JSON: {"filename", "filesize", "sha256"} ─▶│ (Parse & Validate)
  │                                                           │
  │◀── 2. Response JSON: {"status": "READY"} ─────────────────│
  │                                                           │
  │─── 3. Stream Raw Binary (64 KB Chunks) ──────────────────▶│ (Write & Compute SHA-256)
  │    [Chunk 1] ... [Chunk N]                                │
  │                                                           │
  │─── 4. End of Stream (client shutdown/close) ─────────────▶│ (Verify SHA-256)
  │                                                           │
  │◀── 5. Response JSON: {"status": "SUCCESS", "verified": true} ─│
```

#### 1. Client Metadata Handshake:
```json
{
  "filename": "spectral_analysis.pdf",
  "filesize": 2457600,
  "sha256": "4c123b97fc787527a4d65db6de432756d654bc96d526c7173ed4148585d6aaf0"
}\n
```

#### 2. Server Readiness ACK:
```json
{
  "status": "READY",
  "message": "Server ready to receive stream"
}\n
```

#### 3. Binary Stream:
The client streams the file data in **64 KB (65,536 bytes)** buffer chunks. As chunks arrive, the server:
- Writes the bytes directly to disk in `uploads/<filename>`.
- Increments the running byte counter.
- Updates an in-flight `hashlib.sha256()` accumulator without buffering the whole file in memory.

#### 4. Server Verification & Transfer Receipt:
Upon receiving exactly `filesize` bytes, the server computes the final SHA-256 hex digest, compares it to the client's handshake digest, and returns:
```json
{
  "status": "SUCCESS",
  "filename": "spectral_analysis.pdf",
  "filesize": 2457600,
  "sha256": "4c123b97fc787527a4d65db6de432756d654bc96d526c7173ed4148585d6aaf0",
  "verified": true,
  "speed_kbps": 32017.03,
  "save_path": "/home/divya123/Desktop/CNT_CP/labnet-network/uploads/spectral_analysis.pdf",
  "timestamp": "2026-11-17 22:30:30"
}\n
```

---

## 6. Concurrency & Thread Synchronization

1. **Per-Connection Worker Threads:**
   Both `tcp_chat_server.py` and `tcp_file_server.py` spawn dedicated `threading.Thread(target=..., daemon=True)` instances for each accepted socket connection (`conn, addr = server_sock.accept()`).
2. **Mutex-Guarded Client Registry:**
   In `tcp_chat_server.py`, active sockets are tracked in a dictionary `active_clients = {conn: client_info}` protected by a `threading.Lock()` mutex. Broadcast operations iterate safely over registered sockets. Broken sockets are pruned automatically upon `BrokenPipeError` or socket timeout.
3. **SO_REUSEADDR & SO_REUSEPORT:**
   All servers configure `SO_REUSEADDR` and `SO_REUSEPORT` socket options to prevent port-bind lockouts during rapid server restarts or `TIME_WAIT` socket states.
4. **Flask-SocketIO Bridge:**
   The Flask application runs a persistent background daemon thread (`TCPChatBridge`) that maintains an active TCP connection to port 5001. When a browser posts a message, Flask forwards it to port 5001; when port 5001 broadcasts a message, the bridge receives it and emits a WebSocket event `new_message` to all connected browsers.

---

## 7. Security & Input Sanitization

- **Path Traversal Protection:** The file server strips directory paths using `os.path.basename()` to prevent directory traversal attacks (e.g., `../../etc/passwd`).
- **Cryptographic Verification:** Every file is validated against its pre-computed SHA-256 checksum to guarantee that no bytes were corrupted, lost, or truncated during transit.
- **Resource Limits:** File size limits are enforced to prevent disk exhaustion.
