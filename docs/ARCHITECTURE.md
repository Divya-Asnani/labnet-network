# LabNet Architecture & Multi-Protocol Specification

## 1. Executive Overview

**LabNet** is a multi-protocol networking platform designed for university research laboratories. It provides real-time communication, reliable file transfer, heartbeat monitoring, direct peer-to-peer sharing, and network services across virtual machines in a dedicated VMware virtual LAN (`192.168.66.0/24`).

Unlike simulated networking dashboards, LabNet operates over **genuine low-level BSD sockets** (`socket.AF_INET`, `socket.SOCK_STREAM` and `socket.SOCK_DGRAM`) running directly on the Linux kernel. A clean, university-styled Flask application acts as a visualization and control layer, bridging web browser interfaces to raw TCP/UDP networking sockets via local WebSockets (`Flask-SocketIO`), with full persistence and authentication backed by **Supabase PostgreSQL**.

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
             ▲                                   │ (Direct P2P: 2123)
             │ (Discovery & Metadata Only)       ▼
             │                          ┌─────────────────┐
             └──────────────────────────│   VM3: PEER     │
                                        │ 192.168.66.131  │
                                        └─────────────────┘
```

| Node | Default IP Address | Role / Services Hosted |
| :--- | :--- | :--- |
| **VM1 (Server)** | `192.168.66.128` | Hosts TCP Chat Server (:5001), TCP File Server (:2121), UDP File Server (:2122), UDP Heartbeat (:6000), SMTP (:1025), POP3 (:1100), Flask Portal (:5050), and Central Peer Coordinator |
| **VM2 (Workstation)** | `192.168.66.130` | Workstation running CLI clients (`tcp_chat_client.py`, `tcp_file_client.py`, `udp_file_client.py`, `p2p_node.py`), web browser, and UDP heartbeats |
| **VM3 (Peer Node)** | `192.168.66.131` | Secondary workstation running direct P2P receiver (`p2p_node.py:2123`) accepting file payloads directly from VM2 |

---

## 3. Comprehensive Port Allocation Directory

| Port | Transport Protocol | Service / Component | Specification |
| :--- | :--- | :--- | :--- |
| **5001** | **TCP** | `server/tcp_chat_server.py` | Multithreaded broadcast chat server; newline-delimited JSON wire protocol |
| **2121** | **TCP** | `server/tcp_file_server.py` | Dedicated streaming file transfer server; chunked (64 KB) transfer with SHA-256 verification |
| **2122** | **UDP** | `server/udp_file_server.py` | Reliable Sliding-Window ARQ UDP file server with timeout retransmissions |
| **2123** | **TCP** | `clients/p2p_node.py` | Direct Peer-to-Peer workstation listener; bypasses central server payload routing |
| **6000** | **UDP** | `server/udp_server.py` | Node heartbeat receiver, transit latency tracking, and offline timeout detector |
| **1025** | **TCP** | `server/mail_test_server.py` | Local RFC 5321 SMTP email notification receiver |
| **1100** | **TCP** | `server/mail_test_server.py` | Local RFC 1939 POP3 email retrieval server |
| **5050** | **TCP / HTTP** | `web/app.py` | Flask Web Portal & Socket.IO gateway; bridges browsers to low-level sockets |
| **53** | **UDP** | `server/dns_service.py` | DNS client resolution service benchmarking query duration against upstream resolver |

---

## 4. End-to-End Architectural Flow

```
[ Browser (VM1 / VM2) ]
        ▲
        │  WebSocket (Flask-SocketIO)
        ▼
[ Flask Gateway (web/app.py:5050) ]
        ▲
        │  Low-Level BSD Sockets (AF_INET)
        ▼
[ Core Network Services ]
   ├── Port 5001: Multithreaded TCP Chat Server
   ├── Port 2121: Chunked TCP File Server (Node -> Server)
   ├── Port 2122: Reliable Sliding-Window ARQ UDP File Server (Node -> Server)
   ├── Port 6000: UDP Node Heartbeat & Latency Monitor
   ├── Port 1025: RFC 5321 Local SMTP Server
   └── Port 1100: RFC 1939 Local POP3 Server

[ Direct P2P Mesh (Node -> Node) ]
   └── Port 2123: VM2 -> VM3 Direct BSD Socket Stream (Payload bypasses VM1)

[ Database & Auth Persistence ]
   └── Supabase PostgreSQL Layer (profiles, devices, chat_messages, file_transfers, heartbeat_logs, network_sessions, network_events)
```

---

## 5. Security, Validation & Storage Architecture

1. **Path Traversal Protection:**
   The server sanitizes all requested file names using `os.path.basename()` and verifies that resolved paths strictly reside within `UPLOAD_FOLDER`. Arbitrary server directory traversals (`../../etc/passwd`) are rejected with HTTP 404/403.
2. **Cryptographic Integrity (SHA-256):**
   Every transferred file (TCP, UDP, and P2P) is verified using in-flight `hashlib.sha256()` accumulation. Received bytes must match the client's handshake digest before being marked `completed` in the database.
3. **Session Authentication:**
   Supabase Auth controls user registration, login, and protected route access (`/chat`, `/files`, `/network`). Service role keys remain exclusively on the backend.
