"""
LabNet Reliable UDP File Transfer Client (clients/udp_file_client.py)
=====================================================================
Streams files to server/udp_file_server.py (Port 2122) using a reliable
Sliding-Window ARQ protocol with packet sequencing, timeout retransmissions,
dynamic progress reporting, SHA-256 verification, and optional simulated packet loss.

Usage:
  python3 clients/udp_file_client.py <filepath> [--server IP] [--port 2122]
                                     [--window 5] [--timeout 1.0] [--loss-rate 0.0]
"""

import socket
import json
import base64
import hashlib
import os
import sys
import time
import uuid
import random
import argparse
from pathlib import Path

CHUNK_SIZE = 1024  # 1 KB chunk size to fit standard MTU


def upload_file_udp_client(filepath, server_ip="192.168.66.128", server_port=2122, window_size=5, timeout=1.0, max_retries=5, loss_rate=0.0, sender="Student-CLI", progress_callback=None):
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    filename = os.path.basename(filepath)
    filesize = os.path.getsize(filepath)

    print("==================================================")
    print("      LABNET RELIABLE UDP FILE SENDER (ARQ)       ")
    print("==================================================")
    print(f"[*] File:            {filename} ({filesize} bytes)")
    print(f"[*] Target:          {server_ip}:{server_port}")
    print(f"[*] Window Size:     {window_size}")
    print(f"[*] Packet Timeout:  {timeout}s (Max Retries: {max_retries})")
    if loss_rate > 0.0:
        print(f"[*] SIMULATED LOSS:  {loss_rate * 100:.1f}% packet drop rate")
    print("--------------------------------------------------")

    # 1. Pre-read chunks and compute SHA-256
    print("[*] Computing cryptographic SHA-256 checksum...")
    hasher = hashlib.sha256()
    chunks = []
    with open(filepath, "rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            chunks.append(chunk)
            hasher.update(chunk)

    file_sha256 = hasher.hexdigest()
    total_chunks = len(chunks)
    print(f"[*] SHA-256 Checksum: {file_sha256}")
    print(f"[*] Total Chunks:     {total_chunks} ({CHUNK_SIZE} bytes/chunk)")

    # 2. Handshake START
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)

    transfer_id = str(uuid.uuid4())
    start_msg = {
        "action": "START",
        "transfer_id": transfer_id,
        "filename": filename,
        "filesize": filesize,
        "sha256": file_sha256,
        "total_chunks": total_chunks,
        "chunk_size": CHUNK_SIZE,
        "sender": sender
    }

    print("[*] Sending handshake (START) to server...")
    handshake_acked = False
    for attempt in range(max_retries):
        sock.sendto(json.dumps(start_msg).encode("utf-8"), (server_ip, server_port))
        try:
            resp_bytes, _ = sock.recvfrom(2048)
            resp = json.loads(resp_bytes.decode("utf-8"))
            if resp.get("action") == "START_ACK" and resp.get("status") == "READY":
                handshake_acked = True
                print("[+] Handshake ACK received. Server is READY.")
                break
        except socket.timeout:
            print(f"[-] Handshake timeout (attempt {attempt + 1}/{max_retries}). Retrying...")

    if not handshake_acked:
        sock.close()
        raise ConnectionError("Server failed to acknowledge transfer handshake.")

    # 3. Sliding-Window Transmission
    base = 0
    next_seq = 0
    acked = set()
    retries = {seq: 0 for seq in range(total_chunks)}
    total_retransmissions = 0
    start_time = time.time()
    timers = {}

    print("[*] Starting sliding-window stream...")

    while base < total_chunks:
        # Transmit packets within current window
        while next_seq < min(base + window_size, total_chunks):
            if next_seq not in acked:
                # Controlled packet loss simulation
                if loss_rate > 0.0 and random.random() < loss_rate:
                    print(f"\n[PACKET LOSS SIMULATION] Dropped chunk #{next_seq}")
                else:
                    b64_data = base64.b64encode(chunks[next_seq]).decode("ascii")
                    pkt = {
                        "action": "DATA",
                        "transfer_id": transfer_id,
                        "seq": next_seq,
                        "total_chunks": total_chunks,
                        "data": b64_data
                    }
                    sock.sendto(json.dumps(pkt).encode("utf-8"), (server_ip, server_port))
                timers[next_seq] = time.time()
            next_seq += 1

        # Listen for ACKs or check for timeouts
        try:
            sock.settimeout(0.1)
            resp_bytes, _ = sock.recvfrom(2048)
            ack_data = json.loads(resp_bytes.decode("utf-8"))

            if ack_data.get("action") == "ACK" and ack_data.get("transfer_id") == transfer_id:
                ack_seq = int(ack_data.get("seq", -1))
                if 0 <= ack_seq < total_chunks and ack_seq not in acked:
                    acked.add(ack_seq)
                    if ack_seq in timers:
                        del timers[ack_seq]

                    # Slide window base
                    while base in acked:
                        base += 1

                    # Progress reporting
                    elapsed = max(time.time() - start_time, 0.001)
                    sent_bytes = sum(len(chunks[s]) for s in acked)
                    pct = (len(acked) / total_chunks) * 100.0 if total_chunks > 0 else 100.0
                    speed_kbps = (sent_bytes / 1024.0) / elapsed
                    bar = "█" * int(pct // 5) + "░" * (20 - int(pct // 5))
                    print(f"\r[{bar}] {pct:.1f}% ({sent_bytes}/{filesize} bytes) | Speed: {speed_kbps:.2f} KB/s | Retries: {total_retransmissions}", end="", flush=True)

                    if progress_callback:
                        progress_callback(sent_bytes, filesize, pct, round(speed_kbps, 2), total_retransmissions)

            elif ack_data.get("action") == "FIN_ACK" and ack_data.get("transfer_id") == transfer_id:
                # Early or final confirmation
                pass

        except socket.timeout:
            pass

        # Check for unacknowledged packet timeouts within the window
        now = time.time()
        for seq in range(base, min(base + window_size, total_chunks)):
            if seq not in acked and seq in timers:
                if now - timers[seq] > timeout:
                    retries[seq] += 1
                    total_retransmissions += 1
                    if retries[seq] > max_retries:
                        sock.close()
                        raise TimeoutError(f"Packet #{seq} exceeded maximum retransmissions ({max_retries}). Transfer aborted.")

                    # Retransmit timed-out chunk
                    b64_data = base64.b64encode(chunks[seq]).decode("ascii")
                    pkt = {
                        "action": "DATA",
                        "transfer_id": transfer_id,
                        "seq": seq,
                        "total_chunks": total_chunks,
                        "data": b64_data
                    }
                    sock.sendto(json.dumps(pkt).encode("utf-8"), (server_ip, server_port))
                    timers[seq] = time.time()

    # 4. Wait for FIN_ACK verification receipt
    print("\n[*] Waiting for server integrity verification (FIN_ACK)...")
    sock.settimeout(5.0)
    fin_receipt = None
    for _ in range(max_retries):
        try:
            resp_bytes, _ = sock.recvfrom(4096)
            resp = json.loads(resp_bytes.decode("utf-8"))
            if resp.get("action") == "FIN_ACK" and resp.get("transfer_id") == transfer_id:
                fin_receipt = resp
                break
        except socket.timeout:
            # Resend last chunk to trigger FIN_ACK
            pass

    sock.close()

    if not fin_receipt:
        raise TimeoutError("Server did not return transfer verification receipt.")

    elapsed_total = max(time.time() - start_time, 0.001)
    speed_final = (filesize / 1024.0) / elapsed_total

    print("==================================================")
    print("        UDP TRANSFER VERIFICATION RECEIPT         ")
    print("==================================================")
    print(f"  Status:          {fin_receipt.get('status')}")
    print(f"  Integrity:       {'VERIFIED (SHA-256 MATCH)' if fin_receipt.get('verified') else 'FAILED'}")
    print(f"  Local SHA-256:   {file_sha256}")
    print(f"  Server SHA-256:  {fin_receipt.get('sha256')}")
    print(f"  Remote File:     {fin_receipt.get('save_path')}")
    print(f"  Total Retries:   {total_retransmissions}")
    print(f"  Transfer Speed:  {speed_final:.2f} KB/s ({elapsed_total:.2f}s)")
    print("==================================================")

    fin_receipt['local_sha256'] = file_sha256
    fin_receipt['speed_kbps'] = round(speed_final, 2)
    fin_receipt['total_retransmissions'] = total_retransmissions
    return fin_receipt


def main():
    parser = argparse.ArgumentParser(description="LabNet Reliable UDP File Transfer Client")
    parser.add_argument("filepath", help="Path to file to upload")
    parser.add_argument("--server", default="192.168.66.128", help="Target server IP (default: 192.168.66.128)")
    parser.add_argument("--port", type=int, default=2122, help="Target UDP file port (default: 2122)")
    parser.add_argument("--window", type=int, default=5, help="Sliding window size (default: 5)")
    parser.add_argument("--timeout", type=float, default=1.0, help="Packet ACK timeout in seconds (default: 1.0)")
    parser.add_argument("--retries", type=int, default=5, help="Max retries per chunk (default: 5)")
    parser.add_argument("--loss-rate", type=float, default=0.0, help="Simulated packet drop probability (0.0 - 0.5)")
    parser.add_argument("--user", default="Student-CLI", help="Sender identifier")

    args = parser.parse_args()

    try:
        upload_file_udp_client(
            filepath=args.filepath,
            server_ip=args.server,
            server_port=args.port,
            window_size=args.window,
            timeout=args.timeout,
            max_retries=args.retries,
            loss_rate=args.loss_rate,
            sender=args.user
        )
    except Exception as e:
        print(f"\n[-] UDP Transfer failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
