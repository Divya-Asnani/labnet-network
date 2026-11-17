import datetime
from web.database.supabase_client import get_supabase_admin


def record_chat_message(
    sender_user_id: str,
    message: str,
    protocol: str = "TCP",
    status: str = "delivered",
    sender_device_id: str = None,
    message_id: str = None
):
    client = get_supabase_admin()
    if not client:
        return False, "Database client unavailable"

    proto = (protocol or "TCP").upper()
    if proto not in ["TCP", "UDP"]:
        proto = "TCP"

    if status not in ["sent", "delivered", "failed"]:
        status = "delivered"

    record = {
        "sender_user_id": sender_user_id,
        "message": message,
        "protocol": proto,
        "status": status,
        "sent_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    if message_id:
        record["id"] = message_id
    if sender_device_id:
        record["sender_device_id"] = sender_device_id

    try:
        res = client.table("chat_messages").insert(record).execute()
        return True, res.data[0] if res.data else record
    except Exception as e:
        print(f"[REPO ERROR: record_chat_message] {e}")
        return False, str(e)


def update_chat_message_status(message_id: str, status: str):
    client = get_supabase_admin()
    if not client or not message_id:
        return False, "Database client unavailable"
    if status not in ["sent", "delivered", "failed"]:
        return False, f"Invalid status '{status}'"
    try:
        res = client.table("chat_messages").update({"status": status}).eq("id", message_id).execute()
        return True, res.data
    except Exception as e:
        print(f"[REPO ERROR: update_chat_message_status] {e}")
        return False, str(e)


def list_recent_messages(limit: int = 50, before_iso: str = None, sender_user_id: str = None):
    """
    Loads persistent chat history from Supabase `chat_messages`, joined with
    `profiles` and `devices`. Fetches newest `limit` rows (optionally before a
    cursor timestamp for pagination) and returns them in chronological order (oldest -> newest).
    """
    client = get_supabase_admin()
    if not client:
        return []
    try:
        query = (
            client.table("chat_messages")
            .select("id, sender_user_id, sender_device_id, message, protocol, status, sent_at, profiles(display_name, role), devices(device_name, ip_address)")
        )
        if before_iso:
            query = query.lt("sent_at", before_iso)
        if sender_user_id:
            query = query.eq("sender_user_id", sender_user_id)

        res = query.order("sent_at", desc=True).limit(int(limit)).execute()
        rows = res.data or []
        # Reverse to chronological order (oldest -> newest)
        rows.reverse()
        return rows
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
