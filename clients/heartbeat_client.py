import socket
import time
from datetime import datetime

SERVER_IP = "192.168.66.128"
SERVER_PORT = 6000

CLIENT_ID = "client-01"

HEARTBEAT_INTERVAL = 5


client_socket = socket.socket(
    socket.AF_INET,
    socket.SOCK_DGRAM
)

print(
    f"Heartbeat client started: {CLIENT_ID}"
)

print(
    f"Sending UDP heartbeat to "
    f"{SERVER_IP}:{SERVER_PORT}\n"
)


try:

    while True:

        timestamp = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        message = (
            f"HEARTBEAT|{CLIENT_ID}|{timestamp}"
        )

        client_socket.sendto(
            message.encode(),
            (SERVER_IP, SERVER_PORT)
        )

        client_socket.settimeout(2)

        try:

            data, server_address = (
                client_socket.recvfrom(1024)
            )

            print(
                f"[{timestamp}] "
                f"Server: {data.decode()}"
            )

        except socket.timeout:

            print(
                f"[{timestamp}] "
                f"No heartbeat acknowledgement"
            )

        time.sleep(HEARTBEAT_INTERVAL)

except KeyboardInterrupt:

    print("\nHeartbeat client stopped.")

finally:

    client_socket.close()
