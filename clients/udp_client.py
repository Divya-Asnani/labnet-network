import socket

SERVER_IP = "192.168.66.128"
SERVER_PORT = 6000

client_socket = socket.socket(
    socket.AF_INET,
    socket.SOCK_DGRAM
)

print(f"Sending UDP messages to {SERVER_IP}:{SERVER_PORT}")
print("Type 'exit' to stop.\n")

while True:

    message = input("You: ")

    if message.lower() == "exit":
        break

    client_socket.sendto(
        message.encode(),
        (SERVER_IP, SERVER_PORT)
    )

    data, server_address = client_socket.recvfrom(1024)

    print("Server:", data.decode())

client_socket.close()
