# LabNet – Multi-Protocol Research Network

A real, low-level socket-based private computer networking platform built for university research laboratories, providing real-time multithreaded communication, high-throughput verified file sharing, node heartbeat discovery, and network monitoring across Ubuntu virtual machines in VMware.

---

## 1. System Architecture

Unlike web mockups or simulated dashboards, LabNet operates over **genuine BSD networking sockets** (`socket.AF_INET`, `socket.SOCK_STREAM`, and `socket.SOCK_DGRAM`) running on the Linux kernel. A clean, university-styled Flask web interface serves as a visualization and control portal, bridging browser WebSockets (`Flask-SocketIO`) to low-level Python TCP/UDP socket servers.

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
   ├── Port 2121: Chunked TCP File Server (SHA-256 Verified)
   ├── Port 5000: Persistent TCP Echo Server
   └── Port 6000: UDP Heartbeat & Echo Server
```

---

## 2. Network Topology & Ports

- **Subnet:** `192.168.66.0/24` (VMware Virtual LAN)
- **VM1 (LabNet Server):** `192.168.66.128`
- **VM2 (Client Workstation):** `192.168.66.130`

| Port | Transport | Component | Purpose |
| :--- | :--- | :--- | :--- |
| **5001** | **TCP** | `server/tcp_chat_server.py` | Multithreaded broadcast chat server; newline-delimited JSON framing |
| **2121** | **TCP** | `server/tcp_file_server.py` | Dedicated streaming file transfer server; 64 KB chunking, SHA-256 verification |
| **5000** | **TCP** | `server/tcp_server.py` | Persistent multi-message TCP echo server |
| **6000** | **UDP** | `server/udp_server.py` | Datagram echo and periodic node heartbeat receiver |
| **5050** | **TCP / HTTP** | `web/app.py` | Flask Web Portal & Socket.IO bridge |

---

## 3. Directory Structure

```
labnet-network/
├── clients/
│   ├── tcp_client.py           # Repaired persistent TCP client (:5000)
│   ├── tcp_chat_client.py      # Terminal CLI broadcast chat client (:5001)
│   ├── tcp_file_client.py      # Terminal CLI streaming file transfer client (:2121)
│   ├── udp_client.py           # UDP echo client (:6000)
│   └── udp_heartbeat_client.py # Periodic UDP heartbeat sender (:6000)
├── server/
│   ├── tcp_server.py           # Repaired persistent TCP echo server (:5000)
│   ├── tcp_chat_server.py      # Multithreaded TCP broadcast chat server (:5001)
│   ├── tcp_file_server.py      # Chunked TCP streaming file server (:2121)
│   └── udp_server.py           # UDP echo and heartbeat monitor (:6000)
├── web/
│   ├── app.py                  # Flask application & Flask-SocketIO gateway (:5050)
│   ├── services/
│   │   ├── tcp_service.py      # Persistent TCP Chat Bridge & TCP File Stream client
│   │   ├── udp_service.py      # UDP client service routines
│   │   ├── monitoring_service.py # Port checking and traceroute execution
│   │   └── db_service.py       # Supabase database abstraction layer
│   ├── templates/              # Clean university portal HTML templates
│   │   ├── base.html
│   │   ├── index.html
│   │   ├── chat.html
│   │   ├── files.html
│   │   └── network.html
│   └── static/
│       ├── css/style.css       # Academic styling (white/slate, clean typography)
│       └── js/                 # Client scripts (chat.js, files.js, socket.io.min.js)
├── test/
│   └── test_phase1_phase2.py   # Automated 4-stage end-to-end integration test suite
├── docs/
│   ├── ARCHITECTURE.md         # Detailed wire protocol specifications & concurrency model
│   ├── TESTING_GUIDE.md        # Step-by-step testing and tcpdump packet analysis guide
│   └── IMPLEMENTATION_AUDIT.md # Initial repository audit
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

### 5.1. Automated Test Suite
To verify all Phase 1 and Phase 2 socket implementations end-to-end:
```bash
python test/test_phase1_phase2.py
```

### 5.2. Running Standalone Services
Open separate terminal tabs:

```bash
# Terminal 1: TCP Broadcast Chat Server
python server/tcp_chat_server.py

# Terminal 2: TCP File Transfer Server
python server/tcp_file_server.py

# Terminal 3: UDP Heartbeat & Echo Server
python server/udp_server.py

# Terminal 4: LabNet Web Portal
python web/app.py
```

Access the web portal at: `http://localhost:5050` or `http://192.168.66.128:5050`.

### 5.3. Running CLI Clients
```bash
# Chat client from terminal
python clients/tcp_chat_client.py --server 192.168.66.128 --user "Student"

# File transfer client from terminal
python clients/tcp_file_client.py path/to/document.pdf --server 192.168.66.128
```

---

## 6. Packet Inspection with tcpdump

Inspect real BSD socket traffic on the wire:

```bash
# Capture raw TCP Chat packets on port 5001
sudo tcpdump -i any port 5001 -nn -v -X

# Capture raw TCP File Transfer segments on port 2121
sudo tcpdump -i any port 2121 -nn -v
```

Observe the classic three-way TCP handshake (`[S]`, `[S.]`, `[.]`), push-acknowledged payload segments (`[P.]`), and graceful connection closure (`[F.]`).

For full protocol specifications and testing procedures, refer to [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/TESTING_GUIDE.md](docs/TESTING_GUIDE.md).
