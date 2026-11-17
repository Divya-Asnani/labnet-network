import subprocess
import threading
import time
import re
import os
import datetime
import shutil
from pathlib import Path
from dotenv import dotenv_values

root_dir = Path(__file__).resolve().parent.parent.parent
env_path = root_dir / ".env"
config = dotenv_values(env_path) if env_path.exists() else {}

SERVER_IP = config.get("SERVER_HOST", "192.168.66.128")
HEARTBEAT_TIMEOUT = float(config.get("HEARTBEAT_TIMEOUT", 10.0))

events = []
peers_registry = {}  # {peer_name: {"peer_name", "ip", "port", "status", "last_seen"}}

stats = {
    "server_status": "ONLINE",
    "tcp_connections": 0,
    "udp_messages": 0,
    "bytes_transferred": 0,
    "last_heartbeat": "-",
    "server_ip": SERVER_IP,
    "heartbeat_timeout": HEARTBEAT_TIMEOUT,
    "tcp_chat_server_active": False,
    "tcp_file_server_active": False,
    "udp_file_server_active": False,
    "udp_heartbeat_server_active": False,
    "smtp_server_active": False,
    "pop3_server_active": False
}

# Supabase repositories
try:
    from web.database.repositories.device_repository import list_devices, enforce_heartbeat_timeouts
    from web.database.repositories.event_repository import list_events, record_event
    from web.database.repositories.session_repository import list_sessions
    from web.database.repositories.file_repository import list_transfers
    from web.database.repositories.chat_repository import count_messages
    from web.database.repositories.heartbeat_repository import list_heartbeats
    _db_available = True
except Exception as e:
    _db_available = False
    print(f"[MONITORING] Supabase repositories unavailable: {e}")


def add_event(protocol_title, detail_msg, event_type=None, source_ip=None, destination_ip=None, device_id=None):
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    ev = {
        "timestamp": timestamp,
        "title": protocol_title,
        "detail": detail_msg
    }
    events.insert(0, ev)
    if len(events) > 50:
        events.pop()

    if _db_available:
        try:
            proto = protocol_title.split()[0].upper()
            if proto not in ["TCP", "UDP", "ICMP", "HTTP", "DNS", "SMTP", "POP3"]:
                proto = "TCP"
            ev_type = event_type or ("connection" if "CONNECT" in detail_msg.upper() else "transfer")
            record_event(
                event_type=ev_type,
                protocol=proto,
                source_ip=source_ip,
                destination_ip=destination_ip,
                device_id=device_id,
                details={"title": protocol_title, "message": detail_msg}
            )
        except Exception:
            pass


def add_bytes(num):
    stats["bytes_transferred"] += num
    stats["tcp_connections"] += 1


def _get_listening_ports_passive():
    """
    Passively inspects kernel listening TCP and UDP ports via `ss -tuln`.
    CRITICAL: Never opens active TCP connections to port 5001/2121 so the TCP chat
    server is never spammed with synthetic connect/disconnect events.
    Returns: (tcp_ports_set, udp_ports_set)
    """
    tcp_ports = set()
    udp_ports = set()
    try:
        res = subprocess.run(["ss", "-tuln"], capture_output=True, text=True, timeout=3)
        for line in res.stdout.splitlines():
            parts = line.split()
            if len(parts) < 5:
                continue
            proto = parts[0].lower()
            local_addr = parts[4]
            if ":" in local_addr:
                port_str = local_addr.rsplit(":", 1)[-1]
                if port_str.isdigit():
                    port_num = int(port_str)
                    if proto.startswith("tcp"):
                        tcp_ports.add(port_num)
                    elif proto.startswith("udp"):
                        udp_ports.add(port_num)
    except Exception:
        pass
    return tcp_ports, udp_ports


def monitor_servers():
    """Passively monitors LabNet service ports and enforces device heartbeat timeouts."""
    while True:
        try:
            tcp_ports, udp_ports = _get_listening_ports_passive()
            stats["tcp_chat_server_active"] = 5001 in tcp_ports
            stats["tcp_file_server_active"] = 2121 in tcp_ports
            stats["udp_file_server_active"] = 2122 in udp_ports
            stats["udp_heartbeat_server_active"] = 6000 in udp_ports
            stats["smtp_server_active"] = 1025 in tcp_ports
            stats["pop3_server_active"] = 1100 in tcp_ports

            # Enforce real heartbeat timeout in Supabase so stale devices go offline automatically
            if _db_available:
                enforce_heartbeat_timeouts(HEARTBEAT_TIMEOUT)
        except Exception as e:
            print(f"[MONITORING LOOP WARN] {e}")
        time.sleep(5.0)


# ============================================================================
# PEER REGISTRY FOR DIRECT PEER-TO-PEER (P2P) TRANSFERS
# ============================================================================

