"""
LabNet CLI TCP Chat Client (clients/tcp_chat_client.py)
=======================================================
A terminal-based multithreaded TCP chat client connecting to
server/tcp_chat_server.py on port 5001.

Uses two threads:
- Receiver thread: continuously reads incoming JSON frames and prints them.
- Sender thread (main): reads console input and transmits JSON frames.
"""

import socket
import threading
import json
import sys
from datetime import datetime

SERVER_IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.66.128"
SERVER_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 5001
USERNAME = sys.argv[3] if len(sys.argv) > 3 else "TerminalUser"


def receive_loop(client_socket):
    """Continuously receive and render broadcast messages from server."""
    buffer = ""
    try:
        while True:
            data = client_socket.recv(4096)
            if not data:
                print("\n[DISCONNECT] Server closed the connection.")
                break
            buffer += data.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    sender = msg.get("sender", "Server")
                    text = msg.get("text", "")
                    ts = msg.get("timestamp", "")
                    msg_type = msg.get("type", "chat")

                    if msg_type == "system":
                        print(f"\n[SYSTEM] [{ts}] {text}\nYou > ", end="", flush=True)
                    else:
                        print(f"\n[{sender}] [{ts}]: {text}\nYou > ", end="", flush=True)
                except json.JSONDecodeError:
                    print(f"\n[RAW] {line}\nYou > ", end="", flush=True)
    except Exception as err:
        print(f"\n[ERROR] Receive loop terminated: {err}")


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    print(f"[*] Connecting to LabNet TCP Chat Server at {SERVER_IP}:{SERVER_PORT} as '{USERNAME}'...")

    try:
        sock.connect((SERVER_IP, SERVER_PORT))
        print("[+] Connected successfully! Type messages below. Type 'exit' to quit.\n")

        # Start background listener thread
        listener_thread = threading.Thread(target=receive_loop, args=(sock,), daemon=True)
        listener_thread.start()

        # Send an initial greeting
        join_msg = {
            "type": "chat",
            "sender": USERNAME,
            "text": f"Hello! {USERNAME} has joined the chat.",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        sock.sendall((json.dumps(join_msg) + "\n").encode("utf-8"))

        while True:
            text = input("You > ").strip()
            if not text:
                continue
            if text.lower() == "exit":
                break

            msg_obj = {
                "type": "chat",
                "sender": USERNAME,
                "text": text,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            sock.sendall((json.dumps(msg_obj) + "\n").encode("utf-8"))

    except KeyboardInterrupt:
        print("\n[*] Exiting chat client...")
    except Exception as err:
        print(f"[-] Connection failed: {err}")
    finally:
        sock.close()
        print("[*] Socket closed.")


if __name__ == "__main__":
    main()
