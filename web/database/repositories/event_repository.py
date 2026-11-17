import datetime
from web.database.supabase_client import get_supabase_admin


VALID_EVENT_TYPES = ['heartbeat', 'connection', 'transfer', 'disconnect', 'packet_loss', 'timeout', 'diagnostic', 'auth']


def record_event(event_type: str, protocol: str = None, source_ip: str = None, destination_ip: str = None, device_id: str = None, details: dict = None):
    client = get_supabase_admin()
    if not client:
        return False, "Database client unavailable"

    ev_type = event_type.lower()
    if ev_type not in VALID_EVENT_TYPES:
        ev_type = 'connection'

    record = {
        'event_type': ev_type,
        'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    if protocol:
        record['protocol'] = protocol.upper()
    if source_ip:
        record['source_ip'] = source_ip
    if destination_ip:
        record['destination_ip'] = destination_ip
    if device_id:
        record['device_id'] = device_id
    if details is not None:
        record['details'] = details

    try:
        res = client.table('network_events').insert(record).execute()
        return True, res.data[0] if res.data else record
    except Exception as e:
        print(f"[REPO ERROR: record_event] {e}")
        return False, str(e)


def list_events(limit: int = 50):
    client = get_supabase_admin()
    if not client:
        return []
    try:
        res = client.table('network_events').select('*').order('created_at', desc=True).limit(limit).execute()
        return res.data or []
    except Exception as e:
        print(f"[REPO ERROR: list_events] {e}")
        return []
