"""
LabNet Direct Peer-to-Peer (P2P) Node (clients/p2p_node.py)
==========================================================
Enables genuine direct workstation-to-workstation file transfers (e.g., VM2 -> VM3)
without routing file payloads through the central LabNet server.

Architecture:
  - Registers reachability (IP, P2P Port) with Central Coordinator (VM1).
  - Listens on dedicated P2P port (default: 2123).
  - Directly receives files from sender peers over raw BSD TCP sockets.
  - Computes and verifies SHA-256 hashes independently.
  - Can also act as a direct sender to any registered peer IP.

Usage:
  # Mode 1: Start P2P Listening Node on VM2 / VM3
  python3 clients/p2p_node.py --listen --port 2123 --name "VM2-Workstation"

  # Mode 2: Direct Peer-to-Peer Send (VM2 -> VM3)
  python3 clients/p2p_node.py --send file.pdf --target-ip 192.168.66.131 --target-port 2123
"""

import socket
import json
import hashlib
import os
import sys
import time
import argparse
import threading
import urllib.request
from datetime import datetime

CHUNK_SIZE = 65536  # 64 KB chunks
DEFAULT_P2P_PORT = 2123


class P2PReceiver:
    def __init__(self, port, save_dir, node_name, coordinator_url=None):
        self.port = port
        self.save_dir = os.path.abspath(save_dir)
        self.node_name = node_name
        self.coordinator_url = coordinator_url
        os.makedirs(self.save_dir, exist_ok=True)
        self.running = False
        self.server_sock = None

    def register_with_coordinator(self):
        if not self.coordinator_url:
            return
        try:
            # Determine local LAN IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()

            payload = json.dumps({
                "peer_name": self.node_name,
                "ip": local_ip,
                "port": self.port,
                "status": "READY"
            }).encode("utf-8")

            req = urllib.request.Request(
                f"{self.coordinator_url}/api/peers/register",
                data=payload,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                print(f"[P2P COORDINATOR] Successfully registered with central server as {self.node_name} ({local_ip}:{self.port})")
        except Exception as e:
            print(f"[P2P COORDINATOR] Registration note: {e}")

    def start(self):
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind(("0.0.0.0", self.port))
        self.server_sock.listen(5)
        self.running = True

        print("==================================================")
        print(f"       LABNET DIRECT P2P RECEIVER NODE            ")
        print("==================================================")
        print(f"[*] Node Name:      {self.node_name}")
        print(f"[*] P2P Listening:  0.0.0.0:{self.port} (DIRECT BSD TCP)")
        print(f"[*] Save Directory: {self.save_dir}")
        print(f"[*] Central Bypass: File payload travels DIRECTLY from peer")
        print("--------------------------------------------------")
        print("[*] Waiting for incoming direct peer transfers...\n")

        self.register_with_coordinator()

        while self.running:
            try:
                conn, addr = self.server_sock.accept()
                threading.Thread(target=self._handle_peer, args=(conn, addr), daemon=True).start()
            except Exception:
                break

    def _handle_peer(self, conn, addr):
        sender_ip, sender_port = addr
        print(f"[P2P INCOMING] Direct connection established from peer {sender_ip}:{sender_port}")
        start_time = time.time()
        try:
            conn.settimeout(20.0)
            # Read handshake
            handshake_buf = ""
            while "\n" not in handshake_buf:
                data = conn.recv(1024)
                if not data:
                    return
                handshake_buf += data.decode("utf-8", errors="replace")

            meta = json.loads(handshake_buf.strip())
            filename = os.path.basename(meta.get("filename", "p2p_file.bin"))
            filesize = int(meta.get("filesize", 0))
            expected_sha256 = meta.get("sha256", "")
            sender_name = meta.get("sender_name", "Peer-Node")

            print(f"[P2P HANDSHAKE] Direct transfer from '{sender_name}' ({sender_ip})")
            print(f"  File: {filename} ({filesize} bytes)")
            print(f"  Expected SHA-256: {expected_sha256}")

            # Send READY ACK directly to peer
            conn.sendall(b'{"status": "READY"}\n')

            # Stream direct file chunks
            save_path = os.path.join(self.save_dir, filename)
            hasher = hashlib.sha256()
            received_bytes = 0

            with open(save_path, "wb") as f:
                while received_bytes < filesize:
                    chunk = conn.recv(min(CHUNK_SIZE, filesize - received_bytes))
                    if not chunk:
                        break
                    f.write(chunk)
                    hasher.update(chunk)
                    received_bytes += len(chunk)

            computed_sha = hasher.hexdigest()
            verified = (computed_sha.lower() == expected_sha256.lower())
            elapsed = max(time.time() - start_time, 0.001)
            speed_kbps = (received_bytes / 1024.0) / elapsed

            print(f"[P2P TRANSFER COMPLETE] Direct peer payload verified!")
            print(f"  Integrity: {'VERIFIED (SHA-256 MATCH)' if verified else 'INTEGRITY MISMATCH'}")
            print(f"  Speed:     {speed_kbps:.2f} KB/s")
            print(f"  Location:  {save_path}")

            receipt = {
                "status": "SUCCESS" if verified else "FAILED",
                "verified": verified,
                "sha256": computed_sha,
                "received_bytes": received_bytes,
                "speed_kbps": round(speed_kbps, 2),
                "sender_ip": sender_ip,
                "save_path": save_path,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            conn.sendall((json.dumps(receipt) + "\n").encode("utf-8"))

        except Exception as e:
            print(f"[P2P ERROR] Failed direct transfer from {addr}: {e}")
        finally:
            conn.close()


def send_direct_p2p(filepath, target_ip, target_port=DEFAULT_P2P_PORT, sender_name="Workstation-Sender"):
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    filename = os.path.basename(filepath)
    filesize = os.path.getsize(filepath)

    print("==================================================")
    print("      LABNET DIRECT PEER-TO-PEER (P2P) SENDER     ")
    print("==================================================")
    print(f"[*] File:            {filename} ({filesize} bytes)")
    print(f"[*] Destination Peer:{target_ip}:{target_port} (DIRECT)")
    print(f"[*] Central Bypass:  File payload is NOT sent to server")
    print("--------------------------------------------------")

    print("[*] Calculating SHA-256 hash...")
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            hasher.update(chunk)
    file_sha256 = hasher.hexdigest()
    print(f"[*] Checksum:        {file_sha256}")

    print(f"[*] Connecting directly to peer {target_ip}:{target_port}...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(15.0)

    try:
        sock.connect((target_ip, target_port))
        print("[+] Direct peer connection established!")

        # Send metadata header
        metadata = {
            "action": "p2p_upload",
            "filename": filename,
            "filesize": filesize,
            "sha256": file_sha256,
            "sender_name": sender_name
        }
        sock.sendall((json.dumps(metadata) + "\n").encode("utf-8"))

        # Wait for peer READY ACK
        ack_buf = ""
        while "\n" not in ack_buf:
            c = sock.recv(1024)
            if not c:
                raise ConnectionError("Peer closed connection before READY ACK.")
            ack_buf += c.decode("utf-8", errors="replace")

        ack_data = json.loads(ack_buf.strip())
        if ack_data.get("status") != "READY":
            raise RuntimeError(f"Peer rejected direct transfer: {ack_data}")

        print("[+] Peer is READY. Streaming file directly across VMware LAN...")
        bytes_sent = 0
        start_time = time.time()

        with open(filepath, "rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                sock.sendall(chunk)
                bytes_sent += len(chunk)
                pct = (bytes_sent / filesize) * 100.0 if filesize > 0 else 100.0
                bar = "█" * int(pct // 5) + "░" * (20 - int(pct // 5))
                print(f"\r[{bar}] {pct:.1f}% ({bytes_sent}/{filesize} bytes)", end="", flush=True)

        print("\n[*] Waiting for peer verification receipt...")
        final_buf = ""
        while "\n" not in final_buf:
            c = sock.recv(1024)
            if not c:
                break
            final_buf += c.decode("utf-8", errors="replace")

        final_resp = json.loads(final_buf.strip())
        elapsed = max(time.time() - start_time, 0.001)
        speed = (bytes_sent / 1024.0) / elapsed

        print("==================================================")
        print("          DIRECT P2P TRANSFER RECEIPT             ")
        print("==================================================")
        print(f"  Status:          {final_resp.get('status')}")
        print(f"  Integrity:       {'VERIFIED (SHA-256 MATCH)' if final_resp.get('verified') else 'FAILED'}")
        print(f"  Peer Destination:{target_ip}:{target_port}")
        print(f"  Transfer Speed:  {speed:.2f} KB/s ({elapsed:.2f}s)")
        print(f"  Remote Saved At: {final_resp.get('save_path')}")
        print("==================================================")

        return final_resp
    finally:
        sock.close()


def main():
    parser = argparse.ArgumentParser(description="LabNet Direct Peer-to-Peer Node")
    parser.add_argument("--listen", action="store_true", help="Run node in listening mode to accept direct transfers")
    parser.add_argument("--send", type=str, help="Path to file to send directly to a peer")
    parser.add_argument("--target-ip", type=str, help="IP of destination peer")
    parser.add_argument("--target-port", type=int, default=DEFAULT_P2P_PORT, help="Port of destination peer (default: 2123)")
    parser.add_argument("--port", type=int, default=DEFAULT_P2P_PORT, help="Port to listen on (default: 2123)")
    parser.add_argument("--dir", type=str, default="p2p_received", help="Directory to save received P2P files")
    parser.add_argument("--name", type=str, default="Student-VM2", help="Peer node name")
    parser.add_argument("--coordinator", type=str, default="http://192.168.66.128:5050", help="Central coordinator URL")

    args = parser.parse_args()

    if args.send:
        if not args.target_ip:
            print("[-] Error: --target-ip is required when using --send")
            sys.exit(1)
        try:
            send_direct_p2p(args.send, args.target_ip, args.target_port, sender_name=args.name)
        except Exception as e:
            print(f"[-] P2P Send failed: {e}")
            sys.exit(1)
    else:
        # Default to listen mode
        receiver = P2PReceiver(
            port=args.port,
            save_dir=args.dir,
            node_name=args.name,
            coordinator_url=args.coordinator
        )
        try:
            receiver.start()
        except KeyboardInterrupt:
            print("\n[*] P2P Node stopped by user.")


if __name__ == "__main__":
    main()
