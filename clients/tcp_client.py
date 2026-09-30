import socket

SERVER_IP = "192.168.10.10"
SERVER_PORT = 5000

client = socket.socket(
    socket.AF_INET,
    socket.SOCK_STREAM
)

print(f"Connecting to {SERVER_IP}:{SERVER_PORT}")

client.connect((SERVER_IP, SERVER_PORT))

message = "Hello from LabNet client"

client.sendall(message.encode())

response = client.recv(1024)

print("Server response:", response.decode())

client.close()

