import datetime
from web.database.supabase_client import get_supabase_admin


def register_device(user_id: str, device_name: str, hostname: str = None, ip_address: str = None, os_name: str = None, status: str = 'online'):
    client = get_supabase_admin()
    if not client:
        return False, "Database client unavailable"

    if status not in ['online', 'offline']:
        status = 'online'

    # Check if a device with this IP or name already exists
    try:
        query = client.table('devices').select('*')
        if ip_address:
            query = query.eq('ip_address', ip_address)
        else:
            query = query.eq('device_name', device_name)
        existing = query.execute()

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if existing.data and len(existing.data) > 0:
            dev_id = existing.data[0]['id']
            update_data = {
                'status': status,
                'last_seen_at': now_iso
            }
            if hostname:
                update_data['hostname'] = hostname
            if os_name:
                update_data['os'] = os_name
            res = client.table('devices').update(update_data).eq('id', dev_id).execute()
            return True, res.data[0] if res.data else existing.data[0]

        # Insert new device
        record = {
            'user_id': user_id,
            'device_name': device_name,
            'hostname': hostname or device_name.lower().replace(' ', '-'),
            'ip_address': ip_address,
            'os': os_name or 'Ubuntu Linux',
            'status': status,
            'last_seen_at': now_iso
        }
        res = client.table('devices').insert(record).execute()
        return True, res.data[0] if res.data else record
    except Exception as e:
        print(f"[REPO ERROR: register_device] {e}")
        return False, str(e)


def get_device_by_ip(ip_address: str):
    client = get_supabase_admin()
    if not client:
        return None
    try:
        res = client.table('devices').select('*').eq('ip_address', ip_address).execute()
        return res.data[0] if res.data and len(res.data) > 0 else None
    except Exception as e:
        print(f"[REPO ERROR: get_device_by_ip] {e}")
        return None


def get_device_by_name(device_name: str):
    client = get_supabase_admin()
    if not client:
        return None
    try:
        res = client.table('devices').select('*').eq('device_name', device_name).execute()
        return res.data[0] if res.data and len(res.data) > 0 else None
    except Exception as e:
        print(f"[REPO ERROR: get_device_by_name] {e}")
        return None


def update_device_status(device_id: str, status: str, last_seen_at: str = None):
    client = get_supabase_admin()
    if not client:
        return False, "Database client unavailable"

    if status not in ['online', 'offline']:
        return False, f"Invalid device status '{status}'. Must be 'online' or 'offline'."

    updates = {'status': status}
    if last_seen_at:
        updates['last_seen_at'] = last_seen_at
    else:
        updates['last_seen_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()

    try:
        res = client.table('devices').update(updates).eq('id', device_id).execute()
        return True, res.data
    except Exception as e:
        print(f"[REPO ERROR: update_device_status] {e}")
        return False, str(e)


def list_devices():
    client = get_supabase_admin()
    if not client:
        return []
    try:
        res = client.table('devices').select('*').order('created_at', desc=True).execute()
        return res.data or []
    except Exception as e:
        print(f"[REPO ERROR: list_devices] {e}")
        return []
