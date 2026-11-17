# LabNet Multi-Protocol Network Specifications

This document provides in-depth protocol specifications, wire packet formats, framing mechanisms, and state transitions for all transport and application-layer protocols implemented in **LabNet**.

---

## 1. Protocol Architecture & Port Allocations

| Protocol Name | Transport Layer | Default Port | Framing / Standard | Primary Role |
| :--- | :--- | :--- | :--- | :--- |
| **LabNet TCP Chat** | TCP | `5001` | Newline-Delimited JSON (`\n`) | Multithreaded broadcast research chat |
| **LabNet TCP File Stream** | TCP | `2121` | JSON Header + 64 KB Binary Chunks | Chunked streaming file transfer with SHA-256 verification |
| **LabNet UDP Heartbeat** | UDP | `6000` | Pipe-Delimited ASCII (`\|`) | Node discovery, presence, and latency tracking |
| **LabNet Reliable UDP (LN-RUDP)** | UDP | `2122` | Sliding-Window ARQ / Base64 Data | Reliable, sequenced file transfer with timeout retransmission |
| **LabNet Direct P2P Transfer** | TCP / UDP | `2123` | Direct Socket Stream | Node-to-node transfer bypassing the central server |
| **LabNet Portal Gateway** | TCP / HTTP | `5050` | RFC 9112 / WebSocket | Web management portal, Socket.IO gateway, REST APIs |
| **LabNet Mail (SMTP)** | TCP | `1025` | RFC 5321 (SMTP) | Local asynchronous alert and notification queuing |
| **LabNet Mail (POP3)** | TCP | `1100` | RFC 1939 (POP3) | Workstation notification retrieval |
| **LabNet DNS Client** | UDP | `53` | RFC 1035 | Domain lookup and resolution latency benchmarking |
| **LabNet Diagnostics** | ICMP / Raw | N/A | RFC 792 / Linux Kernel | Ping, traceroute, routing table, interface inspection |

---

## 2. Reliable Sliding-Window ARQ UDP File Protocol (Port 2122)

Because raw UDP provides neither reliability nor ordering, LabNet implements **LN-RUDP**—a reliable transport layer on top of UDP datagrams.

### 2.1 Packet Formats

#### 1. Transfer Initiation (START)
```json
{
  "action": "START",
  "transfer_id": "7cc7350e-9260-48c9-88e3-87965dac73f4",
  "filename": "spectral_analysis.pdf",
  "filesize": 122880,
  "sha256": "4cd9788fd6aeb9684aef0a6284b80043273ada66cbc9f073d6133e39e7fdb02e",
  "total_chunks": 120,
  "chunk_size": 1024,
  "sender": "Student-VM2"
}
```

#### 2. Server Handshake Response (START_ACK)
```json
{
  "action": "START_ACK",
  "transfer_id": "7cc7350e-9260-48c9-88e3-87965dac73f4",
  "status": "READY",
  "chunk_size": 1024
}
```

#### 3. Data Datagram (DATA)
```json
{
  "action": "DATA",
  "transfer_id": "7cc7350e-9260-48c9-88e3-87965dac73f4",
  "seq": 4,
  "total_chunks": 120,
  "data": "<base64-encoded-payload>"
}
```

#### 4. Selective Acknowledgement (ACK)
```json
{
  "action": "ACK",
  "transfer_id": "7cc7350e-9260-48c9-88e3-87965dac73f4",
  "seq": 4,
  "received_count": 5,
  "total_chunks": 120
}
```

#### 5. Verification & Completion Receipt (FIN_ACK)
```json
{
  "action": "FIN_ACK",
  "transfer_id": "7cc7350e-9260-48c9-88e3-87965dac73f4",
  "status": "SUCCESS",
  "verified": true,
  "sha256": "4cd9788fd6aeb9684aef0a6284b80043273ada66cbc9f073d6133e39e7fdb02e",
  "filesize": 122880,
  "speed_kbps": 14.15,
  "retransmissions": 20,
  "save_path": "/uploads/spectral_analysis.pdf",
  "timestamp": "2026-11-17 23:12:30"
}
```

### 2.2 Sliding-Window ARQ State Machine

```
Sender State                                                   Receiver State
=============                                                  ==============
[INIT]                                                         [LISTENING]
  │─── Send START ───────────────────────────────────────────────▶│ (Validate metadata, create state)
  │◀── Receive START_ACK ─────────────────────────────────────────│
[TRANSMIT WINDOW: base .. base+N]                              [BUFFERING CHUNKS]
  │─── Send Chunks seq: 0, 1, 2, 3, 4 ───────────────────────────▶│ (Store in state.chunks[seq])
  │                                                               │ (Duplicate? Send ACK, drop body)
  │◀── Receive ACK seq: 0 ────────────────────────────────────────│
  │    (Slide base: 1 .. 5)                                       │
  │─── Send Chunk seq: 5 ────────────────────────────────────────▶│
  │                                                               │
  │    [Packet Loss Event on seq: 2]                              │
  │    (Timer expires for chunk #2 > 1.0s)                        │
  │─── Retransmit Chunk seq: 2 ──────────────────────────────────▶│ (Receive missing chunk #2)
  │◀── Receive ACK seq: 2 ────────────────────────────────────────│
  │    (Slide base forward past all ACKed chunks)                 │
  │                                                               │
  │    ... Repeat until base == total_chunks                      │
  │                                                               │ (Reassemble sequentially)
  │                                                               │ (Compute SHA-256)
  │◀── Receive FIN_ACK (verified: true, sha256) ──────────────────│ (Save to disk uploads/)
[COMPLETED]                                                    [STORED]
```

