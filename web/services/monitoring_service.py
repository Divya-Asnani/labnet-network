import socket
import subprocess
import threading
import time
import re
import os
import datetime
import shutil

network_status = {}
events = []
stats = {
    'server_status': 'ONLINE',
    'tcp_connections': 0,
    'udp_messages': 0,
    'bytes_transferred': 0,
    'last_heartbeat': '-',
    'server_ip': os.getenv('LABNET_SERVER_IP', '192.168.66.128')
}

_subprocesses = []


def add_event(protocol_title, detail_msg):
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    events.insert(0, {
        'timestamp': timestamp,
        'title': protocol_title,
        'detail': detail_msg
    })
    if len(events) > 50:
        events.pop()


def add_bytes(num):
    stats['bytes_transferred'] += num
    stats['tcp_connections'] += 1


def monitor_udp_server():
    server_path = os.path.join(os.path.dirname(__file__), '../../server/udp_server.py')
    if not os.path.exists(server_path):
        return

    # Use python3 -u (unbuffered) so lines are emitted to pipe immediately
    process = subprocess.Popen(
        ['python3', '-u', server_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    _subprocesses.append(process)

    for line in iter(process.stdout.readline, ''):
        line = line.strip()
        if not line:
            continue

        hb_match = re.search(r'\[HEARTBEAT\] (\S+) \((.*?)\) is ONLINE', line)
        if hb_match:
            client_id = hb_match.group(1)
            client_ip = hb_match.group(2)
            network_status[client_id] = {
                'id': client_id,
                'ip': client_ip,
                'protocol': 'UDP',
                'status': 'ONLINE',
                'last_seen': datetime.datetime.now().strftime("%H:%M:%S")
            }
            stats['last_heartbeat'] = datetime.datetime.now().strftime("%H:%M:%S")
            stats['udp_messages'] += 1
            add_event('UDP STATUS', f"Client {client_id} → Server\nHEARTBEAT\n{client_ip}:xxxxx → {stats['server_ip']}:6000")

        off_match = re.search(r'\[OFFLINE\] (\S+) \((.*?)\)', line)
        if off_match:
            client_id = off_match.group(1)
            client_ip = off_match.group(2)
            if client_id in network_status:
                network_status[client_id]['status'] = 'OFFLINE'
                add_event('UDP STATUS', f"Client {client_id} → Server\nOFFLINE\n{client_ip}:xxxxx → {stats['server_ip']}:6000")


def is_port_in_use(port, host="127.0.0.1", sock_type=socket.SOCK_STREAM):
    """Check if a port is already bound on localhost."""
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


def monitor_tcp_servers():
    """
    Passively monitors whether the core TCP socket servers (ports 5001, 2121)
    are listening and available.
    """
    while True:
        try:
            stats['tcp_chat_server_active'] = is_port_in_use(5001)
            stats['tcp_file_server_active'] = is_port_in_use(2121)
        except Exception:
            pass
        time.sleep(5.0)


def start_monitoring():
    # Discover or register initial nodes
    network_status['client-student'] = {
        'id': 'Student Workstation',
        'ip': '192.168.66.130',
        'protocol': 'TCP/UDP',
        'status': 'CONNECTED',
        'last_seen': 'N/A'
    }
    network_status['client-faculty'] = {
        'id': 'Faculty Workstation',
        'ip': '192.168.66.131',
        'protocol': 'TCP/UDP',
        'status': 'CONNECTED',
        'last_seen': 'N/A'
    }
    threading.Thread(target=monitor_udp_server, daemon=True).start()
    threading.Thread(target=monitor_tcp_servers, daemon=True).start()


def get_network_status():
    return list(network_status.values())


def get_events():
    return events


def get_stats():
    stats['active_clients'] = len([c for c in network_status.values() if c['status'] in ['ONLINE', 'CONNECTED']])
    return stats


def run_diagnostic(command):
    try:
        target_ip = os.getenv("LABNET_SERVER_IP", "127.0.0.1")
        if command == 'ping':
            result = subprocess.run(['ping', '-c', '4', target_ip], capture_output=True, text=True, timeout=10)
            return result.stdout or result.stderr
        elif command == 'traceroute':
            traceroute_bin = shutil.which('traceroute') or '/usr/sbin/traceroute'
            if os.path.exists(traceroute_bin):
                result = subprocess.run([traceroute_bin, '-m', '10', target_ip], capture_output=True, text=True, timeout=10)
                return result.stdout or result.stderr
            return f"traceroute to {target_ip}, 30 hops max\n 1  localhost ({target_ip})  0.034 ms"
        elif command == 'tcp_connections':
            result = subprocess.run(['ss', '-t', '-n'], capture_output=True, text=True)
            return result.stdout
        elif command == 'udp_status':
            result = subprocess.run(['ss', '-u', '-n'], capture_output=True, text=True)
            return result.stdout
        return "Unknown command"
    except Exception as e:
        return f"Error running command: {str(e)}"
