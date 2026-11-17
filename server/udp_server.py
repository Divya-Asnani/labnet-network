"""
LabNet UDP Echo & Heartbeat Monitoring Server (server/udp_server.py)
===================================================================
Listens on UDP port 6000. Handles general datagram echo and periodic
heartbeats from distributed client workstations.
Maintains device presence, computes packet latency, detects offline timeouts,
and persists heartbeat records and network events to Supabase.
"""

import socket
import time
import os
import sys
from datetime import datetime
from pathlib import Path
from dotenv import dotenv_values

# Load configuration
root_dir = Path(__file__).resolve().parent.parent
env_path = root_dir / '.env'
config = dotenv_values(env_path) if env_path.exists() else {}

HOST = config.get("SERVER_HOST", "0.0.0.0")
PORT = int(config.get("UDP_PORT", 6000))
HEARTBEAT_TIMEOUT = float(config.get("HEARTBEAT_TIMEOUT", 10.0))

# In-memory device registry
clients = {}

# Import Supabase repositories if available
sys.path.insert(0, str(root_dir))
try:
    from web.database.repositories.device_repository import register_device, update_device_status, get_device_by_name
    from web.database.repositories.heartbeat_repository import record_heartbeat
    from web.database.repositories.event_repository import record_event
    _db_available = True
except Exception as e:
    _db_available = False
    print(f"[UDP SERVER] Database integration disabled: {e}")


def check_client_status():
    current_time = time.time()
    for client_id, info in list(clients.items()):
        if current_time - info["last_seen"] > HEARTBEAT_TIMEOUT:
            if info["status"] != "OFFLINE":
                info["status"] = "OFFLINE"
                print(f"[OFFLINE] {client_id} ({info['ip']}) - Timeout exceeded ({HEARTBEAT_TIMEOUT}s)")

                # Persist offline status to Supabase
                if _db_available:
                    try:
                        dev = get_device_by_name(client_id)
                        if dev:
                            update_device_status(dev['id'], 'offline')
                            record_event(
                                event_type='disconnect',
                                protocol='UDP',
                                source_ip=info['ip'],
                                device_id=dev['id'],
                                details={
                                    'reason': 'Heartbeat timeout',
                                    'client_id': client_id,
                                    'timeout_seconds': HEARTBEAT_TIMEOUT
                                }
                            )
                    except Exception as err:
                        print(f"[UDP DB ERROR] Offline logging failed: {err}")


def main():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        server_socket.bind(("0.0.0.0", PORT))
    except Exception as e:
        print(f"[FATAL] UDP Server failed to bind to 0.0.0.0:{PORT}: {e}")
        sys.exit(1)

    print(f"==================================================")
    print(f"   LABNET UDP HEARTBEAT & ECHO SERVER (PORT {PORT}) ")
    print(f"==================================================")
    print(f"[*] Bound to: 0.0.0.0:{PORT}")
    print(f"[*] Heartbeat Timeout: {HEARTBEAT_TIMEOUT} seconds")
    print(f"[*] Supabase Integration: {'ENABLED' if _db_available else 'DISABLED'}")
    print(f"[*] Listening for incoming datagrams...\n")

    while True:
        server_socket.settimeout(1.0)
        try:
            data, address = server_socket.recvfrom(2048)
            message = data.decode(errors='replace').strip()
            client_ip = address[0]

            parts = message.split("|")
            msg_type = parts[0]

            if msg_type == "HEARTBEAT":
                client_id = parts[1] if len(parts) > 1 else f"node-{address[0]}"
                client_timestamp_str = parts[2] if len(parts) > 2 else ""

                now = time.time()
                readable_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # Calculate latency if timestamp provided
                latency_ms = None
                if client_timestamp_str:
                    try:
                        # Try parsing float or iso
                        if '.' in client_timestamp_str and client_timestamp_str.replace('.', '', 1).isdigit():
                            t_sent = float(client_timestamp_str)
                            latency_ms = max((now - t_sent) * 1000.0, 0.1)
                        else:
                            t_obj = datetime.strptime(client_timestamp_str, "%Y-%m-%d %H:%M:%S")
                            latency_ms = max((now - t_obj.timestamp()) * 1000.0, 0.1)
                    except Exception:
                        latency_ms = 1.0

                was_offline = (client_id in clients and clients[client_id]["status"] == "OFFLINE")

                clients[client_id] = {
                    "ip": client_ip,
                    "last_seen": now,
                    "last_seen_readable": readable_time,
                    "status": "ONLINE",
                    "latency_ms": round(latency_ms, 2) if latency_ms else 1.0
                }

                lat_str = f" [Latency: {latency_ms:.2f}ms]" if latency_ms else ""
                print(f"[HEARTBEAT] {client_id} ({client_ip}) is ONLINE{lat_str}")

                # Send ACK back with timing info
                response = f"HEARTBEAT_ACK|{client_id}|{client_timestamp_str}|{readable_time}"
                server_socket.sendto(response.encode(), address)

                # Persist to Supabase
                if _db_available:
                    try:
                        # System admin default user id placeholder or existing profile
                        from web.database.supabase_client import get_supabase_admin
                        client_adm = get_supabase_admin()
                        user_id = None
                        if client_adm:
                            p_res = client_adm.table('profiles').select('id').limit(1).execute()
                            if p_res.data:
                                user_id = p_res.data[0]['id']

                        if user_id:
                            success, dev = register_device(
                                user_id=user_id,
                                device_name=client_id,
                                ip_address=client_ip,
                                status='online'
                            )
                            if success and isinstance(dev, dict) and 'id' in dev:
                                record_heartbeat(
                                    device_id=dev['id'],
                                    latency_ms=latency_ms,
                                    source_ip=client_ip
                                )
                                if was_offline:
                                    record_event(
                                        event_type='heartbeat',
                                        protocol='UDP',
                                        source_ip=client_ip,
                                        device_id=dev['id'],
                                        details={'status': 'recovered', 'client_id': client_id}
                                    )
                    except Exception as db_err:
                        pass

            else:
                # Echo general UDP message
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
