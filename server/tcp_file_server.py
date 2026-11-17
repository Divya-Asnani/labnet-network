"""
LabNet TCP File Server (server/tcp_file_server.py)
==================================================
A multithreaded TCP file transfer server operating on port 2121.

Networking Concepts Explained:
------------------------------
1. Stream Transport (TCP): Unlike datagrams (UDP), TCP delivers a reliable,
   in-order, error-checked stream of octets. It handles packet retransmissions,
   sequence numbers, and acknowledgments transparently.
2. Chunked Transfer: Transferring large files in chunks (e.g. 64 KB) prevents
   saturating memory buffers and enables streaming arbitrarily large files.
3. Framing & Metadata Protocol:
   - Client first sends metadata (filename, size, expected SHA-256) as newline-delimited JSON.
   - Server validates, ensures directory safety, and responds with a READY frame.
   - Client streams the raw binary payload.
   - Server computes SHA-256 in real-time as chunks arrive and verifies integrity.
4. Security: Path traversal attacks (e.g., ../../etc/passwd) are mitigated using
   os.path.basename() and sanitizing paths inside a dedicated uploads/ folder.
"""

import socket
import threading
import json
import hashlib
import os
import time
from datetime import datetime

HOST = "0.0.0.0"
PORT = 2121
CHUNK_SIZE = 65536  # 64 KB chunks
UPLOAD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../uploads"))

# Ensure uploads directory exists
os.makedirs(UPLOAD_DIR, exist_ok=True)


def get_timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def handle_file_client(client_socket, client_address):
    """
    Handles a single TCP file transfer session from a client.
    """
    client_str = f"{client_address[0]}:{client_address[1]}"
    print(f"\n[{get_timestamp()}] [+] [TRANSFER CONNECT] Connection from {client_str}")

    try:
        # Step 1: Read metadata header (newline-delimited JSON)
        client_socket.settimeout(15.0)
        header_buffer = ""
        while "\n" not in header_buffer:
            chunk = client_socket.recv(1024)
            if not chunk:
                print(f"[{get_timestamp()}] [-] Connection dropped before metadata from {client_str}")
                return
            header_buffer += chunk.decode("utf-8", errors="replace")

        header_line, remaining_buffer = header_buffer.split("\n", 1)
        metadata = json.loads(header_line.strip())

        raw_filename = metadata.get("filename", f"received_{int(time.time())}.bin")
        safe_filename = os.path.basename(raw_filename).replace(" ", "_")
        filesize = int(metadata.get("filesize", 0))
        expected_sha256 = metadata.get("sha256", "").lower()
        sender_id = metadata.get("sender", "Unknown")

        print(f"[{get_timestamp()}] [METADATA] File: '{safe_filename}' | Size: {filesize} bytes | Sender: {sender_id}")

        # Destination file path
        destination_path = os.path.join(UPLOAD_DIR, safe_filename)

        # Step 2: Send READY acknowledgment to client
        ack_msg = json.dumps({"status": "READY", "message": "Server ready to receive stream"}) + "\n"
        client_socket.sendall(ack_msg.encode("utf-8"))

        # Step 3: Stream and write file chunks to disk while calculating SHA-256
        sha256_hasher = hashlib.sha256()
        bytes_received = 0
        start_time = time.time()

        with open(destination_path, "wb") as out_file:
            # First write any leftover binary data that was read with the header
            if remaining_buffer:
                leftover_bytes = remaining_buffer.encode("utf-8")
                # Note: if header was sent with raw bytes, slice properly
                # To keep it clean, client sends header then waits for READY ack.
                pass

            while bytes_received < filesize:
                remaining = filesize - bytes_received
                to_read = min(CHUNK_SIZE, remaining)
                data = client_socket.recv(to_read)
                if not data:
                    print(f"[{get_timestamp()}] [!] Premature EOF at {bytes_received}/{filesize} bytes.")
                    break

                out_file.write(data)
                sha256_hasher.update(data)
                bytes_received += len(data)

                # Periodic progress logging
                if filesize > 0 and (bytes_received == filesize or bytes_received % (1024 * 1024) == 0):
                    pct = (bytes_received / filesize) * 100
                    print(f"[{get_timestamp()}] [STREAM] Received {bytes_received}/{filesize} bytes ({pct:.1f}%)")

        elapsed_time = max(time.time() - start_time, 0.001)
        speed_kbps = (bytes_received / 1024) / elapsed_time
        computed_sha256 = sha256_hasher.hexdigest().lower()

        # Step 4: Verify checksum integrity
        integrity_ok = (expected_sha256 == computed_sha256) if expected_sha256 else True
        status_text = "SUCCESS" if (bytes_received == filesize and integrity_ok) else "FAILED"

        print(f"[{get_timestamp()}] [RESULT] Status: {status_text} | Transferred: {bytes_received} bytes in {elapsed_time:.2f}s ({speed_kbps:.2f} KB/s)")
        print(f"[{get_timestamp()}] [SHA256] Computed: {computed_sha256}")
        if expected_sha256:
            print(f"[{get_timestamp()}] [SHA256] Expected: {expected_sha256} | Verified: {integrity_ok}")

        # Step 5: Send final response frame to client
        result_payload = {
            "status": status_text,
            "filename": safe_filename,
            "filesize": bytes_received,
            "sha256": computed_sha256,
            "verified": integrity_ok,
            "speed_kbps": round(speed_kbps, 2),
            "save_path": destination_path,
            "timestamp": get_timestamp()
        }
        client_socket.sendall((json.dumps(result_payload) + "\n").encode("utf-8"))

    except socket.timeout:
        print(f"[{get_timestamp()}] [ERROR] Transfer timed out for {client_str}")
    except Exception as err:
        print(f"[{get_timestamp()}] [ERROR] File transfer error with {client_str}: {err}")
    finally:
        client_socket.close()
        print(f"[{get_timestamp()}] [-] [TRANSFER CLOSED] Closed connection from {client_str}")


def main():
    bound = False
    for attempt in range(8):
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass

        try:
            server_socket.bind((HOST, PORT))
            bound = True
            break
        except OSError as err:
            server_socket.close()
            if attempt < 7:
                time.sleep(0.8)
            else:
                print(f"[FATAL] Cannot bind file server to {HOST}:{PORT}: {err}")
                return

    server_socket.listen(10)

    print("==================================================")
    print("      LABNET RELIABLE TCP FILE SERVER             ")
    print("==================================================")
    print(f"[*] Port:        {PORT}")
    print(f"[*] Storage Dir: {UPLOAD_DIR}")
    print(f"[*] Chunk Size:  {CHUNK_SIZE // 1024} KB")
    print(f"[*] Status:      Listening for incoming file streams...")
    print("==================================================\n")

    try:
        while True:
            client_socket, client_address = server_socket.accept()
            worker = threading.Thread(
                target=handle_file_client,
                args=(client_socket, client_address),
                daemon=True
            )
            worker.start()
    except KeyboardInterrupt:
        print(f"\n[{get_timestamp()}] [STOP] File server stopped.")
    finally:
        server_socket.close()


if __name__ == "__main__":
    main()
