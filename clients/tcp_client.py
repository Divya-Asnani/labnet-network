import socket

SERVER_IP = "192.168.10.10"
SERVER_PORT = 5000


client_socket = socket.socket(
    socket.AF_INET,
    socket.SOCK_STREAM
)

print(f"Connecting to {SERVER_IP}:{SERVER_PORT}")

client_socket.connect((SERVER_IP, SERVER_PORT))

print("Connected to server.")
print("Type messages. Type 'exit' to close.\n")

try:
    while True:
        message = input("You: ")

        if message.lower() == "exit":
            break

        client_socket.sendall(message.encode())

        response = client_socket.recv(1024)

        print("Server:", response.decode())

finally:
    client_socket.close()
    print("Connection closed.")
