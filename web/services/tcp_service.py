"""
LabNet TCP Service (web/services/tcp_service.py)
================================================
Flask gateway service for bridging browser HTTP / WebSocket requests
with raw Python TCP socket servers on the local machine or remote VMs.

Endpoints bridged:
- Legacy TCP Request-Reply: Port 5000 (server/tcp_server.py)
- Real-Time Broadcast TCP Chat: Port 5001 (server/tcp_chat_server.py)
- Reliable TCP File Transfer:   Port 2121 (server/tcp_file_server.py)
"""

import socket
import threading
import json
import hashlib
import time
import os
from datetime import datetime
from .monitoring_service import add_event, add_bytes

from web.database.supabase_client import get_config_val

# Network server configurations (loaded from .env with safe local loopback default)
TCP_HOST = os.getenv("TCP_BRIDGE_HOST", "127.0.0.1")
TCP_PORT = int(get_config_val("TCP_PORT", 5000))
CHAT_PORT = int(get_config_val("TCP_CHAT_PORT", 5001))
FILE_PORT = int(get_config_val("TCP_FILE_PORT", 2121))

CHUNK_SIZE = 65536  # 64 KB streaming buffer


# ============================================================================
# 1. LEGACY TCP ECHO SERVICE (Port 5000)
# ============================================================================

def send_tcp_message(message, sender="Student"):
    """
    Connects to the legacy TCP server on port 5000, sends a message,
    reads the response, and closes the connection.
    """
    try:
        ip = "192.168.66.130" if sender == "Student" else "192.168.66.131"
        add_event('TCP CONNECTION', f"{sender} → LabNet Server\n{ip}:xxxxx → {TCP_HOST}:{TCP_PORT}\nCONNECTED")

        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket.settimeout(5.0)
        client_socket.connect((TCP_HOST, TCP_PORT))

        payload = message.encode("utf-8")
        client_socket.sendall(payload)
        add_bytes(len(payload))

        response = client_socket.recv(1024).decode("utf-8", errors="replace")
        add_bytes(len(response.encode("utf-8")))
        client_socket.close()

        add_event('TCP CHAT', f"{sender} → Server\n'{message[:30]}...'\nDELIVERED")
        return True, response
    except Exception as err:
        add_event('TCP ERROR', f"{sender} → Server\nConnection Failed\n{str(err)}")
        return False, str(err)


# ============================================================================
# 2. REAL-TIME PERSISTENT TCP CHAT BRIDGE (Port 5001)
# ============================================================================

