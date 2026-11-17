"""
Automated Verification Suite for Phase 1 & Phase 2
===================================================
Unified integration test suite verifying:
1. Repaired TCP server (Port 5000) - persistent multi-turn loop.
2. Multithreaded TCP Chat Server (Port 5001) - 2 concurrent clients, broadcast, disconnect.
3. Reliable TCP File Server (Port 2121) - chunk streaming, SHA-256 verification, file integrity.
4. Flask HTTP & SocketIO Gateway - /api/chat/send and /api/files/upload end-to-end.
"""

import socket
import subprocess
import time
import json
import hashlib
import os
import sys

def wait_for_port_free(port, timeout=10.0):
    start = time.time()
    while time.time() - start < timeout:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) != 0:
                return True
        time.sleep(0.3)
    return False

def wait_for_port_open(port, timeout=10.0):
    start = time.time()
    while time.time() - start < timeout:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) == 0:
                return True
        time.sleep(0.3)
    return False

def stop_process(proc):
    if not proc:
        return
    try:
        proc.terminate()
        proc.communicate(timeout=2.0)
    except Exception:
        try:
            proc.kill()
            proc.communicate(timeout=1.0)
        except Exception:
            pass

def test_repaired_tcp_server():
    print("\n--- TEST 1: Repaired server/tcp_server.py (Port 5000) ---")
    wait_for_port_free(5000)
    srv = subprocess.Popen(['python3', '-u', 'server/tcp_server.py'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    wait_for_port_open(5000)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(('127.0.0.1', 5000))
        
        # Message 1
        s.sendall(b"Hello 1")
        r1 = s.recv(1024).decode()
        print(f"Message 1 response: {r1}")
        assert "Hello from LabNet TCP Server" in r1, f"Unexpected response: {r1}"
        
        # Message 2 (verifies connection stayed alive)
        s.sendall(b"Hello 2")
        r2 = s.recv(1024).decode()
        print(f"Message 2 response: {r2}")
        assert "Hello from LabNet TCP Server" in r2, f"Unexpected response: {r2}"
        
        s.close()
        print("[PASS] Repaired TCP server supports multiple messages on persistent connection.")
    finally:
        stop_process(srv)
        wait_for_port_free(5000)

def test_tcp_chat_server():
    print("\n--- TEST 2: Dedicated server/tcp_chat_server.py (Port 5001) ---")
    # Client 1: Researcher
    c1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c1.connect(('127.0.0.1', 5001))
    
    # Client 2: Student
    c2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c2.connect(('127.0.0.1', 5001))
    time.sleep(0.5)
    
    # Send message from Client 1
    msg1 = {
        "type": "chat",
        "sender": "Researcher",
        "text": "Hello from VM1 Researcher",
        "timestamp": "2026-11-17 22:30:00"
    }
    c1.sendall((json.dumps(msg1) + "\n").encode('utf-8'))
    
    # Client 2 should receive the broadcast
    buf = ""
    c2.settimeout(3.0)
    start_t = time.time()
    received_chat = False
    while time.time() - start_t < 3.0:
        chunk = c2.recv(1024).decode('utf-8')
        buf += chunk
        if "\n" in buf:
            lines = buf.split("\n")
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                if data.get("text") == "Hello from VM1 Researcher":
                    received_chat = True
                    print(f"Client 2 received broadcast: {data['sender']} -> '{data['text']}'")
                    break
        if received_chat:
            break
    
    assert received_chat, "Client 2 failed to receive broadcast from Client 1"

    # Now send from Client 2 -> Client 1
    msg2 = {
        "type": "chat",
        "sender": "Student",
        "text": "Hello from VM2 Student",
        "timestamp": "2026-11-17 22:30:01"
    }
    c2.sendall((json.dumps(msg2) + "\n").encode('utf-8'))
    
    buf1 = ""
    c1.settimeout(3.0)
    start_t = time.time()
    received_reply = False
    while time.time() - start_t < 3.0:
        chunk = c1.recv(1024).decode('utf-8')
        buf1 += chunk
        if "\n" in buf1:
            lines = buf1.split("\n")
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                if data.get("text") == "Hello from VM2 Student":
                    received_reply = True
                    print(f"Client 1 received broadcast: {data['sender']} -> '{data['text']}'")
                    break
        if received_reply:
            break
            
    assert received_reply, "Client 1 failed to receive broadcast from Client 2"

    c1.close()
    c2.close()
    print("[PASS] Multithreaded TCP chat server broadcasted bidirectional messages successfully.")

def test_tcp_file_server():
    print("\n--- TEST 3: server/tcp_file_server.py (Port 2121) ---")
    # Create a sample 256 KB test file
    test_payload = b"LabNet Reliable TCP File Transfer Chunk Data \x00\xff" * 5000
    test_filename = "audit_sample.dat"
    test_filepath = os.path.join("/tmp", test_filename)
    with open(test_filepath, "wb") as f:
        f.write(test_payload)
        
    expected_sha = hashlib.sha256(test_payload).hexdigest()
    print(f"Created test file '{test_filepath}': {len(test_payload)} bytes | SHA256: {expected_sha}")

    # Connect to file server
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(('127.0.0.1', 2121))
    
    # Send metadata header
    meta = {
        "action": "upload",
        "filename": test_filename,
        "filesize": len(test_payload),
        "sha256": expected_sha,
        "sender": "Student_VM2"
    }
    s.sendall((json.dumps(meta) + "\n").encode('utf-8'))
    
    # Read READY acknowledgment
    ack_buf = ""
    while "\n" not in ack_buf:
        ack_buf += s.recv(1024).decode('utf-8')
    ack = json.loads(ack_buf.strip())
    print(f"Server Handshake: {ack}")
    assert ack.get("status") == "READY", f"Server not ready: {ack}"
    
    # Stream chunks
    chunk_size = 16384
    for i in range(0, len(test_payload), chunk_size):
        s.sendall(test_payload[i:i+chunk_size])
        
    # Read final response
    res_buf = ""
    while "\n" not in res_buf:
        res_buf += s.recv(1024).decode('utf-8')
    res = json.loads(res_buf.strip())
    print(f"Server Verification Result: {res}")
    
    assert res.get("status") == "SUCCESS", f"Transfer failed: {res}"
    assert res.get("verified") is True, "Checksum verification failed"
    assert res.get("sha256") == expected_sha, "Checksum mismatch"
    
    # Verify file exists on disk
    saved_path = res.get("save_path")
    assert os.path.exists(saved_path), f"File not found on disk at {saved_path}"
    with open(saved_path, "rb") as f:
        disk_content = f.read()
    assert disk_content == test_payload, "Disk content does not match transmitted payload!"
    
    s.close()
    print("[PASS] TCP File Server successfully received, saved, and verified 256 KB file.")

def test_flask_e2e():
    print("\n--- TEST 4: End-to-End Flask Gateway & Service Bridge ---")
    sys.path.insert(0, os.path.abspath('web'))
    import app as flask_app
    client = flask_app.app.test_client()
    time.sleep(1.0)  # Allow chat_bridge background thread to connect
    
    # Test HTTP chat send bridge
    r_chat = client.post('/api/chat/send', json={
        'message': 'Testing chat bridge over TCP 5001',
        'sender': 'Researcher',
        'device_ip': '192.168.66.128'
    })
    print(f"POST /api/chat/send: {r_chat.status_code} -> {r_chat.get_json()}")
    assert r_chat.status_code == 200, f"Chat send failed: {r_chat.data}"
    
    # Test file upload streaming bridge
    sample_file_content = b"Research Paper Data TCP Socket Test \x01\x02\x03\x04" * 1000
    import io
    file_obj = io.BytesIO(sample_file_content)
    data = {
        'file': (file_obj, 'research_paper.pdf'),
        'protocol': 'TCP',
        'sender': 'Researcher'
    }
    r_file = client.post('/api/files/upload', data=data, content_type='multipart/form-data')
    print(f"POST /api/files/upload: {r_file.status_code} -> {r_file.get_json()}")
    assert r_file.status_code == 200, f"File upload failed: {r_file.data}"
    result = r_file.get_json().get("result", {})
    assert result.get("verified") is True, f"Integrity check failed: {result}"
    
    print("[PASS] End-to-End Flask Gateway, TCP Chat Bridge, and TCP File Stream verified successfully!")

if __name__ == '__main__':
    srv_chat = None
    srv_file = None
    try:
        # Step 1: Test legacy standalone server
        test_repaired_tcp_server()

        # Step 2: Launch Phase 1 and Phase 2 servers
        wait_for_port_free(5001)
        wait_for_port_free(2121)
        print("\n[*] Starting Phase 1 (TCP Chat :5001) and Phase 2 (TCP File :2121) servers...")
        srv_chat = subprocess.Popen(['python3', '-u', 'server/tcp_chat_server.py'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        srv_file = subprocess.Popen(['python3', '-u', 'server/tcp_file_server.py'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert wait_for_port_open(5001), "Failed to open port 5001"
        assert wait_for_port_open(2121), "Failed to open port 2121"
        print("[+] Both socket servers running and listening.")

        # Step 3: Run chat test suite against running server
        test_tcp_chat_server()

        # Step 4: Run file server test suite against running server
        test_tcp_file_server()

        # Step 5: Run end-to-end web gateway integration
        test_flask_e2e()

        print("\n=======================================================")
        print("   ALL PHASE 1 & PHASE 2 VERIFICATION TESTS PASSED!   ")
        print("=======================================================")
    except Exception as e:
        print(f"\n[FAILED] Verification suite encountered error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        print("\n[*] Cleaning up server processes...")
        stop_process(srv_chat)
        stop_process(srv_file)
        wait_for_port_free(5001)
        wait_for_port_free(2121)
        print("[*] Port cleanup complete.")
