import socket
import datetime

HOST = "127.0.0.1"
PORT = 6000

def send_udp_message(message):
    try:
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client_socket.settimeout(2)
        client_socket.sendto(message.encode(), (HOST, PORT))
        response, _ = client_socket.recvfrom(1024)
        client_socket.close()
        return True, response.decode()
    except Exception as e:
        return False, str(e)