class TCPChatBridge:
    """
    Maintains a persistent, full-duplex TCP client connection to
    server/tcp_chat_server.py on port 5001.

    When messages arrive from the TCP socket, it invokes the registered
    on_message callback (which emits the frame to Flask-SocketIO browsers).
    """

    def __init__(self, host=TCP_HOST, port=CHAT_PORT):
        self.host = host
        self.port = port
        self.sock = None
        self.connected = False
        self.running = False
        self.lock = threading.Lock()
        self.message_callbacks = []
        self._thread = None

    def add_message_callback(self, callback):
        """Register a callback function to handle incoming broadcast frames."""
        self.message_callbacks.append(callback)

    def start(self):
        """Start the persistent background bridge thread."""
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run_bridge, daemon=True)
        self._thread.start()

    def _run_bridge(self):
        """Background loop that connects and streams messages from TCP chat server."""
        while self.running:
            try:
                print(f"[TCP CHAT BRIDGE] Connecting to TCP Chat Server at {self.host}:{self.port}...")
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(10.0)
                s.connect((self.host, self.port))
                s.settimeout(None)  # Blocking recv for streaming

                with self.lock:
                    self.sock = s
                    self.connected = True
                print(f"[TCP CHAT BRIDGE] Connected successfully to {self.host}:{self.port}!")
                add_event('TCP BRIDGE', f"Flask Web Gateway ↔ TCP Chat Server ({self.host}:{self.port})\nCONNECTED")

                # Receive stream loop
                read_buffer = ""
                while self.running:
                    raw = s.recv(4096)
                    if not raw:
                        print("[TCP CHAT BRIDGE] TCP Chat Server closed connection.")
                        break

                    read_buffer += raw.decode("utf-8", errors="replace")
                    while "\n" in read_buffer:
                        line, read_buffer = read_buffer.split("\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            msg_obj = json.loads(line)
                            # Invoke registered callbacks (e.g. Flask-SocketIO emit)
                            for cb in self.message_callbacks:
                                try:
                                    cb(msg_obj)
                                except Exception as cb_err:
                                    print(f"[TCP CHAT BRIDGE] Callback error: {cb_err}")
                        except json.JSONDecodeError as err:
                            print(f"[TCP CHAT BRIDGE] Malformed frame: {err}")

            except (ConnectionRefusedError, OSError) as conn_err:
                # Chat server might not be running yet; retry after delay
                print(f"[TCP CHAT BRIDGE] Connection failed ({conn_err}). Retrying in 3 seconds...")
            finally:
                with self.lock:
                    if self.sock:
                        try:
                            self.sock.close()
                        except Exception:
                            pass
                    self.sock = None
                    self.connected = False

            time.sleep(3.0)

    def send_chat_message(self, message_data):
        """
        Transmits a JSON chat frame over the persistent TCP socket.
        Thread-safe write with mutex lock.
        """
        with self.lock:
            if not self.connected or not self.sock:
                # Attempt immediate connection to server
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(3.0)
                    s.connect((self.host, self.port))
                    s.settimeout(None)
                    self.sock = s
                    self.connected = True
                    print(f"[TCP CHAT BRIDGE] Immediate connection established to {self.host}:{self.port}")
                except Exception as conn_err:
                    return False, f"TCP Chat Server ({self.host}:{self.port}) is not reachable: {conn_err}"

            try:
                payload = (json.dumps(message_data) + "\n").encode("utf-8")
                self.sock.sendall(payload)
                add_bytes(len(payload))
                return True, "Dispatched to TCP Chat Server"
            except Exception as err:
                self.connected = False
                return False, f"Socket sendall error: {err}"


# Global singleton instance for the Flask application
chat_bridge = TCPChatBridge()


# ============================================================================
# 3. RELIABLE TCP FILE TRANSFER CLIENT (Port 2121)
# ============================================================================

def upload_file_tcp(file_stream, filename, filesize, sender="Student", sender_ip="192.168.66.130", progress_callback=None):
    """
    Transfers a file stream to server/tcp_file_server.py over a raw TCP socket on port 2121.
    Streams in 64 KB chunks, computes SHA-256 hash in real time, and verifies the server response.

    Parameters:
      - file_stream: readable binary stream (e.g., FileStorage.stream or open file object)
      - filename: base filename string
      - filesize: total file size in bytes
      - sender: sender display name
      - sender_ip: source workstation IP
      - progress_callback: optional callable(bytes_sent, total_bytes, percent, speed_kbps)

    Returns:
      (success: bool, result_or_error: dict or str)
    """
    safe_filename = os.path.basename(filename)
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client_socket.settimeout(20.0)

    try:
        add_event('TCP FILE TRANSFER', f"{sender} → LabNet Server\n{safe_filename}\n{filesize / 1024:.2f} KB\nCONNECTING")
        client_socket.connect((TCP_HOST, FILE_PORT))

        # First pass or pre-compute SHA-256 checksum
        sha256_hasher = hashlib.sha256()
        file_stream.seek(0)
        while chunk := file_stream.read(CHUNK_SIZE):
            sha256_hasher.update(chunk)
        expected_sha256 = sha256_hasher.hexdigest()
        file_stream.seek(0)

        # Step 1: Send metadata header
        metadata = {
            "action": "upload",
            "filename": safe_filename,
            "filesize": filesize,
            "sha256": expected_sha256,
            "sender": sender,
            "sender_ip": sender_ip
        }
        header_bytes = (json.dumps(metadata) + "\n").encode("utf-8")
        client_socket.sendall(header_bytes)
        add_bytes(len(header_bytes))

        # Step 2: Wait for READY handshake acknowledgment
        ack_buf = ""
        while "\n" not in ack_buf:
            c = client_socket.recv(1024)
            if not c:
                raise ConnectionError("File server closed connection before READY handshake.")
            ack_buf += c.decode("utf-8", errors="replace")

        ack_data = json.loads(ack_buf.strip())
        if ack_data.get("status") != "READY":
            raise RuntimeError(f"File server rejected transfer: {ack_data}")

        # Step 3: Stream file chunks over TCP socket
        bytes_sent = 0
        start_time = time.time()
        add_event('TCP FILE TRANSFER', f"{sender} → LabNet Server\n{safe_filename}\nSTREAMING")

        while bytes_sent < filesize:
            chunk = file_stream.read(CHUNK_SIZE)
            if not chunk:
                break
            client_socket.sendall(chunk)
            bytes_sent += len(chunk)
            add_bytes(len(chunk))

            # Trigger progress callback if registered
            if progress_callback:
                elapsed = max(time.time() - start_time, 0.001)
                speed_kbps = (bytes_sent / 1024) / elapsed
                percent = round((bytes_sent / filesize) * 100, 1) if filesize > 0 else 100.0
                progress_callback(bytes_sent, filesize, percent, round(speed_kbps, 2))

        # Step 4: Await server verification response
        final_buf = ""
        while "\n" not in final_buf:
            c = client_socket.recv(1024)
            if not c:
                break
            final_buf += c.decode("utf-8", errors="replace")

        final_resp = json.loads(final_buf.strip())
        elapsed_total = max(time.time() - start_time, 0.001)
        speed_final = (bytes_sent / 1024) / elapsed_total

        if final_resp.get("status") == "SUCCESS" and final_resp.get("verified", False):
            add_event('TCP FILE TRANSFER', f"{sender} → Server\n{safe_filename}\n{bytes_sent / 1024:.2f} KB\nCOMPLETED (SHA-256 MATCH)")
            final_resp["speed_kbps"] = round(speed_final, 2)
            final_resp["local_sha256"] = expected_sha256
            return True, final_resp
        else:
            add_event('TCP FILE ERROR', f"{sender} → Server\n{safe_filename}\nINTEGRITY MISMATCH")
            return False, f"Verification failed: {final_resp}"

    except Exception as err:
        add_event('TCP FILE ERROR', f"{sender} → Server\n{safe_filename}\nError: {str(err)}")
        return False, str(err)
    finally:
        client_socket.close()
