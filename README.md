# LabNet – Multi-Protocol Research Network

A real, low-level socket-based private computer networking platform built for university research laboratories, providing real-time multithreaded communication, high-throughput verified file sharing, reliable UDP transport, direct peer-to-peer sharing, node heartbeat discovery, and network diagnostics across Ubuntu virtual machines in VMware.

---

## 1. System Architecture

Unlike web mockups or simulated dashboards, LabNet operates over **genuine BSD networking sockets** (`socket.AF_INET`, `socket.SOCK_STREAM`, and `socket.SOCK_DGRAM`) running on the Linux kernel. A clean, university-styled Flask web interface serves as a visualization and control portal, bridging browser WebSockets (`Flask-SocketIO`) to low-level Python TCP/UDP socket servers, with full persistence and authentication backed by **Supabase PostgreSQL**.

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

## 2. Network Topology & Ports

- **Subnet:** `192.168.66.0/24` (VMware Virtual LAN)
- **VM1 (LabNet Server):** `192.168.66.128`
- **VM2 (Workstation):** `192.168.66.130`
- **VM3 (Peer Node):** `192.168.66.131`

| Port | Transport | Component | Purpose |
| :--- | :--- | :--- | :--- |
| **5001** | **TCP** | `server/tcp_chat_server.py` | Multithreaded broadcast chat server; newline-delimited JSON framing |
| **2121** | **TCP** | `server/tcp_file_server.py` | Dedicated streaming file transfer server; 64 KB chunking, SHA-256 verification |
| **2122** | **UDP** | `server/udp_file_server.py` | Reliable Sliding-Window ARQ UDP file server with timeout retransmissions |
| **2123** | **TCP** | `clients/p2p_node.py` | Direct Peer-to-Peer workstation listener; bypasses central server payload routing |
| **6000** | **UDP** | `server/udp_server.py` | Datagram echo, node presence, transit latency tracking, and offline timeout detector |
| **1025** | **TCP** | `server/mail_test_server.py` | Local RFC 5321 SMTP email notification receiver |
| **1100** | **TCP** | `server/mail_test_server.py` | Local RFC 1939 POP3 email retrieval server |
| **5050** | **TCP / HTTP** | `web/app.py` | Flask Web Portal & Socket.IO bridge |
| **53** | **UDP** | `server/dns_service.py` | DNS client resolution service benchmarking query duration against upstream resolver |

---

## 3. Directory Structure

```
labnet-network/
├── clients/
│   ├── tcp_client.py           # Persistent TCP client (:5000)
│   ├── tcp_chat_client.py      # Terminal CLI broadcast chat client (:5001)
│   ├── tcp_file_client.py      # Terminal CLI streaming file transfer client (:2121)
│   ├── udp_client.py           # UDP echo client (:6000)
│   ├── heartbeat_client.py     # Periodic UDP heartbeat sender (:6000)
│   ├── udp_file_client.py      # Reliable Sliding-Window ARQ UDP file client (:2122)
│   └── p2p_node.py             # Direct Peer-to-Peer listening and sender node (:2123)
├── server/
│   ├── tcp_server.py           # Persistent TCP echo server (:5000)
│   ├── tcp_chat_server.py      # Multithreaded TCP broadcast chat server (:5001)
│   ├── tcp_file_server.py      # Chunked TCP streaming file server (:2121)
│   ├── udp_server.py           # UDP echo and heartbeat monitor (:6000)
│   ├── udp_file_server.py      # Reliable Sliding-Window ARQ UDP file server (:2122)
│   ├── dns_service.py          # Genuine DNS query resolver client demo
│   ├── smtp_service.py         # RFC 5321 SMTP email client service
│   ├── pop3_service.py         # RFC 1939 POP3 email retrieval client service
│   └── mail_test_server.py     # Local background SMTP (:1025) and POP3 (:1100) servers
├── web/
│   ├── app.py                  # Flask application & Flask-SocketIO gateway (:5050)
│   ├── database/               # Supabase database access layer
│   │   ├── supabase_client.py  # Singleton Supabase Admin/Anon client wrapper
│   │   └── repositories/       # Profile, Device, Chat, File, Heartbeat, Session, Event repos
│   ├── services/
│   │   ├── tcp_service.py      # Persistent TCP Chat Bridge & TCP File Stream client
│   │   ├── udp_service.py      # UDP client service routines
│   │   ├── udp_file_service.py # Reliable UDP File Transfer bridge with Socket.IO progress
│   │   ├── auth_service.py     # Supabase Auth registration, login, logout, protected routes
│   │   └── monitoring_service.py # Port checking, peer registry, safe diagnostics
│   ├── templates/              # Clean university portal HTML templates
│   │   ├── base.html
│   │   ├── index.html
│   │   ├── chat.html
│   │   ├── files.html
│   │   ├── network.html
│   │   └── login.html
│   └── static/
│       ├── css/style.css       # Academic styling (white/slate, clean typography)
│       └── js/                 # Client scripts (chat.js, files.js, network.js, app.js, socket.io.min.js)
├── test/
│   ├── test_phase1_phase2.py   # Automated integration test suite for TCP chat & TCP file
│   └── test_phase3_phase4_phase5.py # Automated integration test suite for UDP, P2P, Services, Auth
├── docs/
│   ├── ARCHITECTURE.md         # Detailed wire protocol specifications & concurrency model
│   ├── DATABASE_SCHEMA_REFERENCE.md # Live verified Supabase schema & constraints reference
│   ├── NETWORK_PROTOCOLS.md    # In-depth packet layouts, ARQ sliding-window & P2P specs
│   └── TESTING_GUIDE.md        # Step-by-step testing and tcpdump packet analysis guide
├── uploads/                    # Server storage directory for received files
├── requirements.txt            # Python dependencies
└── README.md
```

