"""
LabNet TCP Chat Server (server/tcp_chat_server.py)
==================================================
A multithreaded, real-time TCP broadcast chat server demonstrating core
Computer Networks (CNT) socket-programming concepts.

Networking Concepts Explained:
------------------------------
1. socket(): Creates an endpoint for communication. socket(AF_INET, SOCK_STREAM)
   specifies IPv4 addressing and TCP (reliable, connection-oriented byte stream).
2. bind(): Associates the socket with a specific network interface (0.0.0.0 for
   all available interfaces) and port number (5001).
3. listen(): Marks the socket as passive, ready to accept incoming connection requests.
   The backlog parameter defines the maximum queue length for pending connections.
4. accept(): A blocking system call that extracts the first connection request on the
   queue, creates a NEW connected socket specifically for that client, and returns
   (client_socket, client_address). The listening socket continues listening.
5. send() / sendall(): Transmits bytes into the socket's write buffer. sendall()
   guarantees all bytes are sent or raises an error.
6. recv(): Reads incoming bytes from the socket's receive buffer. Returns empty bytes b''
   when the remote host closes the connection cleanly (EOF).
7. threading: Allows handling multiple concurrent TCP clients simultaneously without
   blocking the main server accept loop.
8. Client/Server Architecture: Centralized topology where all clients connect to
   a single authoritative server which mediates communication and broadcasts messages.
9. Framing: TCP is a byte-stream protocol with no inherent message boundaries.
   This implementation uses newline-delimited JSON (\\n) so receivers can reliably
   delineate distinct application-layer messages.
"""

import socket
import threading
import json
import time
import signal
import sys
from datetime import datetime

HOST = "0.0.0.0"
PORT = 5001
BUFFER_SIZE = 4096

# Global listening socket reference for clean shutdown
_listening_socket = None

def _sig_handler(signum, frame):
    global _listening_socket
    if _listening_socket:
        try:
            _listening_socket.close()
        except Exception:
            pass
    sys.exit(0)

signal.signal(signal.SIGTERM, _sig_handler)
signal.signal(signal.SIGINT, _sig_handler)

# Thread-safe registry of connected clients
# Key: socket object, Value: client metadata dictionary
clients = {}
clients_lock = threading.Lock()


