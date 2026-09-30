import socket

HOST = "0.0.0.0"
PORT = 5000

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

server.setsockopt(
    socket.SOL_SOCKET,
    socket.SO_REUSEADDR,
    1
)

server.bind((HOST, PORT))
server.listen(5)

print(f"LabNet TCP server listening on port {PORT}")

while True:
    client, address = server.accept()

    print(f"Client connected: {address}")

    data = client.recv(1024)

    if data:
        message = data.decode()
        print(f"Received: {message}")

        response = "ACK: Message received by LabNet TCP server"

        client.sendall(response.encode())

    client.close()