---

## 4. Installation & Setup

### Prerequisites
- Ubuntu Linux 20.04 or 22.04 LTS
- Python 3.10+
- `virtualenv` / `python3-venv`

### Setup Environment
```bash
git clone https://github.com/Divya-Asnani/labnet-network.git
cd labnet-network

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## 5. Running the System

### 5.1 Automated Integration Tests
Verify all phases end-to-end:
```bash
# Test Phase 1 & 2 (TCP Chat & TCP File Stream)
python test/test_phase1_phase2.py

# Test Phase 3, 4 & 5 (UDP Heartbeat, UDP File ARQ, Direct P2P, Services, Supabase)
python test/test_phase3_phase4_phase5.py
```

### 5.2 Running Standalone Services
Open separate terminal tabs on VM1 (`192.168.66.128`):

```bash
# Terminal 1: TCP Broadcast Chat Server
python server/tcp_chat_server.py

# Terminal 2: TCP File Transfer Server
python server/tcp_file_server.py

# Terminal 3: Reliable UDP File Transfer Server
python server/udp_file_server.py

# Terminal 4: UDP Heartbeat & Presence Server
python server/udp_server.py

# Terminal 5: LabNet Web Portal (:5050)
python web/app.py
```

Access the web portal at: `http://localhost:5050` or `http://192.168.66.128:5050`.

### 5.3 Running CLI Clients on Workstation Nodes (VM2 / VM3)
```bash
# 1. Terminal Chat Client
python clients/tcp_chat_client.py --server 192.168.66.128 --user "Student"

# 2. Reliable UDP File Transfer Client (with 20% simulated packet loss test)
python clients/udp_file_client.py sample.pdf --server 192.168.66.128 --loss-rate 0.20

# 3. Direct Peer-to-Peer Transfer (VM2 -> VM3)
# On VM3:
python clients/p2p_node.py --listen --port 2123 --dir p2p_received
# On VM2:
python clients/p2p_node.py --send sample.pdf --target-ip 192.168.66.131 --target-port 2123

# 4. Heartbeat Client
python clients/heartbeat_client.py --server 192.168.66.128 --simulate-missed 3
```

---

## 6. Packet Inspection with tcpdump

Inspect real BSD socket traffic on the wire:

```bash
# Capture raw TCP Chat packets (:5001)
sudo tcpdump -i any port 5001 -nn -v -X

# Capture raw TCP File Transfer segments (:2121)
sudo tcpdump -i any port 2121 -nn -v

# Capture Reliable UDP File Datagrams (:2122)
sudo tcpdump -i any port 2122 -nn -v

# Capture Direct P2P Stream Packets (:2123)
sudo tcpdump -i any port 2123 -nn -v

# Capture Periodic UDP Heartbeats (:6000)
sudo tcpdump -i any port 6000 -nn -v -X
```

For complete protocol details, refer to [docs/NETWORK_PROTOCOLS.md](docs/NETWORK_PROTOCOLS.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), and [docs/DATABASE_SCHEMA_REFERENCE.md](docs/DATABASE_SCHEMA_REFERENCE.md).
