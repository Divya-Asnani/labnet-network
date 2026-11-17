import socket
import sys

SERVER_IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.66.128"
SERVER_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 5000


client_socket = socket.socket(
    socket.AF_INET,
    socket.SOCK_STREAM
)

print(f"Connecting to {SERVER_IP}:{SERVER_PORT}")

try:
    client_socket.connect((SERVER_IP, SERVER_PORT))
    print("Connected to server.")
    print("Type messages. Type 'exit' to close.\n")

    while True:
        message = input("You: ")

        if message.lower() == "exit":
            break

        client_socket.sendall(message.encode())

        response = client_socket.recv(1024)
        if not response:
            print("Server closed the connection.")
            break

        print("Server:", response.decode())

except Exception as err:
    print(f"Connection error: {err}")
finally:
    client_socket.close()
    print("Connection closed.")
