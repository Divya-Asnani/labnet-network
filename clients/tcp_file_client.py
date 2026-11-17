"""
LabNet CLI TCP File Client (clients/tcp_file_client.py)
=======================================================
A command-line TCP file transfer client that streams files to
server/tcp_file_server.py on port 2121.

Usage:
  python3 tcp_file_client.py <filepath> [server_ip] [server_port] [sender_name]

Example:
  python3 tcp_file_client.py test.pdf 192.168.66.128 2121 Student
"""

import socket
import json
import hashlib
import os
import sys
import time

CHUNK_SIZE = 65536  # 64 KB


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 tcp_file_client.py <filepath> [server_ip] [server_port] [sender_name]")
        sys.exit(1)

    file_path = sys.argv[1]
    if not os.path.isfile(file_path):
        print(f"[-] Error: File not found: {file_path}")
        sys.exit(1)

    server_ip = sys.argv[2] if len(sys.argv) > 2 else "192.168.66.128"
    server_port = int(sys.argv[3]) if len(sys.argv) > 3 else 2121
    sender_name = sys.argv[4] if len(sys.argv) > 4 else "CLI_Client"

    filename = os.path.basename(file_path)
    filesize = os.path.getsize(file_path)

    print("==================================================")
    print("        LABNET TCP FILE TRANSFER CLIENT           ")
    print("==================================================")
    print(f"[*] File:       {filename} ({filesize} bytes)")
    print(f"[*] Target:     {server_ip}:{server_port}")
    print(f"[*] Sender:     {sender_name}")
    print("--------------------------------------------------")

    # Compute SHA-256 before transfer
    print("[*] Computing SHA-256 checksum...")
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            hasher.update(chunk)
    file_sha256 = hasher.hexdigest()
    print(f"[*] Checksum:   {file_sha256}")

    # Connect to TCP file server
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((server_ip, server_port))
        print("[+] Connected to file server.")

        # Send metadata header
        metadata = {
            "action": "upload",
            "filename": filename,
            "filesize": filesize,
            "sha256": file_sha256,
            "sender": sender_name
        }
        sock.sendall((json.dumps(metadata) + "\n").encode("utf-8"))

        # Wait for READY acknowledgment
        resp_buf = ""
        while "\n" not in resp_buf:
            c = sock.recv(1024)
            if not c:
                raise ConnectionError("Server closed connection unexpectedly during handshake.")
            resp_buf += c.decode("utf-8", errors="replace")

        ready_data = json.loads(resp_buf.strip())
        if ready_data.get("status") != "READY":
            raise RuntimeError(f"Server rejected transfer: {ready_data}")

        print("[+] Server READY. Streaming file in chunks...")
        start_time = time.time()
        bytes_sent = 0

        with open(file_path, "rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                sock.sendall(chunk)
                bytes_sent += len(chunk)
                pct = (bytes_sent / filesize) * 100
                bar = "█" * int(pct // 5) + "░" * (20 - int(pct // 5))
                print(f"\r[{bar}] {pct:.1f}% ({bytes_sent}/{filesize} bytes)", end="", flush=True)

        print("\n[*] Waiting for server integrity verification...")
        final_buf = ""
        while "\n" not in final_buf:
            c = sock.recv(1024)
            if not c:
                break
            final_buf += c.decode("utf-8", errors="replace")

        final_resp = json.loads(final_buf.strip())
        elapsed = max(time.time() - start_time, 0.001)
        speed = (bytes_sent / 1024) / elapsed

        print("--------------------------------------------------")
        print(f"[+] Status:     {final_resp.get('status')}")
        print(f"[+] Verified:   {final_resp.get('verified')}")
        print(f"[+] Speed:      {speed:.2f} KB/s in {elapsed:.2f}s")
        print(f"[+] Server SHA: {final_resp.get('sha256')}")
        print(f"[+] Remote Dir: {final_resp.get('save_path')}")
        print("==================================================")

    except Exception as err:
        print(f"\n[-] Transfer failed: {err}")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
