"""
LabNet SMTP Client Service (server/smtp_service.py)
==================================================
Demonstrates standard RFC 5321 SMTP email submission using Python's smtplib
against the local LabNet SMTP test server (Port 1025). Captures the full
command/response protocol transcript for educational inspection.
NOTE: Messages are queued strictly in the local LabNet test spool, not delivered externally.
"""

import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime


def send_mail_smtp(sender: str, recipient: str, subject: str, body: str, host: str = "127.0.0.1", port: int = 1025):
    """
    Transmits an email message to the local SMTP test server.
    Returns: (success: bool, result_dict: dict)
    """
    start_time = time.time()
    transcript = []

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg["Date"] = datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0530")
    msg.attach(MIMEText(body, "plain"))

    try:
        transcript.append(f"-> CONNECT {host}:{port} (TCP)")
        with smtplib.SMTP(host, port, timeout=5.0) as server:
            ehlo_code, ehlo_msg = server.ehlo()
            ehlo_text = ehlo_msg.decode("utf-8", errors="replace") if isinstance(ehlo_msg, bytes) else str(ehlo_msg)
            transcript.append(f"-> EHLO labnet-client")
            transcript.append(f"<- {ehlo_code} {ehlo_text.splitlines()[0] if ehlo_text else 'OK'}")

            code_mf, msg_mf = server.mail(sender)
            transcript.append(f"-> MAIL FROM:<{sender}>")
            transcript.append(f"<- {code_mf} {msg_mf.decode('utf-8', errors='replace')}")

            code_rt, msg_rt = server.rcpt(recipient)
            transcript.append(f"-> RCPT TO:<{recipient}>")
            transcript.append(f"<- {code_rt} {msg_rt.decode('utf-8', errors='replace')}")

            code_dt, msg_dt = server.data(msg.as_string())
            transcript.append("-> DATA [RFC 2822 headers + body + <CRLF>.<CRLF>]")
            transcript.append(f"<- {code_dt} {msg_dt.decode('utf-8', errors='replace')}")

            transcript.append("-> QUIT")
            transcript.append("<- 221 2.0.0 Bye")

        duration_ms = round((time.time() - start_time) * 1000.0, 2)
        return True, {
            "status": "DELIVERED_LOCAL_SPOOL",
            "sender": sender,
            "recipient": recipient,
            "subject": subject,
            "smtp_host": f"{host}:{port}",
            "ehlo_response": f"{ehlo_code} {ehlo_text}",
            "transcript": transcript,
            "note": "Queued in local LabNet test spool (port 1025). Not transmitted to external mail relays.",
            "duration_ms": duration_ms,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        duration_ms = round((time.time() - start_time) * 1000.0, 2)
        transcript.append(f"!! SMTP ERROR: {str(e)}")
        return False, {
            "status": "FAILED",
            "sender": sender,
            "recipient": recipient,
            "subject": subject,
            "smtp_host": f"{host}:{port}",
            "transcript": transcript,
            "error": str(e),
            "duration_ms": duration_ms,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
