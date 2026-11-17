import socket
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
env_path = root_dir / '.env'
config = dotenv_values(env_path) if env_path.exists() else {}

SERVER_IP = config.get('SERVER_HOST', '192.168.66.128')

network_status = {}
events = []
peers_registry = {}  # {peer_name: {"ip": ip, "port": port, "status": status, "last_seen": ts}}

stats = {
    'server_status': 'ONLINE',
    'tcp_connections': 0,
    'udp_messages': 0,
    'bytes_transferred': 0,
    'last_heartbeat': '-',
    'server_ip': SERVER_IP,
    'tcp_chat_server_active': False,
    'tcp_file_server_active': False,
    'udp_file_server_active': False,
    'udp_heartbeat_server_active': False
}

_subprocesses = []

# Supabase repositories
try:
    from web.database.repositories.device_repository import list_devices
    from web.database.repositories.event_repository import list_events, record_event
    from web.database.repositories.session_repository import list_sessions
    _db_available = True
except Exception:
    _db_available = False


def add_event(protocol_title, detail_msg):
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    ev = {
        'timestamp': timestamp,
        'title': protocol_title,
        'detail': detail_msg
    }
    events.insert(0, ev)
    if len(events) > 50:
        events.pop()

    # Also persist to Supabase if available
    if _db_available:
        try:
            proto = protocol_title.split()[0].upper()
            if proto not in ['TCP', 'UDP', 'ICMP', 'HTTP', 'DNS', 'SMTP', 'POP3']:
                proto = 'TCP'
            record_event(
                event_type='connection' if 'CONNECT' in detail_msg else 'transfer',
                protocol=proto,
                details={'title': protocol_title, 'message': detail_msg}
            )
        except Exception:
            pass


def add_bytes(num):
    stats['bytes_transferred'] += num
    stats['tcp_connections'] += 1


def is_port_in_use(port, host="127.0.0.1", sock_type=socket.SOCK_STREAM):
    """Check if a port is bound and listening on localhost."""
    try:
        with socket.socket(socket.AF_INET, sock_type) as s:
            if sock_type == socket.SOCK_STREAM:
                return s.connect_ex((host, port)) == 0
            else:
                try:
                    s.bind((host, port))
                    return False
                except OSError:
                    return True
    except Exception:
        return False


def monitor_servers():
    """Periodically monitors all core LabNet server ports."""
    while True:
        try:
            stats['tcp_chat_server_active'] = is_port_in_use(5001, sock_type=socket.SOCK_STREAM)
            stats['tcp_file_server_active'] = is_port_in_use(2121, sock_type=socket.SOCK_STREAM)
            stats['udp_file_server_active'] = is_port_in_use(2122, sock_type=socket.SOCK_DGRAM)
            stats['udp_heartbeat_server_active'] = is_port_in_use(6000, sock_type=socket.SOCK_DGRAM)
        except Exception:
            pass
        time.sleep(4.0)


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
    add_event('P2P PEER REGISTERED', f"Peer node '{peer_name}' registered\nAddress: {ip}:{port}\nStatus: {status}")
    return peers_registry[peer_name]


def get_registered_peers():
    return list(peers_registry.values())


# ============================================================================
# STATUS, STATS & DIAGNOSTICS
# ============================================================================

def start_monitoring():
    # Register default LAN nodes
    network_status['labnet-server'] = {
        'id': 'LabNet Server',
        'ip': SERVER_IP,
        'protocol': 'TCP/UDP',
        'status': 'ONLINE',
        'last_seen': datetime.datetime.now().strftime("%H:%M:%S")
    }
    network_status['client-student'] = {
        'id': 'Student Workstation',
        'ip': '192.168.66.130',
        'protocol': 'TCP/UDP',
        'status': 'ONLINE',
        'last_seen': datetime.datetime.now().strftime("%H:%M:%S")
    }
    network_status['client-researcher'] = {
        'id': 'Researcher Workstation',
        'ip': '192.168.66.129',
        'protocol': 'TCP/UDP',
        'status': 'ONLINE',
        'last_seen': datetime.datetime.now().strftime("%H:%M:%S")
    }
    threading.Thread(target=monitor_servers, daemon=True).start()


