"""
LabNet POP3 Client Service (server/pop3_service.py)
==================================================
Demonstrates RFC 1939 POP3 mail retrieval using Python's poplib against
the local LabNet POP3 test server (Port 1100). Captures the full protocol
command/response transcript for educational inspection.
"""

import poplib
import time
from datetime import datetime


def fetch_pop3_inbox(user: str = "student", password: str = "password", host: str = "127.0.0.1", port: int = 1100, retrieve_index: int = None):
    """
    Connects to local POP3 server, authenticates, executes STAT/LIST, and
    retrieves either all messages or a specific message index.
    Returns: (success: bool, result_dict: dict)
    """
    start_time = time.time()
    transcript = []
    try:
        transcript.append(f"-> CONNECT {host}:{port} (TCP)")
        pop_client = poplib.POP3(host, port, timeout=5.0)
        welcome = pop_client.getwelcome().decode("utf-8", errors="replace")
        transcript.append(f"<- {welcome}")

        u_resp = pop_client.user(user).decode("utf-8", errors="replace")
        transcript.append(f"-> USER {user}")
        transcript.append(f"<- {u_resp}")

        p_resp = pop_client.pass_(password).decode("utf-8", errors="replace")
        transcript.append("-> PASS ********")
        transcript.append(f"<- {p_resp}")

        num_msgs, total_octets = pop_client.stat()
        transcript.append("-> STAT")
        transcript.append(f"<- +OK {num_msgs} {total_octets}")

        _, list_lines, _ = pop_client.list()
        transcript.append("-> LIST")
        transcript.append(f"<- +OK {len(list_lines)} messages listed")

        messages = []
        indices = [retrieve_index] if (retrieve_index and 1 <= retrieve_index <= num_msgs) else list(range(1, num_msgs + 1))

        for i in indices:
            _, lines, octets = pop_client.retr(i)
            transcript.append(f"-> RETR {i}")
            transcript.append(f"<- +OK {octets} octets retrieved")
            raw_msg = "\n".join(l.decode("utf-8", errors="replace") for l in lines)
            # Parse basic headers for clean UI display
            subject = "(No Subject)"
            sender = "unknown"
            date_str = ""
            for line in raw_msg.splitlines():
                low = line.lower()
                if low.startswith("subject:"):
                    subject = line.split(":", 1)[1].strip()
                elif low.startswith("from:"):
                    sender = line.split(":", 1)[1].strip()
                elif low.startswith("date:"):
                    date_str = line.split(":", 1)[1].strip()
            messages.append({
                "index": i,
                "size_bytes": octets,
                "from": sender,
                "subject": subject,
                "date": date_str,
                "content": raw_msg
            })

        q_resp = pop_client.quit().decode("utf-8", errors="replace")
        transcript.append("-> QUIT")
        transcript.append(f"<- {q_resp}")

        duration_ms = round((time.time() - start_time) * 1000.0, 2)
        return True, {
            "status": "SUCCESS",
            "user": user,
            "pop3_host": f"{host}:{port}",
            "message_count": num_msgs,
            "total_octets": total_octets,
            "messages": messages,
            "transcript": transcript,
            "duration_ms": duration_ms,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        duration_ms = round((time.time() - start_time) * 1000.0, 2)
        transcript.append(f"!! POP3 ERROR: {str(e)}")
        return False, {
            "status": "FAILED",
            "user": user,
            "pop3_host": f"{host}:{port}",
            "transcript": transcript,
            "error": str(e),
            "duration_ms": duration_ms,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
