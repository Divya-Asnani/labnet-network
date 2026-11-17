import datetime
from web.database.supabase_client import get_supabase_admin


def record_heartbeat(device_id: str, latency_ms: float = None, source_ip: str = None):
    client = get_supabase_admin()
    if not client:
        return False, "Database client unavailable"

    record = {
        'device_id': device_id,
        'received_at': datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    if latency_ms is not None:
        record['latency_ms'] = float(latency_ms)
    if source_ip:
        record['source_ip'] = source_ip

    try:
        res = client.table('heartbeat_logs').insert(record).execute()
        return True, res.data[0] if res.data else record
    except Exception as e:
        print(f"[REPO ERROR: record_heartbeat] {e}")
        return False, str(e)


def list_heartbeats(device_id: str = None, limit: int = 50):
    client = get_supabase_admin()
    if not client:
        return []
    try:
        query = client.table('heartbeat_logs').select('*, devices(device_name, ip_address)').order('received_at', desc=True).limit(limit)
        if device_id:
            query = query.eq('device_id', device_id)
        res = query.execute()
        return res.data or []
    except Exception as e:
        print(f"[REPO ERROR: list_heartbeats] {e}")
        return []
