from web.database.supabase_client import get_supabase_admin


def get_profile_by_id(user_id: str):
    client = get_supabase_admin()
    if not client:
        return None
    try:
        res = client.table('profiles').select('*').eq('id', user_id).execute()
        if res.data and len(res.data) > 0:
            return res.data[0]
        return None
    except Exception as e:
        print(f"[REPO ERROR: get_profile_by_id] {e}")
        return None


def update_profile(user_id: str, display_name: str = None, role: str = None):
    client = get_supabase_admin()
    if not client:
        return False, "Database client unavailable"
    updates = {}
    if display_name:
        updates['display_name'] = display_name
    if role:
        if role not in ['admin', 'student']:
            return False, f"Invalid role '{role}'. Allowed: 'admin', 'student'"
        updates['role'] = role

    if not updates:
        return True, "No changes requested"

    try:
        res = client.table('profiles').update(updates).eq('id', user_id).execute()
        return True, res.data
    except Exception as e:
        print(f"[REPO ERROR: update_profile] {e}")
        return False, str(e)


def list_profiles():
    client = get_supabase_admin()
    if not client:
        return []
    try:
        res = client.table('profiles').select('*').order('created_at', desc=True).execute()
        return res.data or []
    except Exception as e:
        print(f"[REPO ERROR: list_profiles] {e}")
        return []
