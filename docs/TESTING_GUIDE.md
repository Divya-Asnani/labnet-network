# LabNet Comprehensive Testing & Verification Guide

This guide provides end-to-end instructions for testing and validating the Phase 1 (Real-Time TCP Chat) and Phase 2 (TCP File Transfer) implementations of **LabNet**.

---

## 1. Quick Verification (Automated Test Suite)

LabNet includes an automated integration test suite that spins up the socket servers, tests persistent TCP echo, multithreaded broadcast chat, chunked file transfer with SHA-256 validation, and the Flask web gateway bridge:

```bash
# Activate virtual environment
source .venv/bin/activate

# Execute the test suite
python test/test_phase1_phase2.py
```

### Expected Output:
```
--- TEST 1: Repaired server/tcp_server.py (Port 5000) ---
[PASS] Repaired TCP server supports multiple messages on persistent connection.

[*] Starting Phase 1 (TCP Chat :5001) and Phase 2 (TCP File :2121) servers...
[+] Both socket servers running and listening.

--- TEST 2: Dedicated server/tcp_chat_server.py (Port 5001) ---
Client 2 received broadcast: Researcher -> 'Hello from VM1 Researcher'
Client 1 received broadcast: Student -> 'Hello from VM2 Student'
[PASS] Multithreaded TCP chat server broadcasted bidirectional messages successfully.

--- TEST 3: server/tcp_file_server.py (Port 2121) ---
Server Verification Result: {'status': 'SUCCESS', 'verified': True, ...}
[PASS] TCP File Server successfully received, saved, and verified 256 KB file.

--- TEST 4: End-to-End Flask Gateway & Service Bridge ---
POST /api/chat/send: 200 -> {'status': 'delivered', ...}
POST /api/files/upload: 200 -> {'status': 'completed', ...}
[PASS] End-to-End Flask Gateway, TCP Chat Bridge, and TCP File Stream verified successfully!

=======================================================
   ALL PHASE 1 & PHASE 2 VERIFICATION TESTS PASSED!   
=======================================================
```

---

## 2. Testing on VM1 (Localhost / Single VM)

You can run individual servers and clients in separate terminal windows on VM1.

### Terminal 1: Start TCP Chat Server (:5001)
```bash
source .venv/bin/activate
python server/tcp_chat_server.py
```
*Expected:* `[TCP CHAT SERVER] Listening on 0.0.0.0:5001 (Multithreaded)...`

### Terminal 2: Start TCP File Server (:2121)
```bash
source .venv/bin/activate
python server/tcp_file_server.py
```
*Expected:* `[TCP FILE SERVER] Listening on 0.0.0.0:2121 (Chunked streaming mode)...`

### Terminal 3: Start LabNet Web Portal (:5050)
```bash
source .venv/bin/activate
python web/app.py
```
*Expected:* `* Running on http://0.0.0.0:5050`

### Terminal 4: Run Terminal Chat Client
```bash
source .venv/bin/activate
python clients/tcp_chat_client.py --server 127.0.0.1 --user Researcher
```

### Terminal 5: Run Terminal File Transfer Client
```bash
# Generate a test payload file
dd if=/dev/urandom of=/tmp/sample_data.bin bs=1M count=2

# Transmit to TCP file server
source .venv/bin/activate
python clients/tcp_file_client.py /tmp/sample_data.bin --server 127.0.0.1
```

---

## 3. Two-VM Distributed Testing (VM1 Server + VM2 Client)

### Network Configuration:
- **VM1 (LabNet Server):** `192.168.66.128`
- **VM2 (LabNet Client Workstation):** `192.168.66.130` (or `192.168.66.129` depending on your DHCP lease)

### Step 3.1: Start Services on VM1 (Server: `192.168.66.128`)

Open separate terminal tabs or use a multiplexer (`tmux`/`screen`):

```bash
# Tab 1: Start Chat Server
python server/tcp_chat_server.py

# Tab 2: Start File Server
python server/tcp_file_server.py

# Tab 3: Start UDP Echo / Heartbeat Server
python server/udp_server.py

# Tab 4: Start Web Portal
python web/app.py
```

### Step 3.2: Verify Network Reachability from VM2

On VM2, open a terminal and verify ICMP connectivity:
```bash
ping -c 3 192.168.66.128
```

