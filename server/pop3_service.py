"""
LabNet POP3 Client Service (server/pop3_service.py)
==================================================
Demonstrates RFC 1939 POP3 mail retrieval using Python's poplib.
Connects to local POP3 service (Port 1100), authenticates user, lists messages,
retrieves message content, and terminates session with QUIT.
"""

import poplib
import time
from datetime import datetime


def fetch_pop3_inbox(user: str = "student", password: str = "password", host: str = "127.0.0.1", port: int = 1100):
    """
    Connects to POP3 server and retrieves message list and bodies.
    Returns: (success: bool, result_dict: dict)
    """
    start_time = time.time()
    try:
        pop_client = poplib.POP3(host, port, timeout=5.0)
        # Authentication
        pop_client.user(user)
        pop_client.pass_(password)

        # STAT: (num_messages, total_size)
        stat = pop_client.stat()
        num_msgs = stat[0]
        total_octets = stat[1]

        messages = []
        for i in range(1, num_msgs + 1):
            # RETR i: (response, lines, octets)
            _, lines, octets = pop_client.retr(i)
            raw_msg = "\n".join(l.decode('utf-8', errors='replace') for l in lines)
            messages.append({
                "index": i,
                "size_bytes": octets,
                "content": raw_msg
            })

        pop_client.quit()
        duration_ms = round((time.time() - start_time) * 1000.0, 2)

        return True, {
            "status": "SUCCESS",
            "user": user,
            "pop3_host": f"{host}:{port}",
            "message_count": num_msgs,
            "total_octets": total_octets,
            "messages": messages,
            "duration_ms": duration_ms,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        duration_ms = round((time.time() - start_time) * 1000.0, 2)
        return False, {
            "status": "FAILED",
            "user": user,
            "error": str(e),
            "duration_ms": duration_ms,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }


if __name__ == "__main__":
    success, res = fetch_pop3_inbox()
    print("POP3 Result:", res)
