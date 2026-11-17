# LabNet Comprehensive Testing & Examiner Demonstration Guide

This guide separates **Unit/Security Tests**, **Local Integration Tests**, **Cross-VM Tests**, and the **19-Step Examiner Demonstration Sequence**.

---

## 1. Test Categories & Automated Suites

### 1.1 Automated Security, Persistence & Protocol Validation Suite
```bash
cd ~/Desktop/CNT_CP/labnet-network
source .venv/bin/activate
python test/test_final_validation.py
```
Covers:
- **Unit & Security:** Protected route redirects (`302`), unauthenticated API rejection (`401`), RBAC enforcement (`403` for students on admin routes), privilege-escalation prevention on public signup, path traversal rejection (`400/404`), shell-injection rejection on diagnostics, zero Lucide icon verification.
- **Local Socket Integration:** TCP Chat (`:5001`), Chunked TCP File (`:2121`), Reliable Sliding-Window ARQ UDP (`:2122` clean + 15% controlled packet loss), Direct P2P (`:2123`), UDP Heartbeat (`:6000` / isolated test port) with online $\rightarrow$ timeout offline $\rightarrow$ recovery.
- **Real Supabase Persistence:** Registers a controlled synthetic account, verifies trigger-created `profiles`, `devices`, `heartbeat_logs`, `chat_messages`, `file_transfers`, `network_sessions`, and `network_events`, then cleans up synthetic test rows.

### 1.2 Core Socket Integration Suites
```bash
python test/test_phase1_phase2.py
python test/test_phase3_phase4_phase5.py
```
> **Important Distinction:** Localhost integration tests validate protocol correctness on VM1, but are **not** a substitute for true cross-VM tests over the VMware virtual LAN (`192.168.66.0/24`).

---

## 2. Exact Service Startup Commands (VM1 — `192.168.66.128`)

Always activate `.venv` so every daemon loads `supabase` and `.env` properly:

```bash
cd ~/Desktop/CNT_CP/labnet-network
source .venv/bin/activate

# Tab 1: Multithreaded TCP Chat Server (:5001)
python server/tcp_chat_server.py

# Tab 2: Chunked TCP File Transfer Server (:2121)
python server/tcp_file_server.py

# Tab 3: Reliable Sliding-Window ARQ UDP File Server (:2122)
python server/udp_file_server.py

# Tab 4: UDP Heartbeat & Presence Server (:6000)
python server/udp_server.py

# Tab 5: Flask Portal + Socket.IO + Local SMTP (:1025) & POP3 (:1100) (:5050)
python web/app.py
```

---

## 3. Exact Client Setup Commands (VM2 — Verify IP via `ip -br addr`)

```bash
cd ~/Desktop/CNT_CP/labnet-network
git pull origin main
source .venv/bin/activate
pip install -r requirements.txt
ip -br addr   # Verify current VM2 IP on 192.168.66.0/24
```

---

## 4. Final Examiner Demonstration Sequence (19 Steps)

