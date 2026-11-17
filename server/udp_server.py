"""
LabNet UDP Echo & Heartbeat Monitoring Server (server/udp_server.py)
===================================================================
Listens on UDP port 6000. Handles general datagram echo and periodic
heartbeats from distributed client workstations.
Maintains device presence, computes packet latency, detects offline timeouts,
and persists heartbeat records, device statuses, and network events to Supabase.
"""

import socket
import time
import os
import sys
from datetime import datetime
from pathlib import Path
from dotenv import dotenv_values

root_dir = Path(__file__).resolve().parent.parent
env_path = root_dir / ".env"
config = dotenv_values(env_path) if env_path.exists() else {}

PORT = int(os.getenv("UDP_PORT") or config.get("UDP_PORT", 6000))
HEARTBEAT_TIMEOUT = float(os.getenv("HEARTBEAT_TIMEOUT") or config.get("HEARTBEAT_TIMEOUT", 10.0))

# In-memory device registry
clients = {}

# Import Supabase repositories if available
sys.path.insert(0, str(root_dir))
try:
    from web.database.repositories.device_repository import (
        register_device,
        update_device_status,
        get_device_by_name
    )
    from web.database.repositories.heartbeat_repository import record_heartbeat
    from web.database.repositories.event_repository import record_event
    from web.database.supabase_client import get_supabase_admin
    _db_available = True
except Exception as e:
    _db_available = False
    print(f"[UDP SERVER] Database integration disabled: {e}")

_cached_default_user_id = None


def _get_default_user_id():
    global _cached_default_user_id
    if _cached_default_user_id:
        return _cached_default_user_id
    if not _db_available:
        return None
    try:
        admin = get_supabase_admin()
        if admin:
            res = admin.table("profiles").select("id").limit(1).execute()
            if res.data:
                _cached_default_user_id = res.data[0]["id"]
                return _cached_default_user_id
    except Exception as err:
        print(f"[UDP DB WARN] Could not resolve default profile user_id: {err}")
    return None


def check_client_status():
    current_time = time.time()
    for client_id, info in list(clients.items()):
        if current_time - info["last_seen"] > HEARTBEAT_TIMEOUT:
            if info["status"] != "OFFLINE":
                info["status"] = "OFFLINE"
                print(f"[OFFLINE] {client_id} ({info['ip']}) - Timeout exceeded ({HEARTBEAT_TIMEOUT}s)")

                if _db_available:
                    try:
                        dev_id = info.get("device_id")
                        if not dev_id:
                            dev = get_device_by_name(client_id)
                            dev_id = dev["id"] if dev else None
                        if dev_id:
                            update_device_status(dev_id, "offline", update_last_seen=False)
                            record_event(
                                event_type="timeout",
                                protocol="UDP",
                                source_ip=info["ip"],
                                device_id=dev_id,
                                details={
                                    "reason": "Heartbeat timeout",
                                    "client_id": client_id,
                                    "timeout_seconds": HEARTBEAT_TIMEOUT
                                }
                            )
                    except Exception as err:
                        print(f"[UDP DB ERROR] Offline logging failed for {client_id}: {err}")


def main():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        server_socket.bind(("0.0.0.0", PORT))
    except Exception as e:
        print(f"[FATAL] UDP Server failed to bind to 0.0.0.0:{PORT}: {e}")
        sys.exit(1)

    print("==================================================")
    print(f"   LABNET UDP HEARTBEAT & ECHO SERVER (PORT {PORT}) ")
    print("==================================================")
    print(f"[*] Bound to: 0.0.0.0:{PORT}")
    print(f"[*] Heartbeat Timeout: {HEARTBEAT_TIMEOUT} seconds")
    print(f"[*] Supabase Integration: {'ENABLED' if _db_available else 'DISABLED'}")
    print("[*] Listening for incoming datagrams...\n")

    while True:
        server_socket.settimeout(1.0)
        try:
            data, address = server_socket.recvfrom(2048)
            message = data.decode(errors="replace").strip()
            client_ip = address[0]

            parts = message.split("|")
            msg_type = parts[0]

            if msg_type == "HEARTBEAT":
                client_id = parts[1] if len(parts) > 1 and parts[1].strip() else f"node-{client_ip}"
                client_timestamp_str = parts[2] if len(parts) > 2 else ""

                now = time.time()
                readable_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # Calculate transit latency safely (guard against VM clock skew > 10s)
                latency_ms = 1.0
                if client_timestamp_str:
                    try:
                        if client_timestamp_str.replace(".", "", 1).isdigit():
                            t_sent = float(client_timestamp_str)
                            diff_ms = (now - t_sent) * 1000.0
                            if 0.0 <= diff_ms <= 10000.0:
                                latency_ms = max(round(diff_ms, 2), 0.1)
                            else:
                                latency_ms = 1.2
                        else:
                            t_obj = datetime.strptime(client_timestamp_str, "%Y-%m-%d %H:%M:%S")
                            diff_ms = (now - t_obj.timestamp()) * 1000.0
                            if 0.0 <= diff_ms <= 10000.0:
                                latency_ms = max(round(diff_ms, 2), 0.1)
                    except Exception:
                        latency_ms = 1.0

                was_new = client_id not in clients
                was_offline = (client_id in clients and clients[client_id]["status"] == "OFFLINE")

                prev_dev_id = clients.get(client_id, {}).get("device_id")
                clients[client_id] = {
                    "ip": client_ip,
                    "last_seen": now,
                    "last_seen_readable": readable_time,
                    "status": "ONLINE",
                    "latency_ms": latency_ms,
                    "device_id": prev_dev_id
                }

                print(f"[HEARTBEAT] {client_id} ({client_ip}) is ONLINE [Latency: {latency_ms:.2f}ms]")

                # Send ACK immediately before any DB I/O so client RTT stays accurate
                response = f"HEARTBEAT_ACK|{client_id}|{client_timestamp_str}|{readable_time}"
                server_socket.sendto(response.encode(), address)

                # Persist to Supabase
                if _db_available:
                    try:
                        user_id = _get_default_user_id()
                        if user_id:
                            ok, dev = register_device(
                                user_id=user_id,
                                device_name=client_id,
                                ip_address=client_ip,
                                status="online"
                            )
                            if ok and isinstance(dev, dict) and "id" in dev:
                                dev_id = dev["id"]
                                clients[client_id]["device_id"] = dev_id
                                record_heartbeat(
                                    device_id=dev_id,
                                    latency_ms=latency_ms,
                                    source_ip=client_ip
                                )
                                if was_new or was_offline:
                                    record_event(
                                        event_type="heartbeat",
                                        protocol="UDP",
                                        source_ip=client_ip,
                                        device_id=dev_id,
                                        details={
                                            "status": "recovered" if was_offline else "connected",
                                            "client_id": client_id,
                                            "latency_ms": latency_ms
                                        }
                                    )
                        else:
                            print(f"[UDP DB WARN] No profile user_id found; skipping device persistence for {client_id}")
                    except Exception as db_err:
                        print(f"[UDP DB ERROR] Failed to persist heartbeat for {client_id}: {db_err}")

            else:
                print(f"[UDP ECHO] {address} -> {message}")
                response = f"UDP_RECEIVED|{message}"
                server_socket.sendto(response.encode(), address)

        except socket.timeout:
            pass
        except Exception as err:
            print(f"[UDP ERROR] {err}")

        check_client_status()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[UDP SERVER] Shutting down cleanly.")
