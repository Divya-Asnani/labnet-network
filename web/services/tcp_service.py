import socket
import datetime
from .monitoring_service import add_event, add_bytes

HOST = "127.0.0.1" 
PORT = 5000

def send_tcp_message(message, sender="Student"):
    try:
        ip = "192.168.66.130" if sender == "Student" else "192.168.66.131"
        add_event('TCP CONNECTION', f"{sender} → LabNet Server\n{ip}:xxxxx → 192.168.66.128:5000\nCONNECTED")
        
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket.settimeout(5)
        client_socket.connect((HOST, PORT))
        client_socket.sendall(message.encode())
        add_bytes(len(message.encode()))
        
        response = client_socket.recv(1024).decode()
        add_bytes(len(response.encode()))
        client_socket.close()
        
        add_event('TCP CHAT', f"{sender} → Server\n'{message[:20]}...'\nDELIVERED")
        return True, response
    except Exception as e:
        add_event('TCP ERROR', f"{sender} → Server\nConnection Failed\n{str(e)}")
        return False, str(e)

def upload_file_tcp(filename, file_content, sender="Student"):
    try:
        ip = "192.168.66.130" if sender == "Student" else "192.168.66.131"
        size_kb = len(file_content) / 1024
        add_event('TCP FILE TRANSFER', f"{sender} → Server\n{filename}\n{size_kb:.2f} KB\nTRANSFERRING")
        
        # Socket mock connection to port 2121
        # client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # client_socket.connect((HOST, 2121))
        # client_socket.sendall(file_content)
        # client_socket.close()
        
        add_bytes(len(file_content))
        add_event('TCP FILE TRANSFER', f"{sender} → Server\n{filename}\nCOMPLETED")
        return True, "Success"
    except Exception as e:
        add_event('TCP ERROR', f"{sender} → Server\nTransfer Failed\n{str(e)}")
        return False, str(e)
