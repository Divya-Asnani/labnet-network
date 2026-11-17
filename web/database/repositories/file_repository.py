import datetime
from web.database.supabase_client import get_supabase_admin


def record_file_transfer(
    sender_user_id: str,
    file_name: str,
    file_size: int,
    protocol: str = "TCP",
    checksum: str = None,
    storage_path: str = None,
    status: str = "in_progress",
    sender_device_id: str = None,
    receiver_device_id: str = None
):
    client = get_supabase_admin()
    if not client:
        return False, "Database client unavailable"

    proto = (protocol or "TCP").upper()
    if proto not in ["TCP", "UDP"]:
        proto = "TCP"

    if status not in ["pending", "in_progress", "completed", "failed"]:
        status = "in_progress"

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    record = {
        "sender_user_id": sender_user_id,
        "file_name": file_name,
        "file_size": int(file_size),
        "protocol": proto,
        "status": status,
        "started_at": now_iso
    }
    if checksum:
        record["checksum"] = checksum
    if storage_path:
        record["storage_path"] = storage_path
    if sender_device_id:
        record["sender_device_id"] = sender_device_id
    if receiver_device_id:
        record["receiver_device_id"] = receiver_device_id
    if status in ["completed", "failed"]:
        record["completed_at"] = now_iso

    try:
        res = client.table("file_transfers").insert(record).execute()
        return True, res.data[0] if res.data else record
    except Exception as e:
        print(f"[REPO ERROR: record_file_transfer] {e}")
        return False, str(e)


def update_transfer_status(transfer_id: str, status: str, checksum: str = None, storage_path: str = None):
    client = get_supabase_admin()
    if not client:
        return False, "Database client unavailable"

    if status not in ["pending", "in_progress", "completed", "failed"]:
        return False, f"Invalid transfer status '{status}'"

    updates = {"status": status}
    if checksum:
        updates["checksum"] = checksum
    if storage_path:
        updates["storage_path"] = storage_path
    if status in ["completed", "failed"]:
        updates["completed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

    try:
        res = client.table("file_transfers").update(updates).eq("id", transfer_id).execute()
        return True, res.data
    except Exception as e:
        print(f"[REPO ERROR: update_transfer_status] {e}")
        return False, str(e)


def get_transfer_by_id(transfer_id: str):
    client = get_supabase_admin()
    if not client:
        return None
    try:
        res = client.table("file_transfers").select("*, profiles(display_name, role)").eq("id", transfer_id).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[REPO ERROR: get_transfer_by_id] {e}")
        return None


def get_transfer_by_filename(filename: str):
    client = get_supabase_admin()
    if not client:
        return None
    try:
        res = (
            client.table("file_transfers")
            .select("*, profiles(display_name, role)")
            .eq("file_name", filename)
            .order("started_at", desc=True)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[REPO ERROR: get_transfer_by_filename] {e}")
        return None


def list_transfers(limit: int = 50):
    client = get_supabase_admin()
    if not client:
        return []
    try:
        res = (
            client.table("file_transfers")
            .select("*, profiles(display_name, role)")
            .order("started_at", desc=True)
            .limit(int(limit))
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"[REPO ERROR: list_transfers] {e}")
        return []
