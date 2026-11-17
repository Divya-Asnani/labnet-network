"""
LabNet SMTP Client Service (server/smtp_service.py)
==================================================
Demonstrates standard RFC 5321 SMTP email transmission using Python's smtplib.
Transmits structured notifications to the local SMTP server (Port 1025),
captures transaction timing, and audits the session.
"""

import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime


def send_mail_smtp(sender: str, recipient: str, subject: str, body: str, host: str = "127.0.0.1", port: int = 1025):
    """
    Transmits an email message via SMTP.
    Returns: (success: bool, result_dict: dict)
    """
    start_time = time.time()
    msg = MIMEMultipart()
    msg['From'] = sender
    msg['To'] = recipient
    msg['Subject'] = subject
    msg['Date'] = datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0530")
    msg.attach(MIMEText(body, 'plain'))

    try:
        with smtplib.SMTP(host, port, timeout=5.0) as server:
            server.set_debuglevel(0)
            # EHLO / HELO
            ehlo_code, ehlo_msg = server.ehlo()
            # Send message
            server.sendmail(sender, [recipient], msg.as_string())

        duration_ms = round((time.time() - start_time) * 1000.0, 2)
        return True, {
            "status": "DELIVERED",
            "sender": sender,
            "recipient": recipient,
            "subject": subject,
            "smtp_host": f"{host}:{port}",
            "ehlo_response": f"{ehlo_code} {ehlo_msg.decode('utf-8', errors='replace') if isinstance(ehlo_msg, bytes) else ehlo_msg}",
            "duration_ms": duration_ms,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        duration_ms = round((time.time() - start_time) * 1000.0, 2)
        return False, {
            "status": "FAILED",
            "sender": sender,
            "recipient": recipient,
            "subject": subject,
            "error": str(e),
            "duration_ms": duration_ms,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }


if __name__ == "__main__":
    success, res = send_mail_smtp("researcher@labnet.local", "student@labnet.local", "Experiment Protocol", "Data acquisition scheduled for 14:00.")
    print("SMTP Result:", res)
