import socket

HOST = "0.0.0.0"
PORT = 6000

server_socket = socket.socket(
    socket.AF_INET,
    socket.SOCK_DGRAM
)

server_socket.bind((HOST, PORT))

print(f"UDP Server listening on {HOST}:{PORT}")

while True:
    data, client_address = server_socket.recvfrom(1024)

    message = data.decode()

    print(
        f"Received from {client_address}: {message}"
    )

    response = f"UDP Server received: {message}"

    server_socket.sendto(
        response.encode(),
        client_address
    )
