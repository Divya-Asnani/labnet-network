# LabNet Comprehensive Testing & Verification Guide

This guide provides end-to-end instructions for running, testing, and capturing packet evidence for all phases of **LabNet** across local and multi-VM environments.

---

## 1. Quick Automated Test Suites

### 1.1 Run Phase 1 & 2 Tests (TCP Chat & TCP File)
```bash
source .venv/bin/activate
python test/test_phase1_phase2.py
```

### 1.2 Run Phase 3, 4 & 5 Tests (UDP, P2P, Services, Auth, Persistence)
```bash
source .venv/bin/activate
python test/test_phase3_phase4_phase5.py
```

---

## 2. Starting Core Services on VM1 (Server: `192.168.66.128`)

In separate terminal tabs or multiplexer windows (`tmux`/`screen`):

```bash
source .venv/bin/activate

# Tab 1: TCP Broadcast Chat Server (Port 5001)
python server/tcp_chat_server.py

# Tab 2: TCP Chunked File Server (Port 2121)
python server/tcp_file_server.py

# Tab 3: Reliable Sliding-Window UDP File Server (Port 2122)
python server/udp_file_server.py

# Tab 4: UDP Heartbeat & Presence Server (Port 6000)
python server/udp_server.py

# Tab 5: LabNet Web Portal & Service Gateway (Port 5050)
python web/app.py
```

---

## 3. Cross-VM Testing from VM2 (`192.168.66.130`)

### 3.1 UDP Heartbeat, Latency & Offline Timeout Detection
Run the heartbeat client on VM2:
```bash
# Sends heartbeats every 2s; simulates 3 missed heartbeats after packet #3 to trigger offline detection
python clients/heartbeat_client.py --server 192.168.66.128 --port 6000 --id "Student-VM2" --interval 2 --simulate-missed 3
```
**Expected Observation:**
- Packets 1–3 receive immediate `HEARTBEAT_ACK` with RTT latency.
- Client pauses for 8 seconds. VM1 console prints: `[OFFLINE] Student-VM2 (192.168.66.130) - Timeout exceeded (10.0s)`.
- Client resumes sending heartbeats. VM1 console prints: `[HEARTBEAT] Student-VM2 (192.168.66.130) is ONLINE`.

### 3.2 Reliable UDP File Transfer (Port 2122)
Create a test file on VM2 and stream it over UDP to VM1:
```bash
dd if=/dev/urandom of=research_data.bin bs=1M count=1

# Clean transfer
python clients/udp_file_client.py research_data.bin --server 192.168.66.128 --port 2122

# Transfer with 20% simulated packet loss to test sliding-window ARQ retransmissions
python clients/udp_file_client.py research_data.bin --server 192.168.66.128 --port 2122 --loss-rate 0.20
```
**Expected Observation:**
- With packet loss, dropped chunks trigger timeout detection.
- Retransmission counters increment in the live progress bar.
- Upon completion, the server reassembles all chunks sequentially and validates the SHA-256 hash.

### 3.3 Direct Peer-to-Peer (P2P) File Transfer (VM2 -> VM3)
To prove genuine P2P functionality without routing file payloads through the central server:
1. **On VM3 (`192.168.66.131`):** Start the P2P listener daemon:
   ```bash
   python clients/p2p_node.py --listen --port 2123 --name "VM3-Workstation" --dir p2p_received
   ```
2. **On VM2 (`192.168.66.130`):** Transmit directly to VM3:
   ```bash
   dd if=/dev/urandom of=direct_payload.dat bs=1M count=2
   python clients/p2p_node.py --send direct_payload.dat --target-ip 192.168.66.131 --target-port 2123
   ```
3. **Verification:** Inspect network traffic on VM1 during transfer using `sudo tcpdump -i any host 192.168.66.131 and port 2123`. VM1 sees **zero** payload packets because the stream flows exclusively between VM2 and VM3!

### 3.4 Remote File Download & Verification
1. Download a stored file from the server's repository:
   ```bash
   curl -O http://192.168.66.128:5050/api/files/download/research_data.bin
   ```
2. Compare the SHA-256 hash of the downloaded file with the original:
   ```bash
   sha256sum research_data.bin
   ```

---

## 4. Web Portal Verification & Testing

Open a web browser on VM1 or VM2 and navigate to `http://192.168.66.128:5050`.

### 4.1 Authentication (Supabase Auth)
1. Click **Sign In** in the top navigation bar.
2. Switch to **Register** tab: enter display name, email (e.g., `student@labnet.local`), role (`Student Workstation`), and password.
3. Authenticate session. The top navigation bar now displays your user name and role badge.

### 4.2 File Transfer Portal (`/files`)
1. Select a file from disk.
2. Select **Transport Protocol**:
   - Choose `[ TCP ]` for Port 2121 stream.
   - Choose `[ UDP ]` for Port 2122 sliding-window ARQ stream.
3. Click **Start Transfer**. Observe the real-time byte counter, ASCII pipeline animation, throughput calculation, and SHA-256 verification receipt.
4. Scroll to **Server File Repository**: verify your uploaded file appears in the table with its size and checksum. Click **Download** to retrieve it back.

### 4.3 Network Services & Diagnostics (`/network`)
1. **Live Workstations Table:** Verify registered VM nodes display real-time status (`ONLINE`/`OFFLINE`) and heartbeat timestamps.
2. **System Diagnostics:** Click `PING`, `TRACEROUTE`, `ROUTING TABLE`, `INTERFACES`, `LISTENING SOCKETS` to inspect live kernel networking data.
3. **DNS Service:** Enter a hostname (e.g. `localhost`) and click **Resolve DNS Query** to benchmark lookup duration.
4. **SMTP Mail Service:** Click **Send Notification via SMTP** to queue an alert on port 1025.
5. **POP3 Mail Service:** Click **Fetch Inbox via POP3** to retrieve queued alerts on port 1100.

---

## 5. Packet Capture Evidence with tcpdump

Run `tcpdump` commands to capture genuine packet traces on the wire:

```bash
# 1. Capture TCP Chat Packets (Port 5001)
sudo tcpdump -i any port 5001 -nn -v -X

# 2. Capture TCP File Transfer Segments (Port 2121)
sudo tcpdump -i any port 2121 -nn -v

# 3. Capture Reliable UDP File Datagrams (Port 2122)
sudo tcpdump -i any port 2122 -nn -v

# 4. Capture Direct P2P Traffic (Port 2123)
sudo tcpdump -i any port 2123 -nn -v

# 5. Capture Periodic UDP Heartbeats (Port 6000)
sudo tcpdump -i any port 6000 -nn -v -X

# 6. Capture Local SMTP Email Transmission (Port 1025)
sudo tcpdump -i any port 1025 -nn -v -X

# 7. Capture Local POP3 Retrieval (Port 1100)
sudo tcpdump -i any port 1100 -nn -v -X
```