---

## 3. Direct Peer-to-Peer (P2P) File Transfer Protocol (Port 2123)

In standard client-server mode, file bytes travel $Node \rightarrow Server$. In **Direct P2P Mode**, file bytes travel $Node A \rightarrow Node B$ directly over the VMware LAN, completely bypassing VM1.

### 3.1 Coordination & Discovery Phase (Central Server)
1. Receiving peer starts `clients/p2p_node.py --listen --port 2123 --name VM3-Workstation`.
2. It sends an HTTP registration POST to `http://192.168.66.128:5050/api/peers/register`:
   ```json
   {"peer_name": "VM3-Workstation", "ip": "192.168.66.131", "port": 2123, "status": "READY"}
   ```
3. Sender queries `GET /api/peers/list` to discover registered reachable peer IPs.

### 3.2 Direct Data Streaming Phase (Peer to Peer)
1. Sender initiates raw BSD TCP socket connection directly to `192.168.66.131:2123`.
2. Sender transmits JSON handshake header:
   ```json
   {"action": "p2p_upload", "filename": "data.dat", "filesize": 81920, "sha256": "c2c9...", "sender_name": "VM2"}\n
   ```
3. Receiver responds with `{"status": "READY"}\n`.
4. Sender streams raw binary in 64 KB chunks directly to the receiver.
5. Receiver writes directly to local directory (e.g., `p2p_received/`), calculates SHA-256, and returns a verified transfer receipt over the direct TCP socket.
6. The central server (`192.168.66.128`) acts solely as a discovery coordinator; not a single byte of file payload routes through it.

---

## 4. UDP Heartbeat & Presence Protocol (Port 6000)

### 4.1 Wire Message Format
- **Client Heartbeat Datagram:**
  ```
  HEARTBEAT|<client_id>|<timestamp_epoch>
  Example: HEARTBEAT|Student-VM2|1731862350.1245
  ```
- **Server Response Datagram:**
  ```
  HEARTBEAT_ACK|<client_id>|<echoed_client_timestamp>|<server_readable_time>
  Example: HEARTBEAT_ACK|Student-VM2|1731862350.1245|2026-11-17 23:12:30
  ```

### 4.2 Latency & Offline Timeout Detection
- **Latency Measurement:** The server calculates Transit Latency as $(t_{server\_recv} - t_{client\_sent}) \times 1000$ ms. The client calculates Round-Trip Time (RTT) as $(t_{client\_ack\_recv} - t_{client\_sent}) \times 1000$ ms.
- **Offline Detection:** If current time minus last recorded heartbeat exceeds `HEARTBEAT_TIMEOUT` (10 seconds), the server marks the device as `OFFLINE`, logs a `disconnect` event in `network_events`, and updates the `devices` table in Supabase.
- **Recovery:** Upon receiving a new heartbeat from an offline device, status transitions back to `ONLINE` and a recovery event is logged.

---

## 5. Network Services (HTTP, DNS, SMTP, POP3, ICMP)

### 5.1 HTTP (Port 5050)
- RFC 9112 compliant Flask web server.
- Session authentication with secure cookies and Supabase Auth tokens.
- RESTful JSON endpoints returning standard HTTP status codes: `200 OK`, `201 Created`, `400 Bad Request`, `401 Unauthorized`, `404 Not Found`, `500 Server Error`, `503 Service Unavailable`.

### 5.2 DNS Client Resolution (UDP Port 53)
- Resolves domain queries against the system resolver (`/etc/resolv.conf`).
- Supports record types: `A` (IPv4 address), `AAAA` (IPv6 address), `PTR` (Reverse pointer).
- Captures DNS resolution round-trip latency in milliseconds.

### 5.3 SMTP Internal Mail (TCP Port 1025)
- Complies with RFC 5321.
- State sequence: `CONNECT` $\rightarrow$ `220` $\rightarrow$ `EHLO` $\rightarrow$ `250` $\rightarrow$ `MAIL FROM` $\rightarrow$ `250` $\rightarrow$ `RCPT TO` $\rightarrow$ `250` $\rightarrow$ `DATA` $\rightarrow$ `354` $\rightarrow$ Payload $\rightarrow$ `\r\n.\r\n` $\rightarrow$ `250 OK` $\rightarrow$ `QUIT` $\rightarrow$ `221 Bye`.
- Queues messages directly into in-memory spool for immediate local retrieval.

### 5.4 POP3 Mail Retrieval (TCP Port 1100)
- Complies with RFC 1939.
- State sequence: `CONNECT` $\rightarrow$ `+OK` $\rightarrow$ `USER <user>` $\rightarrow$ `+OK` $\rightarrow$ `PASS <pass>` $\rightarrow$ `+OK` $\rightarrow$ `STAT` $\rightarrow$ `LIST` $\rightarrow$ `RETR <index>` $\rightarrow$ `QUIT`.

### 5.5 ICMP & Diagnostics
- Executes real kernel tools via safe Python `subprocess.run(argument_list)`:
  - `ping -c 4 -W 2 <ip>`: ICMP echo request/reply (RFC 792).
  - `traceroute -m 10 <ip>`: Hop-by-hop TTL expiry diagnostics.
  - `ip route`: Kernel routing table examination.
  - `ip -br addr`: Physical and virtual network interface details.
  - `ss -tuln`: Open listening TCP and UDP socket sockets.