Verify that the ports are reachable:
```bash
nc -zv 192.168.66.128 5001
nc -zv 192.168.66.128 2121
nc -zv 192.168.66.128 5050
```

### Step 3.3: Run Terminal Chat on VM2
```bash
python clients/tcp_chat_client.py --server 192.168.66.128 --user "Student-VM2"
```
Type messages and hit Enter. Any other connected client or web browser on VM1 will see the message appear immediately.

### Step 3.4: Transfer Files from VM2 to VM1
```bash
# Create a 5MB test file on VM2
dd if=/dev/urandom of=test_report.bin bs=1M count=5

# Transmit over raw TCP socket to VM1
python clients/tcp_file_client.py test_report.bin --server 192.168.66.128
```
*Expected Console Output on VM2:*
```
[+] Target: 192.168.66.128:2121
[+] File:   test_report.bin (5,242,880 bytes)
[+] SHA256: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
[+] Connecting...
[+] Connected! Handshaking with server...
[+] Server ACK: Server ready to receive stream
[==================================================] 100.0% (5242880/5242880 bytes)
[+] Stream finished. Waiting for cryptographic verification...

============================================================
           TRANSFER VERIFICATION RECEIPT                    
============================================================
  Status:           SUCCESS
  Integrity:        VERIFIED (SHA-256 match)
  Remote File:      uploads/test_report.bin
  Remote Size:      5,242,880 bytes
  Transfer Speed:   34,512.45 KB/s
  Timestamp:        2026-11-17 22:30:30
============================================================
```

### Step 3.5: Multi-User Web Browser Chat
1. On VM1, open a browser and navigate to: `http://localhost:5050/chat`
   - Select persona: **Researcher**
2. On VM2, open a browser and navigate to: `http://192.168.66.128:5050/chat`
   - Select persona: **Student Workstation**
3. Type a message from VM1 — observe it instantly appear on VM2.
4. Type a message from VM2 — observe it instantly appear on VM1.
5. Notice the **Packet Flow Tracker** on the right sidebar updating real-time socket statistics (Packets In/Out, Latency, Socket State).

---

## 4. Packet Inspection with tcpdump & Wireshark

To prove that raw TCP socket traffic is passing between the machines (and not fake HTTP polling), run `tcpdump` on VM1 or VM2:

### 4.1. Inspecting TCP Chat Packets (Port 5001)
```bash
sudo tcpdump -i any port 5001 -nn -v -X
```

#### What to Look For:
1. **Three-Way Handshake:**
   - `[S]` (SYN): Client initiates connection.
   - `[S.]` (SYN-ACK): Server acknowledges and synchronizes.
   - `[.]` (ACK): Client acknowledges connection establishment.
2. **Data Transmission:**
   - `[P.]` (PSH-ACK): TCP packets containing raw JSON text payload:
     `{"type": "chat", "sender": "Student", "text": "..."}\n`
3. **Graceful Teardown:**
   - `[F.]` (FIN): Sockets closing cleanly without reset (`RST`).

### 4.2. Inspecting TCP File Transfer Packets (Port 2121)
```bash
sudo tcpdump -i any port 2121 -nn -v
```

#### What to Look For:
1. Handshake metadata packet (`PSH, ACK`) carrying the filename, filesize, and SHA-256 checksum.
2. High-speed burst of sequential `65536` byte TCP segment payloads (`ACK`, `PSH, ACK`).
3. Verification receipt packet (`PSH, ACK`) returning `{"status": "SUCCESS", "verified": true}`.

---

## 5. Troubleshooting & Diagnostics

| Symptom | Cause | Solution |
| :--- | :--- | :--- |
| `[Errno 98] Address already in use` | A previous instance is still holding the port. | Run `fuser -k <port>/tcp` or `pkill -f tcp_chat_server.py`. |
| `[Errno 111] Connection refused` | The target server is not running or listening on `127.0.0.1` instead of `0.0.0.0`. | Verify server is started with `0.0.0.0` and check `netstat -tuln \| grep <port>`. |
| `No route to host` / `Timeout` | VMware virtual network adapter or Linux firewall (`ufw`) blocking. | On VM1: run `sudo ufw allow 5001/tcp && sudo ufw allow 2121/tcp && sudo ufw allow 5050/tcp`. |