def get_timestamp():
    """Return formatted current timestamp."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def broadcast(message_dict, sender_socket=None):
    """
    Broadcast a message dictionary to all connected clients.
    Uses JSON serialization with a newline delimiter for message framing.
    Thread-safe access to the client registry is enforced using clients_lock.
    """
    payload = (json.dumps(message_dict) + "\n").encode("utf-8")
    disconnected_sockets = []

    with clients_lock:
        active_count = len(clients)
        for sock, info in clients.items():
            try:
                # sendall ensures the complete byte stream is delivered to the socket buffer
                sock.sendall(payload)
            except (BrokenPipeError, ConnectionResetError, OSError) as err:
                print(f"[{get_timestamp()}] [ERROR] Failed to send to {info['addr']}: {err}")
                disconnected_sockets.append(sock)

        # Clean up any sockets that encountered network failures during broadcast
        for sock in disconnected_sockets:
            dead_addr = clients[sock]["addr"]
            sock.close()
            del clients[sock]
            print(f"[{get_timestamp()}] [PRUNE] Cleaned disconnected client {dead_addr}. Active: {len(clients)}")

    print(f"[{get_timestamp()}] [BROADCAST] Dispatched '{message_dict.get('type')}' to {active_count} client(s)")


def handle_client(client_socket, client_address):
    """
    Worker function executed in a dedicated thread for each connected client.
    Maintains a persistent TCP connection and streams incoming frames.
    """
    client_str = f"{client_address[0]}:{client_address[1]}"
    thread_name = threading.current_thread().name

    print(f"\n[{get_timestamp()}] [+] [CONNECT] Client connected from {client_str} on {thread_name}")

    with clients_lock:
        clients[client_socket] = {
            "addr": client_str,
            "ip": client_address[0],
            "port": client_address[1],
            "connected_at": time.time(),
            "username": "Unknown"
        }
        total_clients = len(clients)

    print(f"[{get_timestamp()}] [INFO] Active TCP client count: {total_clients}")

    # Notify all clients of the new connection
    welcome_notice = {
        "type": "system",
        "sender": "LabNet Server",
        "text": f"New connection from {client_str}",
        "timestamp": get_timestamp(),
        "active_clients": total_clients
    }
    broadcast(welcome_notice)

    # Stream buffer for handling message framing
    # Because TCP is a continuous byte stream, multiple messages may arrive in one recv()
    # or a single large message may be split across multiple recv() calls.
    read_buffer = ""

    try:
        while True:
            # recv() blocks until data is available on the TCP receive buffer
            raw_data = client_socket.recv(BUFFER_SIZE)

            # An empty byte string b'' indicates client cleanly closed connection (TCP FIN)
            if not raw_data:
                print(f"[{get_timestamp()}] [-] [EOF] Client {client_str} closed connection.")
                break

            read_buffer += raw_data.decode("utf-8", errors="replace")

            # Extract complete newline-delimited frames from stream buffer
            while "\n" in read_buffer:
                line, read_buffer = read_buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue

                try:
                    message_data = json.loads(line)
                except json.JSONDecodeError as json_err:
                    print(f"[{get_timestamp()}] [WARN] Invalid JSON from {client_str}: {json_err}")
                    continue

                # Update sender's registered identity if provided
                sender_name = message_data.get("sender", "Anonymous")
                with clients_lock:
                    if client_socket in clients:
                        clients[client_socket]["username"] = sender_name

                # Ensure timestamp and metadata exist
                if "timestamp" not in message_data:
                    message_data["timestamp"] = get_timestamp()
                message_data["origin_addr"] = client_str

                print(f"[{get_timestamp()}] [RECV] From {sender_name} ({client_str}): {message_data.get('text', '')}")

                # Broadcast to all connected clients
                broadcast(message_data, sender_socket=client_socket)

    except (ConnectionResetError, ConnectionAbortedError) as err:
        print(f"[{get_timestamp()}] [!] [RESET] Client {client_str} abruptly disconnected: {err}")
    except Exception as general_err:
        print(f"[{get_timestamp()}] [ERROR] Unexpected exception with {client_str}: {general_err}")
    finally:
        # Cleanly deregister client and close socket
        with clients_lock:
            if client_socket in clients:
                del clients[client_socket]
            remaining_clients = len(clients)

        client_socket.close()
        print(f"[{get_timestamp()}] [-] [DISCONNECT] Client {client_str} deregistered. Active clients: {remaining_clients}")

        # Broadcast departure notification
        leave_notice = {
            "type": "system",
            "sender": "LabNet Server",
            "text": f"Client {client_str} disconnected",
            "timestamp": get_timestamp(),
            "active_clients": remaining_clients
        }
        broadcast(leave_notice)


def main():
    """
    Main entry point for the LabNet TCP Chat Server.
    Initializes the listening socket and enters the multi-client accept loop.
    """
    bound = False
    for attempt in range(8):
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass

        try:
            server_socket.bind((HOST, PORT))
            _listening_socket = server_socket
            bound = True
            break
        except OSError as bind_err:
            server_socket.close()
            if attempt < 7:
                time.sleep(0.8)
            else:
                print(f"[FATAL] Failed to bind to {HOST}:{PORT}: {bind_err}")
                return

    # Backlog queue size set to 20 concurrent pending connections
    server_socket.listen(20)

    print("==================================================")
    print("      LABNET MULTITHREADED TCP CHAT SERVER        ")
    print("==================================================")
    print(f"[*] Bound to: {HOST}:{PORT}")
    print(f"[*] Protocol: TCP (SOCK_STREAM)")
    print(f"[*] Framing:  Newline-delimited JSON")
    print(f"[*] Status:   Listening for incoming connections...")
    print("==================================================\n")

    try:
        while True:
            # accept() returns a new socket for communication and client address
            client_socket, client_address = server_socket.accept()

            # Spawn a new thread to handle this client concurrently
            client_thread = threading.Thread(
                target=handle_client,
                args=(client_socket, client_address),
                daemon=True
            )
            client_thread.start()

    except KeyboardInterrupt:
        print(f"\n[{get_timestamp()}] [STOP] Server shutting down on operator interrupt.")
    finally:
        with clients_lock:
            for sock in list(clients.keys()):
                try:
                    sock.close()
                except Exception:
                    pass
            clients.clear()
        server_socket.close()
        print(f"[{get_timestamp()}] [SHUTDOWN] Server socket closed cleanly.")


if __name__ == "__main__":
    main()