def register_peer_node(peer_name: str, ip: str, port: int = 2123, status: str = "READY"):
    peers_registry[peer_name] = {
        "peer_name": peer_name,
        "ip": ip,
        "port": int(port),
        "status": status,
        "last_seen": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    add_event(
        "P2P PEER REGISTERED",
        f"Peer node '{peer_name}' registered at {ip}:{port} ({status})",
        event_type="connection",
        source_ip=ip
    )
    return peers_registry[peer_name]


def get_registered_peers():
    return list(peers_registry.values())


# ============================================================================
# STATUS, STATS & REAL DATABASE METRICS
# ============================================================================

def start_monitoring():
    """Starts the passive port monitor and heartbeat timeout reaper thread."""
    threading.Thread(target=monitor_servers, daemon=True).start()


def get_network_status():
    """
    Returns genuine registered devices from Supabase `devices` table after enforcing
    live heartbeat timeout rules. Never fabricates hardcoded online workstations.
    """
    if not _db_available:
        return []
    try:
        enforce_heartbeat_timeouts(HEARTBEAT_TIMEOUT)
        db_devs = list_devices()
        # Fetch most recent heartbeat latency per device
        recent_hbs = list_heartbeats(limit=100)
        latest_latency = {}
        for hb in recent_hbs:
            did = hb.get("device_id")
            if did and did not in latest_latency:
                latest_latency[did] = hb.get("latency_ms")

        result = []
        for d in db_devs:
            prof = d.get("profiles") or {}
            did = d.get("id")
            result.append({
                "id": did,
                "device_name": d.get("device_name"),
                "hostname": d.get("hostname") or "-",
                "ip": d.get("ip_address") or "-",
                "os": d.get("os") or "Ubuntu Linux",
                "user_name": prof.get("display_name") or "Unknown",
                "user_role": prof.get("role") or "student",
                "status": (d.get("status") or "offline").upper(),
                "last_seen": d.get("last_seen_at") or "-",
                "created_at": d.get("created_at") or "-",
                "latency_ms": latest_latency.get(did)
            })
        return result
    except Exception as e:
        print(f"[MONITORING ERROR: get_network_status] {e}")
        return []


def get_events(limit=40, event_type=None, protocol=None):
    if _db_available:
        try:
            db_evs = list_events(limit=limit, event_type=event_type, protocol=protocol)
            res = []
            for e in db_evs:
                details = e.get("details") or {}
                if isinstance(details, dict):
                    msg = details.get("message") or details.get("reason") or details.get("action") or str(details)
                else:
                    msg = str(details)
                dev = e.get("devices") or {}
                res.append({
                    "id": e.get("id"),
                    "timestamp": e.get("created_at") or "",
                    "event_type": e.get("event_type") or "connection",
                    "protocol": e.get("protocol") or "NET",
                    "source_ip": e.get("source_ip") or "-",
                    "destination_ip": e.get("destination_ip") or "-",
                    "device_name": dev.get("device_name") or "-",
                    "title": f"{e.get('protocol') or 'NET'} {(e.get('event_type') or '').upper()}",
                    "detail": msg,
                    "details": details
                })
            return res
        except Exception as e:
            print(f"[MONITORING ERROR: get_events] {e}")
    return events


def get_stats():
    devs = get_network_status()
    online_count = len([d for d in devs if d["status"] == "ONLINE"])
    offline_count = len([d for d in devs if d["status"] != "ONLINE"])
    stats["total_devices"] = len(devs)
    stats["active_clients"] = online_count
    stats["online_devices"] = online_count
    stats["offline_devices"] = offline_count

    if _db_available:
        try:
            hbs = list_heartbeats(limit=1)
            if hbs:
                stats["last_heartbeat"] = hbs[0].get("received_at") or "-"
        except Exception:
            pass
    return stats


def get_dashboard_overview():
    """
    Aggregates real backend/database metrics for the Admin & Overview Dashboard.
    Zero hardcoded sample counts.
    """
    devices = get_network_status()
    online_devices = [d for d in devices if d["status"] == "ONLINE"]
    offline_devices = [d for d in devices if d["status"] != "ONLINE"]

    transfers = list_transfers(limit=100) if _db_available else []
    successful_transfers = [t for t in transfers if t.get("status") == "completed"]
    failed_transfers = [t for t in transfers if t.get("status") == "failed"]

    sessions = list_sessions(limit=50) if _db_available else []
    active_sessions = [s for s in sessions if s.get("status") == "active"]

    total_messages = count_messages() if _db_available else 0
    recent_events = get_events(limit=15)
    recent_heartbeats = list_heartbeats(limit=20) if _db_available else []

    # Protocol activity breakdown from real events & transfers
    protocol_counts = {"TCP": 0, "UDP": 0, "HTTP": 0, "DNS": 0, "SMTP": 0, "POP3": 0, "ICMP": 0}
    for ev in recent_events:
        p = (ev.get("protocol") or "").upper()
        if p in protocol_counts:
            protocol_counts[p] += 1
    for tr in transfers:
        p = (tr.get("protocol") or "").upper()
        if p in protocol_counts:
            protocol_counts[p] += 1

    return {
        "counts": {
            "total_devices": len(devices),
            "online_devices": len(online_devices),
            "offline_devices": len(offline_devices),
            "active_sessions": len(active_sessions),
            "total_messages": total_messages,
            "total_transfers": len(transfers),
            "successful_transfers": len(successful_transfers),
            "failed_transfers": len(failed_transfers),
            "registered_peers": len(peers_registry)
        },
        "services": {
            "tcp_chat_5001": stats["tcp_chat_server_active"],
            "tcp_file_2121": stats["tcp_file_server_active"],
            "udp_file_2122": stats["udp_file_server_active"],
            "udp_heartbeat_6000": stats["udp_heartbeat_server_active"],
            "smtp_1025": stats["smtp_server_active"],
            "pop3_1100": stats["pop3_server_active"]
        },
        "protocol_activity": protocol_counts,
        "recent_devices": devices[:10],
        "recent_transfers": transfers[:8],
        "recent_events": recent_events[:10],
        "recent_heartbeats": recent_heartbeats[:10],
        "server_ip": SERVER_IP,
        "heartbeat_timeout": HEARTBEAT_TIMEOUT
    }


def run_diagnostic(command, target=None):
    """
    Safely executes system networking diagnostic tools using subprocess argument lists.
    Returns structured dict: {command, target, output, exit_code, duration_ms}.
    Guards strictly against shell injection.
    """
    t0 = time.time()
    try:
        if target:
            target = target.strip()
            if not re.match(r"^[a-zA-Z0-9\.\-:]+$", target):
                return {
                    "command": command,
                    "target": target,
                    "output": "Invalid target host format. Only alphanumeric characters, dots, colons, and hyphens are allowed.",
                    "exit_code": 1,
                    "duration_ms": 0.0
                }
        else:
            target = SERVER_IP

        cmd_args = None
        if command == "ping":
            cmd_args = ["ping", "-c", "4", "-W", "2", target]
        elif command == "traceroute":
            traceroute_bin = shutil.which("traceroute") or "/usr/sbin/traceroute"
            if os.path.exists(traceroute_bin):
                cmd_args = [traceroute_bin, "-m", "10", "-w", "2", target]
            else:
                tracepath_bin = shutil.which("tracepath")
                if tracepath_bin:
                    cmd_args = [tracepath_bin, "-m", "10", target]
                else:
                    return {
                        "command": "traceroute",
                        "target": target,
                        "output": "Neither traceroute nor tracepath is installed on this host. Install with: sudo apt install traceroute",
                        "exit_code": 127,
                        "duration_ms": round((time.time() - t0) * 1000.0, 2)
                    }
        elif command in ["routes", "routing_table"]:
            cmd_args = ["ip", "route"]
        elif command in ["interfaces", "ip_addr"]:
            cmd_args = ["ip", "-br", "addr"]
        elif command in ["sockets", "listening_ports"]:
            cmd_args = ["ss", "-tuln"]
        elif command == "tcp_connections":
            cmd_args = ["ss", "-t", "-n"]
        elif command == "udp_status":
            cmd_args = ["ss", "-u", "-n", "-a"]
        else:
            return {
                "command": command,
                "target": target,
                "output": f"Unsupported diagnostic command '{command}'.",
                "exit_code": 1,
                "duration_ms": 0.0
            }

        res = subprocess.run(cmd_args, capture_output=True, text=True, timeout=15)
        duration_ms = round((time.time() - t0) * 1000.0, 2)
        output = (res.stdout or "") + (("\n" + res.stderr) if res.stderr else "")

        if _db_available:
            record_event(
                event_type="diagnostic",
                protocol="ICMP" if command in ["ping", "traceroute"] else "TCP",
                destination_ip=target if re.match(r"^\d+\.\d+\.\d+\.\d+$", target) else None,
                details={
                    "command": " ".join(cmd_args),
                    "exit_code": res.returncode,
                    "duration_ms": duration_ms
                }
            )

        return {
            "command": " ".join(cmd_args),
            "target": target,
            "output": output.strip() or "(no output)",
            "exit_code": res.returncode,
            "duration_ms": duration_ms
        }
    except subprocess.TimeoutExpired:
        return {
            "command": command,
            "target": target or SERVER_IP,
            "output": f"Diagnostic command '{command}' timed out after 15 seconds.",
            "exit_code": 124,
            "duration_ms": round((time.time() - t0) * 1000.0, 2)
        }
    except Exception as e:
        return {
            "command": command,
            "target": target or SERVER_IP,
            "output": f"Error executing diagnostic: {str(e)}",
            "exit_code": 1,
            "duration_ms": round((time.time() - t0) * 1000.0, 2)
        }
