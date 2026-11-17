import re
import datetime
from web.database.supabase_client import get_supabase_admin


def _parse_iso_utc(ts_str: str) -> datetime.datetime:
    """Parses PostgreSQL ISO-8601 timestamps safely on Python 3.10 (pads 1-5 digit fractional seconds)."""
    ts = str(ts_str).strip().replace("Z", "+00:00")
    ts = re.sub(r"\.(\d+)", lambda m: "." + m.group(1).ljust(6, "0")[:6], ts)
    dt = datetime.datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def register_device(
    user_id: str,
    device_name: str,
    hostname: str = None,
    ip_address: str = None,
    os_name: str = None,
    status: str = "online"
):
    client = get_supabase_admin()
    if not client:
        return False, "Database client unavailable"

    if status not in ["online", "offline"]:
        status = "online"

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        # Match strictly by device_name so distinct devices belonging to the same user never overwrite each other
        existing = None
        if device_name:
            res_name = client.table("devices").select("*").eq("device_name", device_name).execute()
            if res_name.data:
                existing = res_name.data[0]

        if existing:
            dev_id = existing["id"]
            update_data = {
                "status": status,
                "last_seen_at": now_iso
            }
            if ip_address:
                update_data["ip_address"] = ip_address
            if hostname:
                update_data["hostname"] = hostname
            if os_name:
                update_data["os"] = os_name
            res = client.table("devices").update(update_data).eq("id", dev_id).execute()
            return True, res.data[0] if res.data else {**existing, **update_data}

        record = {
            "user_id": user_id,
            "device_name": device_name,
            "hostname": hostname or device_name.lower().replace(" ", "-"),
            "ip_address": ip_address,
            "os": os_name or "Ubuntu Linux",
            "status": status,
            "last_seen_at": now_iso
        }
        res = client.table("devices").insert(record).execute()
        return True, res.data[0] if res.data else record
    except Exception as e:
        print(f"[REPO ERROR: register_device] {e}")
        return False, str(e)


def get_device_by_id(device_id: str):
    client = get_supabase_admin()
    if not client:
        return None
    try:
        res = client.table("devices").select("*").eq("id", device_id).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[REPO ERROR: get_device_by_id] {e}")
        return None


def get_device_by_ip(ip_address: str):
    client = get_supabase_admin()
    if not client:
        return None
    try:
        res = client.table("devices").select("*").eq("ip_address", ip_address).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[REPO ERROR: get_device_by_ip] {e}")
        return None


def get_device_by_name(device_name: str):
    client = get_supabase_admin()
    if not client:
        return None
    try:
        res = client.table("devices").select("*").eq("device_name", device_name).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[REPO ERROR: get_device_by_name] {e}")
        return None


def update_device_status(device_id: str, status: str, update_last_seen: bool = True):
    client = get_supabase_admin()
    if not client:
        return False, "Database client unavailable"

    if status not in ["online", "offline"]:
        return False, f"Invalid device status '{status}'. Must be 'online' or 'offline'."

    updates = {"status": status}
    if update_last_seen:
        updates["last_seen_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

    try:
        res = client.table("devices").update(updates).eq("id", device_id).execute()
        return True, res.data
    except Exception as e:
        print(f"[REPO ERROR: update_device_status] {e}")
        return False, str(e)


def list_devices():
    client = get_supabase_admin()
    if not client:
        return []
    try:
        res = (
            client.table("devices")
            .select("*, profiles(display_name, role)")
            .order("last_seen_at", desc=True)
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"[REPO ERROR: list_devices] {e}")
        return []


def enforce_heartbeat_timeouts(timeout_seconds: float = 10.0):
    """
    Checks all devices marked `online` in Supabase. If `now - last_seen_at > timeout_seconds`,
    marks them `offline` and records a timeout event in `network_events`.
    Returns the list of devices that transitioned to offline.
    """
    client = get_supabase_admin()
    if not client:
        return []

    transitioned = []
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    try:
        res = client.table("devices").select("*").eq("status", "online").execute()
        online_devs = res.data or []
        for dev in online_devs:
            last_seen_str = dev.get("last_seen_at")
            if not last_seen_str:
                continue
            try:
                last_dt = _parse_iso_utc(last_seen_str)
                age = (now_utc - last_dt).total_seconds()
                if age > float(timeout_seconds):
                    # Mark offline without modifying last_seen_at so user sees exact last heartbeat time
                    client.table("devices").update({"status": "offline"}).eq("id", dev["id"]).execute()
                    client.table("network_events").insert({
                        "device_id": dev["id"],
                        "event_type": "timeout",
                        "protocol": "UDP",
                        "source_ip": dev.get("ip_address"),
                        "details": {
                            "device_name": dev.get("device_name"),
                            "reason": f"No heartbeat received for {round(age, 1)}s (timeout={timeout_seconds}s)"
                        },
                        "created_at": now_utc.isoformat()
                    }).execute()
                    transitioned.append(dev)
            except Exception as parse_err:
                print(f"[TIMEOUT CHECK PARSE ERROR] {parse_err}")
    except Exception as e:
        print(f"[REPO ERROR: enforce_heartbeat_timeouts] {e}")

    return transitioned
