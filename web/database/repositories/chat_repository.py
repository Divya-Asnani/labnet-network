"""
LabNet Chat Message Repository (web/database/repositories/chat_repository.py)
=============================================================================
Enforces strict conversation-level isolation on every read and write:
  - Every message MUST be linked to a valid `conversation_id`.
  - Every read/write explicitly checks `is_user_participant(conversation_id, user_id)`
    on the backend (mirroring PostgreSQL RLS).
  - Legacy unassigned messages (`conversation_id IS NULL`) are NEVER exposed to
    any user conversation.
"""

import datetime
from web.database.supabase_client import get_supabase_admin
from web.database.repositories.conversation_repository import is_user_participant


def record_chat_message(
    conversation_id: str,
    sender_user_id: str,
    message: str,
    protocol: str = "TCP",
    status: str = "delivered",
    sender_device_id: str = None,
    message_id: str = None
):
    if not conversation_id:
        return False, "conversation_id is required to persist a chat message.", 400

    # Mandatory server-side participant verification before writing
    if not is_user_participant(conversation_id, sender_user_id):
        return False, "Forbidden: sender is not an authorized participant of this conversation.", 403

    client = get_supabase_admin()
    if not client:
        return False, "Database client unavailable", 503

    proto = (protocol or "TCP").upper()
    if proto not in ["TCP", "UDP"]:
        proto = "TCP"

    if status not in ["sent", "delivered", "failed"]:
        status = "delivered"

    record = {
        "conversation_id": str(conversation_id),
        "sender_user_id": str(sender_user_id),
        "message": message,
        "protocol": proto,
        "status": status,
        "sent_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    if message_id:
        record["id"] = str(message_id)
    if sender_device_id:
        record["sender_device_id"] = str(sender_device_id)

    try:
        res = client.table("chat_messages").insert(record).execute()
        return True, (res.data[0] if res.data else record), 201
    except Exception as e:
        print(f"[REPO ERROR: record_chat_message] {e}")
        return False, str(e), 500


def update_chat_message_status(message_id: str, status: str):
    client = get_supabase_admin()
    if not client or not message_id:
        return False, "Database client unavailable"
    if status not in ["sent", "delivered", "failed"]:
        return False, f"Invalid status '{status}'"
    try:
        res = client.table("chat_messages").update({"status": status}).eq("id", str(message_id)).execute()
        return True, res.data
    except Exception as e:
        print(f"[REPO ERROR: update_chat_message_status] {e}")
        return False, str(e)


def list_conversation_messages(
    conversation_id: str,
    requesting_user_id: str,
    limit: int = 50,
    before_iso: str = None
):
    """
    Retrieves paginated chronological history strictly for ONE conversation
    after verifying that `requesting_user_id` is an authorized participant.
    Never returns messages from other conversations or unassigned legacy rows.
    """
    if not conversation_id:
        return False, "conversation_id is required.", 400

    if not is_user_participant(conversation_id, requesting_user_id):
        return False, "Forbidden: you do not have access to this conversation.", 403

    client = get_supabase_admin()
    if not client:
        return False, "Database client unavailable.", 503

    try:
        query = (
            client.table("chat_messages")
            .select("id, conversation_id, sender_user_id, sender_device_id, message, protocol, status, sent_at, profiles(display_name, role), devices(device_name, ip_address)")
            .eq("conversation_id", str(conversation_id))
        )
        if before_iso:
            query = query.lt("sent_at", before_iso)

        res = query.order("sent_at", desc=True).limit(int(limit)).execute()
        rows = res.data or []
        rows.reverse()  # Chronological order: oldest -> newest
        return True, rows, 200
    except Exception as e:
        print(f"[REPO ERROR: list_conversation_messages] {e}")
        return False, str(e), 500


def list_recent_messages(limit: int = 50):
    """Admin-only aggregate helper returning count/overview metadata only (never used for user chat display)."""
    client = get_supabase_admin()
    if not client:
        return []
    try:
        res = (
            client.table("chat_messages")
            .select("id, conversation_id, sender_user_id, protocol, status, sent_at")
            .order("sent_at", desc=True)
            .limit(int(limit))
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"[REPO ERROR: list_recent_messages] {e}")
        return []


def count_messages() -> int:
    client = get_supabase_admin()
    if not client:
        return 0
    try:
        res = client.table("chat_messages").select("id", count="exact").execute()
        return res.count if res.count is not None else len(res.data or [])
    except Exception as e:
        print(f"[REPO ERROR: count_messages] {e}")
        return 0
