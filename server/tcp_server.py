import socket
import threading

HOST = "0.0.0.0"
PORT = 5000


def handle_client(client_socket, client_address):
    print(f"[+] Client connected: {client_address}")

    try:
        data = client_socket.recv(1024)

        if data:
            message = data.decode()
            print(f"[{client_address}] {message}")

            response = "Hello from LabNet TCP Server"
            client_socket.sendall(response.encode())

    except Exception as error:
        print(f"[ERROR] {client_address}: {error}")

    finally:
        client_socket.close()
        print(f"[-] Client disconnected: {client_address}")


server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

server_socket.setsockopt(
    socket.SOL_SOCKET,
    socket.SO_REUSEADDR,
    1
)

server_socket.bind((HOST, PORT))
server_socket.listen(10)

print(f"LabNet TCP Server listening on port {PORT}")

while True:
    client_socket, client_address = server_socket.accept()

    client_thread = threading.Thread(
        target=handle_client,
        args=(client_socket, client_address)
    )

    client_thread.start()

    print(f"Active threads: {threading.active_count()}")
