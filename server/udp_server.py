import socket
import time
from datetime import datetime

HOST = "0.0.0.0"
PORT = 6000

HEARTBEAT_TIMEOUT = 10

clients = {}


def check_client_status():
    current_time = time.time()

    for client_id, info in clients.items():
        if current_time - info["last_seen"] > HEARTBEAT_TIMEOUT:
            if info["status"] != "OFFLINE":
                info["status"] = "OFFLINE"

                print(
                    f"[OFFLINE] {client_id} "
                    f"({info['ip']})"
                )


server_socket = socket.socket(
    socket.AF_INET,
    socket.SOCK_DGRAM
)

server_socket.setsockopt(
    socket.SOL_SOCKET,
    socket.SO_REUSEADDR,
    1
)

server_socket.bind((HOST, PORT))

print(f"UDP Server listening on {HOST}:{PORT}")
print("Heartbeat monitoring enabled.\n")


while True:

    server_socket.settimeout(1)

    try:
        data, address = server_socket.recvfrom(1024)

        message = data.decode()

        print(
            f"[UDP] {address} -> {message}"
        )

        parts = message.split("|")

        if parts[0] == "HEARTBEAT":

            client_id = parts[1]

            client_ip = address[0]

            clients[client_id] = {
                "ip": client_ip,
                "last_seen": time.time(),
                "last_seen_readable": datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "status": "ONLINE"
            }

            print(
                f"[HEARTBEAT] {client_id} "
                f"({client_ip}) is ONLINE"
            )

            response = f"HEARTBEAT_ACK|{client_id}"

            server_socket.sendto(
                response.encode(),
                address
            )

        else:

            response = f"UDP_RECEIVED|{message}"

            server_socket.sendto(
                response.encode(),
                address
            )

    except socket.timeout:
        pass

    check_client_status()
