"""
LabNet Local Mail Demonstration Server (server/mail_test_server.py)
==================================================================
Provides genuine local RFC 5321 SMTP server on port 1025 and
RFC 1939 POP3 retrieval server on port 1100 for network service demonstrations.
Stores internal laboratory messages in an in-memory mailbox.
"""

import socket
import threading
import time
from datetime import datetime

SMTP_PORT = 1025
POP3_PORT = 1100

# Thread-safe in-memory mail spool: {username: [messages]}
_mailbox_lock = threading.Lock()
_mailbox = {
    "student": [
        "From: admin@labnet.local\r\nTo: student@labnet.local\r\nSubject: Welcome to LabNet Research Network\r\nDate: Tue, 17 Nov 2026 12:00:00 +0530\r\n\r\nWelcome to LabNet. Your VM node is registered and active on subnet 192.168.66.0/24.\r\n"
    ]
}


class SMTPServerThread(threading.Thread):
    def __init__(self, port=SMTP_PORT):
        super().__init__(daemon=True)
        self.port = port
        self.sock = None

    def run(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.bind(("0.0.0.0", self.port))
            self.sock.listen(5)
            print(f"[SMTP SERVER] Listening on 0.0.0.0:{self.port} (RFC 5321)")
            while True:
                conn, addr = self.sock.accept()
                threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()
        except Exception as e:
            print(f"[SMTP SERVER ERROR] {e}")

    def handle_client(self, conn, addr):
        try:
            conn.sendall(b"220 labnet-server LabNet SMTP Service Ready\r\n")
            mail_from = ""
            rcpt_to = []
            data_mode = False
            data_lines = []

            while True:
                line = conn.recv(1024).decode('utf-8', errors='replace')
                if not line:
                    break

                if data_mode:
                    data_lines.append(line)
                    if "\r\n.\r\n" in "".join(data_lines) or "\n.\n" in "".join(data_lines):
                        full_msg = "".join(data_lines)
                        # Strip trailing .
                        full_msg = full_msg.split("\r\n.\r\n")[0]
                        with _mailbox_lock:
                            for rcpt in rcpt_to:
                                uname = rcpt.split('@')[0].lower()
                                if uname not in _mailbox:
                                    _mailbox[uname] = []
                                _mailbox[uname].append(full_msg)
                        conn.sendall(b"250 2.0.0 OK: message queued\r\n")
                        data_mode = False
                        data_lines = []
                    continue

                cmd_line = line.strip()
                cmd = cmd_line.split(' ')[0].upper()

                if cmd in ["HELO", "EHLO"]:
                    conn.sendall(b"250-labnet-server Hello\r\n250 8BITMIME\r\n")
                elif cmd == "MAIL":
                    # MAIL FROM:<...>
                    mail_from = cmd_line
                    conn.sendall(b"250 2.1.0 Sender OK\r\n")
                elif cmd == "RCPT":
                    # RCPT TO:<...>
                    rcpt_to.append(cmd_line.split(':')[-1].strip('<> '))
                    conn.sendall(b"250 2.1.5 Recipient OK\r\n")
                elif cmd == "DATA":
                    data_mode = True
                    conn.sendall(b"354 Start mail input; end with <CRLF>.<CRLF>\r\n")
                elif cmd == "NOOP":
                    conn.sendall(b"250 OK\r\n")
                elif cmd == "RSET":
                    mail_from = ""
                    rcpt_to = []
                    data_mode = False
                    conn.sendall(b"250 OK\r\n")
                elif cmd == "QUIT":
                    conn.sendall(b"221 2.0.0 Bye\r\n")
                    break
                else:
                    conn.sendall(b"500 Syntax error, command unrecognized\r\n")
        except Exception:
            pass
        finally:
            conn.close()


class POP3ServerThread(threading.Thread):
    def __init__(self, port=POP3_PORT):
        super().__init__(daemon=True)
        self.port = port
        self.sock = None

    def run(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.bind(("0.0.0.0", self.port))
            self.sock.listen(5)
            print(f"[POP3 SERVER] Listening on 0.0.0.0:{self.port} (RFC 1939)")
            while True:
                conn, addr = self.sock.accept()
                threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()
        except Exception as e:
            print(f"[POP3 SERVER ERROR] {e}")

    def handle_client(self, conn, addr):
        try:
            conn.sendall(b"+OK LabNet POP3 Server Ready\r\n")
            current_user = None

            while True:
                line = conn.recv(1024).decode('utf-8', errors='replace')
                if not line:
                    break

                parts = line.strip().split(' ', 1)
                cmd = parts[0].upper()
                arg = parts[1] if len(parts) > 1 else ""

                if cmd == "USER":
                    current_user = arg.lower()
                    conn.sendall(b"+OK User accepted\r\n")
                elif cmd == "PASS":
                    conn.sendall(b"+OK Pass accepted\r\n")
                elif cmd == "STAT":
                    with _mailbox_lock:
                        msgs = _mailbox.get(current_user, [])
                        total_bytes = sum(len(m) for m in msgs)
                        conn.sendall(f"+OK {len(msgs)} {total_bytes}\r\n".encode())
                elif cmd == "LIST":
                    with _mailbox_lock:
                        msgs = _mailbox.get(current_user, [])
                        total_bytes = sum(len(m) for m in msgs)
                        resp = f"+OK {len(msgs)} messages ({total_bytes} octets)\r\n"
                        for idx, m in enumerate(msgs, 1):
                            resp += f"{idx} {len(m)}\r\n"
                        resp += ".\r\n"
                        conn.sendall(resp.encode())
                elif cmd == "RETR":
                    try:
                        idx = int(arg) - 1
                        with _mailbox_lock:
                            msgs = _mailbox.get(current_user, [])
                            if 0 <= idx < len(msgs):
                                msg_content = msgs[idx]
                                conn.sendall(f"+OK {len(msg_content)} octets\r\n{msg_content}\r\n.\r\n".encode())
                            else:
                                conn.sendall(b"-ERR No such message\r\n")
                    except Exception:
                        conn.sendall(b"-ERR Invalid message index\r\n")
                elif cmd == "NOOP":
                    conn.sendall(b"+OK\r\n")
                elif cmd == "QUIT":
                    conn.sendall(b"+OK LabNet POP3 Server Signing Off\r\n")
                    break
                else:
                    conn.sendall(b"-ERR Unknown command\r\n")
        except Exception:
            pass
        finally:
            conn.close()


def start_mail_servers():
    smtp_srv = SMTPServerThread(SMTP_PORT)
    pop3_srv = POP3ServerThread(POP3_PORT)
    smtp_srv.start()
    pop3_srv.start()
    return smtp_srv, pop3_srv


if __name__ == "__main__":
    start_mail_servers()
    print("[*] Mail test servers started. Press Ctrl+C to exit.")
    while True:
        time.sleep(1)
