"""
LabNet Web UDP File Service (web/services/udp_file_service.py)
=============================================================
Provides a callable service layer for the Flask application to transfer files
to server/udp_file_server.py (Port 2122) over genuine UDP sockets using
sliding-window ARQ with progress callback integration for Socket.IO.
"""

import os
import socket
import json
import base64
import hashlib
import time
import uuid
import tempfile
from pathlib import Path
from dotenv import dotenv_values

root_dir = Path(__file__).resolve().parent.parent.parent
env_path = root_dir / '.env'
config = dotenv_values(env_path) if env_path.exists() else {}

UDP_HOST = config.get("SERVER_HOST", "127.0.0.1")
UDP_PORT = int(config.get("UDP_FILE_PORT", 2122))
CHUNK_SIZE = int(config.get("UDP_FILE_CHUNK_SIZE", 1024))
TIMEOUT = float(config.get("UDP_FILE_TIMEOUT", 1.0))
MAX_RETRIES = int(config.get("UDP_FILE_MAX_RETRIES", 5))
WINDOW_SIZE = int(config.get("UDP_FILE_WINDOW_SIZE", 5))


def upload_file_udp(file_stream, filename, filesize, sender="Student", sender_ip="192.168.66.130", progress_callback=None):
    """
    Transfers a file stream to server/udp_file_server.py over a raw UDP socket on port 2122.
    Streams in 1024-byte chunks, computes SHA-256 hash, handles ACKs and sliding-window retransmissions.

    Returns:
      (success: bool, result_or_error: dict or str)
    """
    safe_filename = os.path.basename(filename)

    # 1. Read all chunks and calculate SHA-256
    file_stream.seek(0)
    hasher = hashlib.sha256()
    chunks = []
    while chunk := file_stream.read(CHUNK_SIZE):
        chunks.append(chunk)
        hasher.update(chunk)

    expected_sha256 = hasher.hexdigest()
    total_chunks = len(chunks)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(TIMEOUT)

    transfer_id = str(uuid.uuid4())
    start_msg = {
        "action": "START",
        "transfer_id": transfer_id,
        "filename": safe_filename,
        "filesize": filesize,
        "sha256": expected_sha256,
        "total_chunks": total_chunks,
        "chunk_size": CHUNK_SIZE,
        "sender": sender
    }

    # 2. Handshake
    handshake_acked = False
    for attempt in range(MAX_RETRIES):
        sock.sendto(json.dumps(start_msg).encode("utf-8"), (UDP_HOST, UDP_PORT))
        try:
            resp_bytes, _ = sock.recvfrom(2048)
            resp = json.loads(resp_bytes.decode("utf-8"))
            if resp.get("action") == "START_ACK" and resp.get("status") == "READY":
                handshake_acked = True
                break
        except socket.timeout:
            continue

    if not handshake_acked:
        sock.close()
        return False, "UDP File Server (Port 2122) failed to acknowledge transfer handshake."

    # 3. Sliding-window transmission
    base = 0
    next_seq = 0
    acked = set()
    retries = {seq: 0 for seq in range(total_chunks)}
    total_retransmissions = 0
    start_time = time.time()
    timers = {}

    while base < total_chunks:
        # Transmit within window
        while next_seq < min(base + WINDOW_SIZE, total_chunks):
            if next_seq not in acked:
                b64_data = base64.b64encode(chunks[next_seq]).decode("ascii")
                pkt = {
                    "action": "DATA",
                    "transfer_id": transfer_id,
                    "seq": next_seq,
                    "total_chunks": total_chunks,
                    "data": b64_data
                }
                sock.sendto(json.dumps(pkt).encode("utf-8"), (UDP_HOST, UDP_PORT))
                timers[next_seq] = time.time()
            next_seq += 1

        # Check for ACKs
        try:
            sock.settimeout(0.05)
            resp_bytes, _ = sock.recvfrom(2048)
            ack_data = json.loads(resp_bytes.decode("utf-8"))

            if ack_data.get("action") == "ACK" and ack_data.get("transfer_id") == transfer_id:
                ack_seq = int(ack_data.get("seq", -1))
                if 0 <= ack_seq < total_chunks and ack_seq not in acked:
                    acked.add(ack_seq)
                    if ack_seq in timers:
                        del timers[ack_seq]

                    while base in acked:
                        base += 1

                    if progress_callback:
                        elapsed = max(time.time() - start_time, 0.001)
                        sent_bytes = sum(len(chunks[s]) for s in acked)
                        pct = round((len(acked) / total_chunks) * 100.0, 1) if total_chunks > 0 else 100.0
                        speed_kbps = round((sent_bytes / 1024.0) / elapsed, 2)
                        progress_callback(sent_bytes, filesize, pct, speed_kbps, total_retransmissions)
        except socket.timeout:
            pass

        # Timeout retransmissions
        now = time.time()
        for seq in range(base, min(base + WINDOW_SIZE, total_chunks)):
            if seq not in acked and seq in timers:
                if now - timers[seq] > TIMEOUT:
                    retries[seq] += 1
                    total_retransmissions += 1
                    if retries[seq] > MAX_RETRIES:
                        sock.close()
                        return False, f"UDP packet #{seq} exceeded max retries. Transfer aborted."

                    b64_data = base64.b64encode(chunks[seq]).decode("ascii")
                    pkt = {
                        "action": "DATA",
                        "transfer_id": transfer_id,
                        "seq": seq,
                        "total_chunks": total_chunks,
                        "data": b64_data
                    }
                    sock.sendto(json.dumps(pkt).encode("utf-8"), (UDP_HOST, UDP_PORT))
                    timers[seq] = time.time()

    # 4. Wait for FIN_ACK
    sock.settimeout(5.0)
    fin_receipt = None
    for _ in range(MAX_RETRIES):
        try:
            resp_bytes, _ = sock.recvfrom(4096)
            resp = json.loads(resp_bytes.decode("utf-8"))
            if resp.get("action") == "FIN_ACK" and resp.get("transfer_id") == transfer_id:
                fin_receipt = resp
                break
        except socket.timeout:
            pass

    sock.close()

    if not fin_receipt:
        return False, "Server did not return transfer completion receipt."

    fin_receipt["local_sha256"] = expected_sha256
    fin_receipt["total_retransmissions"] = total_retransmissions
    return True, fin_receipt