| Step | Open / Location | Command or Action | Expected Output | Networking Concept Demonstrated |
| :---: | :--- | :--- | :--- | :--- |
| **1** | Browser on VM1 (`http://192.168.66.128:5050/login`) | Sign in with provisioned Admin account (or register a Student account & observe password eye toggle) | Redirects to `/` with user role badge; invalid passwords display explicit error | Supabase Auth, session cookies, RBAC |
| **2** | Browser on VM1 (`/`) | View Administrator Overview Dashboard | Displays real KPI counts from Supabase (`devices`, `chat_messages`, `file_transfers`, `network_sessions`) & passive service port states | Application-layer telemetry & passive kernel inspection |
| **3** | Browser on VM1 (`/monitoring`) | Open Devices & Network Monitoring | Shows registered devices table with search/filter & zero hardcoded fake online nodes | Centralized presence monitoring |
| **4** | Terminal on VM2 | `python clients/heartbeat_client.py --server 192.168.66.128 --port 6000 --id VM2-Workstation --interval 2` | VM2 prints `ACK received [RTT: ~1.x ms]` every 2 seconds | Connectionless UDP datagrams (`SOCK_DGRAM`) |
| **5** | Browser on VM1 (`/monitoring`) | Observe `VM2-Workstation` in devices table & heartbeat log | Status shows `ONLINE` with real IP, timestamp, and measured latency | Heartbeat liveness protocol |
| **6** | Browsers on VM1 & VM2 (`/chat`) | Send messages from both VMs | Messages appear instantaneously in Dribbble 3-panel UI with zero connect/disconnect spam | Multithreaded TCP broadcast (`:5001`) + WebSocket bridge |
| **7** | Terminal on VM2 | Press `Ctrl+C` to stop `heartbeat_client.py` and wait > 10 seconds | VM1 marks `VM2-Workstation` `OFFLINE` and records a `timeout` event in `network_events` | Timeout-based failure detection |
| **8** | Browser on VM1 (`/chat`) + VM2 | Send a message on VM1 while VM2 browser is closed; reopen `/chat` on VM2 | VM2 loads full chronological history from Supabase `chat_messages` including the missed message | Separation of live TCP transport vs durable DB persistence |
| **9** | Browser on VM2 (`/files`) | Select **TCP Stream (:2121)**, choose a file, click Transmit | Streams 64 KB chunks with live progress bar, throughput (KB/s), and `VERIFIED (SHA-256 MATCH)` | Reliable connection-oriented TCP byte stream |
| **10** | Browser on VM2 (`/files`) | Select **Reliable UDP (:2122)**, choose a file, click Transmit | Splits into 1024 B sequenced datagrams with selective ACKs and `VERIFIED (SHA-256 MATCH)` | Application-layer reliability over UDP (Sliding-Window ARQ) |
| **11** | Terminal on VM2 | `python clients/udp_file_client.py sample.bin --server 192.168.66.128 --port 2122 --loss-rate 0.20` | Prints dropped chunks, timeout retransmissions, and final `SHA-256 MATCH` | Packet loss recovery & ARQ retransmission |
| **12** | Terminals on VM2 & VM3 (or 2nd node) | Receiver: `python clients/p2p_node.py --listen --port 2123`<br>Sender: `python clients/p2p_node.py --send sample.bin --target-ip <peer-ip> --target-port 2123` | Peer receives file directly with `SHA-256 MATCH`; `tcpdump` on VM1 shows zero payload packets | Direct Peer-to-Peer mesh bypassing central server |
| **13** | Browser on VM2 (`/repository`) | Click **Download** on an uploaded file, then run `sha256sum ~/Downloads/<file>` | File downloads securely (authenticated) and local SHA-256 equals repository checksum | Authenticated HTTP file retrieval & path-traversal protection |
| **14** | Browser on VM1 (`/diagnostics`) | Enter `localhost` or `google.com`, select `A`, click **Run Live DNS Lookup** | Displays upstream resolver IP, resolved addresses, and latency in ms | Domain Name System (DNS) resolution |
| **15** | Browser on VM1 (`/diagnostics`) | Click **Send Test Email to Local Spool (:1025)** | Console displays `EHLO → MAIL FROM → RCPT TO → DATA → 250 OK → QUIT` | RFC 5321 SMTP push mail protocol |
| **16** | Browser on VM1 (`/diagnostics`) | Click **Refresh Mailbox** and select message in POP3 panel (`:1100`) | Console displays `USER → PASS → STAT → LIST → RETR → QUIT` and full email text | RFC 1939 POP3 pull mail retrieval |
| **17** | Browser on VM1 (`/diagnostics`) | Click **Ping**, **Traceroute**, **Routing Table**, **Interfaces**, **Listening Sockets** | Shows exact command, exit code, execution time, and live Linux kernel output | ICMP (Layer 3), IP routing, OS socket state |
| **18** | Supabase Dashboard / `/activity` | Inspect all 7 tables (`profiles`, `devices`, `chat_messages`, `file_transfers`, `heartbeat_logs`, `network_sessions`, `network_events`) | Live rows match every executed action | Full-stack relational persistence & RLS |
| **19** | Terminal on VM1 | Run `tcpdump` capture commands below during steps 6, 9, 10, 12 | Shows real TCP 3-way handshake (`[S]`, `[S.]`, `[.]`), UDP datagrams, ACKs, and teardown | Wire-level packet inspection |

---

## 5. Packet Capture Commands (`tcpdump`)

```bash
# TCP Broadcast Chat (:5001) — observe SYN/SYN-ACK/ACK and JSON PSH frames
sudo tcpdump -i any port 5001 -nn -v -X

# TCP File Transfer (:2121) — observe 64 KB segmented stream
sudo tcpdump -i any port 2121 -nn -v

# Reliable UDP File Transfer (:2122) — observe sequenced DATA & ACK datagrams
sudo tcpdump -i any port 2122 -nn -v

# Direct P2P Transfer (:2123) — verify packets flow directly between peers, NOT VM1
sudo tcpdump -i any port 2123 -nn -v

# UDP Heartbeat Datagrams (:6000) — observe HEARTBEAT & HEARTBEAT_ACK
sudo tcpdump -i any port 6000 -nn -v -X

# Local SMTP (:1025) & POP3 (:1100)
sudo tcpdump -i any port 1025 or port 1100 -nn -v -X
```