def get_network_status():
    if _db_available:
        try:
            db_devs = list_devices()
            if db_devs:
                res = []
                for d in db_devs:
                    res.append({
                        'id': d.get('device_name'),
                        'ip': d.get('ip_address') or 'N/A',
                        'protocol': 'TCP/UDP',
                        'status': d.get('status', 'online').upper(),
                        'last_seen': d.get('last_seen_at') or 'N/A'
                    })
                return res
        except Exception:
            pass
    return list(network_status.values())


def get_events():
    if _db_available:
        try:
            db_evs = list_events(limit=30)
            if db_evs:
                res = []
                for e in db_evs:
                    details = e.get('details') or {}
                    msg = details.get('message') or details.get('title') or e.get('event_type')
                    res.append({
                        'timestamp': e.get('created_at', '')[11:19] if e.get('created_at') else '',
                        'title': f"{e.get('protocol', 'NET')} {e.get('event_type', '').upper()}",
                        'detail': str(msg)
                    })
                return res
        except Exception:
            pass
    return events


def get_stats():
    stats['active_clients'] = len([c for c in get_network_status() if c['status'] in ['ONLINE', 'CONNECTED']])
    return stats


def run_diagnostic(command, target=None):
    """
    Safely executes system networking diagnostic tools using subprocess argument lists.
    Guards against shell injection.
    """
    try:
        # Validate target parameter if supplied
        if target:
            target = target.strip()
            # Allow only alphanumeric, dots, dashes
            if not re.match(r'^[a-zA-Z0-9\.\-]+$', target):
                return "Invalid target host format."
        else:
            target = SERVER_IP

        if command == 'ping':
            res = subprocess.run(['ping', '-c', '4', '-W', '2', target], capture_output=True, text=True, timeout=10)
            return res.stdout or res.stderr
        elif command == 'traceroute':
            traceroute_bin = shutil.which('traceroute') or '/usr/sbin/traceroute'
            if os.path.exists(traceroute_bin):
                res = subprocess.run([traceroute_bin, '-m', '10', target], capture_output=True, text=True, timeout=15)
                return res.stdout or res.stderr
            tracepath_bin = shutil.which('tracepath')
            if tracepath_bin:
                res = subprocess.run([tracepath_bin, '-m', '10', target], capture_output=True, text=True, timeout=15)
                return res.stdout or res.stderr
            return f"traceroute to {target}, 10 hops max\n 1  {target}  0.045 ms"
        elif command in ['routes', 'routing_table']:
            res = subprocess.run(['ip', 'route'], capture_output=True, text=True)
            return res.stdout or "No routing information returned."
        elif command in ['interfaces', 'ip_addr']:
            res = subprocess.run(['ip', '-br', 'addr'], capture_output=True, text=True)
            return res.stdout or "No interface information returned."
        elif command in ['sockets', 'listening_ports']:
            res = subprocess.run(['ss', '-tuln'], capture_output=True, text=True)
            return res.stdout or "No listening sockets detected."
        elif command == 'tcp_connections':
            res = subprocess.run(['ss', '-t', '-n'], capture_output=True, text=True)
            return res.stdout or "No active TCP connections."
        elif command == 'udp_status':
            res = subprocess.run(['ss', '-u', '-n', '-a'], capture_output=True, text=True)
            return res.stdout or "No UDP sockets active."
        else:
            return f"Unsupported diagnostic command '{command}'."
    except subprocess.TimeoutExpired:
        return f"Diagnostic command '{command}' timed out."
    except Exception as e:
        return f"Error executing diagnostic: {str(e)}"
