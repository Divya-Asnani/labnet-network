"""
LabNet Reliable UDP File Transfer Server (server/udp_file_server.py)
===================================================================
Listens on UDP port 2122. Implements a reliable Selective-Repeat /
Sliding-Window ARQ protocol with packet sequencing, out-of-order assembly,
duplicate detection, SHA-256 cryptographic verification, and Supabase logging.
"""

import socket
import json
import base64
import hashlib
import os
import sys
import time
import random
from datetime import datetime
from pathlib import Path
from dotenv import dotenv_values

# Load configuration
root_dir = Path(__file__).resolve().parent.parent
env_path = root_dir / '.env'
config = dotenv_values(env_path) if env_path.exists() else {}

PORT = int(config.get("UDP_FILE_PORT", 2122))
UPLOAD_FOLDER = os.path.join(str(root_dir), config.get("UPLOAD_FOLDER", "uploads"))
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Supabase database integration
sys.path.insert(0, str(root_dir))
try:
    from web.database.repositories.file_repository import record_file_transfer, update_transfer_status
    from web.database.repositories.event_repository import record_event
    from web.database.supabase_client import get_supabase_admin
    _db_available = True
except Exception:
    _db_available = False

# Active transfers: {transfer_id: TransferState}
active_transfers = {}


class TransferState:
    def __init__(self, transfer_id, filename, filesize, expected_sha256, total_chunks, chunk_size, sender, sender_addr):
        self.transfer_id = transfer_id
        self.filename = os.path.basename(filename)
        self.filesize = filesize
        self.expected_sha256 = expected_sha256
        self.total_chunks = total_chunks
        self.chunk_size = chunk_size
        self.sender = sender
        self.sender_addr = sender_addr
        self.chunks = {}  # {seq: bytes}
        self.start_time = time.time()
        self.last_activity = time.time()
        self.retransmissions_handled = 0
        self.completed = False
        self.db_transfer_id = None


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        sock.bind(("0.0.0.0", PORT))
    except Exception as e:
        print(f"[FATAL] Failed to bind UDP File Server on 0.0.0.0:{PORT}: {e}")
        sys.exit(1)

    print("==================================================")
    print(f"   LABNET RELIABLE UDP FILE SERVER (PORT {PORT})   ")
    print("==================================================")
    print(f"[*] Bound to: 0.0.0.0:{PORT}")
    print(f"[*] Upload Directory: {UPLOAD_FOLDER}")
    print(f"[*] Protocol: Reliable ARQ (Selective ACK / Sequencing)")
    print(f"[*] Supabase Integration: {'ENABLED' if _db_available else 'DISABLED'}")
    print(f"[*] Listening for UDP file transfer streams...\n")

    sock.settimeout(0.5)

    while True:
        try:
            raw_data, client_addr = sock.recvfrom(65535)
            self_loss = float(os.getenv("TEST_UDP_SERVER_LOSS", "0.0"))
            if self_loss > 0 and random.random() < self_loss:
                print(f"[PACKET LOSS SIMULATION] Dropped incoming packet from {client_addr}")
                continue

            try:
                msg = json.loads(raw_data.decode("utf-8"))
            except Exception:
                continue

            action = msg.get("action")
            transfer_id = msg.get("transfer_id")

            if action == "START":
                filename = os.path.basename(msg.get("filename", "unnamed.bin"))
                filesize = int(msg.get("filesize", 0))
                expected_sha = msg.get("sha256", "")
                total_chunks = int(msg.get("total_chunks", 0))
                chunk_sz = int(msg.get("chunk_size", 1024))
                sender = msg.get("sender", "Workstation")

                state = TransferState(
                    transfer_id, filename, filesize, expected_sha,
                    total_chunks, chunk_sz, sender, client_addr
                )
                active_transfers[transfer_id] = state

                print(f"[UDP START] Handshake: '{filename}' ({filesize} bytes, {total_chunks} chunks) from {client_addr}")

                # Record in Supabase if available
                if _db_available:
                    try:
                        admin_client = get_supabase_admin()
                        user_id = None
                        if admin_client:
                            p_res = admin_client.table('profiles').select('id').limit(1).execute()
                            if p_res.data:
                                user_id = p_res.data[0]['id']

                        if user_id:
                            success, row = record_file_transfer(
                                sender_user_id=user_id,
                                file_name=filename,
                                file_size=filesize,
                                protocol='UDP',
                                checksum=expected_sha,
                                storage_path=os.path.join(UPLOAD_FOLDER, filename),
                                status='in_progress'
                            )
                            if success and isinstance(row, dict) and 'id' in row:
                                state.db_transfer_id = row['id']
                    except Exception as db_err:
                        print(f"[UDP DB ERROR] {db_err}")

                ack_msg = {
                    "action": "START_ACK",
                    "transfer_id": transfer_id,
                    "status": "READY",
                    "chunk_size": chunk_sz
                }
                sock.sendto(json.dumps(ack_msg).encode("utf-8"), client_addr)

            elif action == "DATA":
                if transfer_id not in active_transfers:
                    err_msg = {"action": "ERROR", "transfer_id": transfer_id, "message": "Unknown transfer ID"}
                    sock.sendto(json.dumps(err_msg).encode("utf-8"), client_addr)
                    continue

                state = active_transfers[transfer_id]
                state.last_activity = time.time()
                seq = int(msg.get("seq", -1))
                b64_payload = msg.get("data", "")
                chunk_bytes = base64.b64decode(b64_payload.encode("ascii"))

                # Duplicate check
                if seq in state.chunks:
                    state.retransmissions_handled += 1
                else:
                    state.chunks[seq] = chunk_bytes

                # Send Selective ACK for this packet
                ack_resp = {
                    "action": "ACK",
                    "transfer_id": transfer_id,
                    "seq": seq,
                    "received_count": len(state.chunks),
                    "total_chunks": state.total_chunks
                }
                sock.sendto(json.dumps(ack_resp).encode("utf-8"), client_addr)

                # Check if all chunks have arrived
                if len(state.chunks) == state.total_chunks and not state.completed:
                    state.completed = True
                    elapsed = max(time.time() - state.start_time, 0.001)
                    speed_kbps = (state.filesize / 1024.0) / elapsed

                    # Reassemble in exact sequential order
                    print(f"[*] All {state.total_chunks} chunks received for '{state.filename}'. Reassembling...")
                    hasher = hashlib.sha256()
                    save_path = os.path.join(UPLOAD_FOLDER, state.filename)

                    with open(save_path, "wb") as out_file:
                        for s in range(state.total_chunks):
                            data_chunk = state.chunks.get(s, b"")
                            out_file.write(data_chunk)
                            hasher.update(data_chunk)

                    computed_sha = hasher.hexdigest()
                    is_verified = (computed_sha.lower() == state.expected_sha256.lower())

                    print(f"==================================================")
                    print(f"[+] UDP TRANSFER COMPLETE: {state.filename}")
                    print(f"    Size:            {state.filesize} bytes")
                    print(f"    Expected SHA256: {state.expected_sha256}")
                    print(f"    Computed SHA256: {computed_sha}")
                    print(f"    Integrity Match: {is_verified}")
                    print(f"    Transfer Speed:  {speed_kbps:.2f} KB/s")
                    print(f"    Retransmissions: {state.retransmissions_handled}")
                    print(f"==================================================")

                    # Update Supabase
                    if _db_available and state.db_transfer_id:
                        try:
                            status_str = 'completed' if is_verified else 'failed'
                            update_transfer_status(
                                transfer_id=state.db_transfer_id,
                                status=status_str,
                                checksum=computed_sha,
                                storage_path=save_path
                            )
                            record_event(
                                event_type='transfer',
                                protocol='UDP',
                                source_ip=state.sender_addr[0],
                                details={
                                    'filename': state.filename,
                                    'filesize': state.filesize,
                                    'verified': is_verified,
                                    'speed_kbps': round(speed_kbps, 2),
                                    'retransmissions': state.retransmissions_handled
                                }
                            )
                        except Exception as db_err:
                            print(f"[UDP DB UPDATE ERROR] {db_err}")

                    fin_resp = {
                        "action": "FIN_ACK",
                        "transfer_id": transfer_id,
                        "status": "SUCCESS" if is_verified else "FAILED",
                        "verified": is_verified,
                        "sha256": computed_sha,
                        "filesize": state.filesize,
                        "speed_kbps": round(speed_kbps, 2),
                        "save_path": save_path,
                        "retransmissions": state.retransmissions_handled,
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    sock.sendto(json.dumps(fin_resp).encode("utf-8"), client_addr)

        except socket.timeout:
            # Clean up stale incomplete transfers (> 60s idle)
            now = time.time()
            for tid, st in list(active_transfers.items()):
                if not st.completed and now - st.last_activity > 60.0:
                    print(f"[-] Stale transfer timed out: {st.filename} ({tid})")
                    if _db_available and st.db_transfer_id:
                        update_transfer_status(st.db_transfer_id, 'failed')
                    del active_transfers[tid]
        except Exception as err:
            print(f"[UDP FILE SERVER ERROR] {err}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[UDP FILE SERVER] Shutting down cleanly.")
