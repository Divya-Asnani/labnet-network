import datetime
from web.database.supabase_client import get_supabase_admin


def record_session(protocol: str, status: str = 'active', source_device_id: str = None, destination_device_id: str = None, source_port: int = None, destination_port: int = None, bytes_sent: int = 0, bytes_received: int = 0):
    client = get_supabase_admin()
    if not client:
        return False, "Database client unavailable"

    proto = protocol.upper()
    if proto not in ['TCP', 'UDP', 'ICMP', 'HTTP', 'DNS', 'SMTP', 'POP3']:
        proto = 'TCP'

    if status not in ['active', 'completed']:
        status = 'active'

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    record = {
        'protocol': proto,
        'status': status,
        'started_at': now_iso,
        'bytes_sent': int(bytes_sent),
        'bytes_received': int(bytes_received)
    }
    if source_device_id:
        record['source_device_id'] = source_device_id
    if destination_device_id:
        record['destination_device_id'] = destination_device_id
    if source_port:
        record['source_port'] = int(source_port)
    if destination_port:
        record['destination_port'] = int(destination_port)
    if status == 'completed':
        record['ended_at'] = now_iso

    try:
        res = client.table('network_sessions').insert(record).execute()
        return True, res.data[0] if res.data else record
    except Exception as e:
        print(f"[REPO ERROR: record_session] {e}")
        return False, str(e)


def update_session(session_id: str, status: str = 'completed', bytes_sent: int = None, bytes_received: int = None):
    client = get_supabase_admin()
    if not client:
        return False, "Database client unavailable"

    updates = {'status': status}
    if status == 'completed':
        updates['ended_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if bytes_sent is not None:
        updates['bytes_sent'] = int(bytes_sent)
    if bytes_received is not None:
        updates['bytes_received'] = int(bytes_received)

    try:
        res = client.table('network_sessions').update(updates).eq('id', session_id).execute()
        return True, res.data
    except Exception as e:
        print(f"[REPO ERROR: update_session] {e}")
        return False, str(e)


def list_sessions(limit: int = 50):
    client = get_supabase_admin()
    if not client:
        return []
    try:
        res = client.table('network_sessions').select('*').order('started_at', desc=True).limit(limit).execute()
        return res.data or []
    except Exception as e:
        print(f"[REPO ERROR: list_sessions] {e}")
        return []
