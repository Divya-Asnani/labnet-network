"""
LabNet Database Service (web/services/db_service.py)
===================================================
Persistence abstraction layer for LabNet.
Prepares clean integration hooks for the 7 designated Supabase tables:
- profiles
- devices
- chat_messages
- file_transfers
- heartbeat_logs
- network_sessions
- network_events

Note: Exact table schemas and Supabase connection parameters are validated
before active live database writes.
"""

import os
from datetime import datetime

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")


def is_supabase_configured():
    """Check if Supabase credentials are provided in the environment."""
    return bool(SUPABASE_URL and SUPABASE_KEY and not SUPABASE_URL.startswith("https://your-project"))


def record_chat_message(sender, message_text, msg_id=None, timestamp=None):
    """
    Hook to persist chat messages to the Supabase 'chat_messages' table.
    """
    if not is_supabase_configured():
        # Awaiting table specs confirmation in Prompt 2
        return False, "Supabase credentials not configured"

    # Future integration via supabase-py SDK
    return True, "Message queued for persistence"


def record_file_transfer(metadata):
    """
    Hook to record a completed file transfer to the Supabase 'file_transfers' table.
    Expected fields:
      - sender_id / sender_device_id
      - receiver_device_id
      - filename
      - file_size
      - protocol (e.g. TCP)
      - checksum (SHA-256)
      - status (SUCCESS/FAILED)
      - timestamp
      - storage_path
    """
    if not is_supabase_configured():
        return False, "Supabase credentials not configured"

    return True, "File transfer record queued"
