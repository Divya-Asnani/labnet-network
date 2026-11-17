import datetime
from web.database.supabase_client import get_supabase_admin


def record_chat_message(sender_user_id: str, message: str, protocol: str = 'TCP', status: str = 'delivered', sender_device_id: str = None):
    client = get_supabase_admin()
    if not client:
        return False, "Database client unavailable"

    proto = protocol.upper()
    if proto not in ['TCP', 'UDP']:
        proto = 'TCP'

    if status not in ['sent', 'delivered', 'failed']:
        status = 'delivered'

    record = {
        'sender_user_id': sender_user_id,
        'message': message,
        'protocol': proto,
        'status': status,
        'sent_at': datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    if sender_device_id:
        record['sender_device_id'] = sender_device_id

    try:
        res = client.table('chat_messages').insert(record).execute()
        return True, res.data[0] if res.data else record
    except Exception as e:
        print(f"[REPO ERROR: record_chat_message] {e}")
        return False, str(e)


def list_recent_messages(limit: int = 50):
    client = get_supabase_admin()
    if not client:
        return []
    try:
        # Join with profiles to get display_name
        res = client.table('chat_messages').select('*, profiles(display_name, role)').order('sent_at', desc=False).limit(limit).execute()
        return res.data or []
    except Exception as e:
        print(f"[REPO ERROR: list_recent_messages] {e}")
        return []
