"""
LabNet Trusted User & Role Provisioning Utility (scripts/manage_users.py)
=========================================================================
Admin accounts must NEVER be created via public browser signup forms.
Use this trusted backend CLI utility on VM1 to list users, confirm emails,
or promote a registered student account to the `admin` role in Supabase.

Usage:
  .venv/bin/python scripts/manage_users.py list
  .venv/bin/python scripts/manage_users.py promote <email>
  .venv/bin/python scripts/manage_users.py demote <email>
  .venv/bin/python scripts/manage_users.py confirm <email>
"""

import sys
import httpx
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from web.database.supabase_client import get_config_val
from web.database.repositories.profile_repository import update_profile, list_profiles


def _auth_headers():
    srv = get_config_val("SUPABASE_SERVICE_ROLE_KEY")
    return {"apikey": srv, "Authorization": f"Bearer {srv}", "Content-Type": "application/json"}


def list_all_users():
    url = get_config_val("SUPABASE_URL")
    r = httpx.get(f"{url}/auth/v1/admin/users", headers=_auth_headers(), timeout=10)
    users = r.json().get("users", []) if r.status_code == 200 else []
    profiles = {p["id"]: p for p in list_profiles()}
    print(f"{'EMAIL':<35} {'DISPLAY NAME':<22} {'ROLE':<10} {'CONFIRMED'}")
    print("-" * 80)
    for u in users:
        p = profiles.get(u["id"], {})
        confirmed = "YES" if u.get("email_confirmed_at") else "NO"
        print(f"{u.get('email', '-'):<35} {p.get('display_name', '-'):<22} {p.get('role', '-'):<10} {confirmed}")


def find_user_by_email(email: str):
    url = get_config_val("SUPABASE_URL")
    r = httpx.get(f"{url}/auth/v1/admin/users", headers=_auth_headers(), timeout=10)
    users = r.json().get("users", []) if r.status_code == 200 else []
    target = email.strip().lower()
    for u in users:
        if (u.get("email") or "").lower() == target:
            return u
    return None


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    action = sys.argv[1].lower()
    if action == "list":
        list_all_users()
        return

    if len(sys.argv) < 3:
        print("Error: email address required.")
        sys.exit(1)

    email = sys.argv[2]
    user = find_user_by_email(email)
    if not user:
        print(f"[-] User '{email}' not found in Supabase Auth.")
        sys.exit(1)

    uid = user["id"]
    if action == "promote":
        ok, res = update_profile(uid, role="admin")
        print(f"[+] Promoted {email} to admin." if ok else f"[-] Failed: {res}")
    elif action == "demote":
        ok, res = update_profile(uid, role="student")
        print(f"[+] Demoted {email} to student." if ok else f"[-] Failed: {res}")
    elif action == "confirm":
        url = get_config_val("SUPABASE_URL")
        r = httpx.put(
            f"{url}/auth/v1/admin/users/{uid}",
            headers=_auth_headers(),
            json={"email_confirm": True},
            timeout=10
        )
        print(f"[+] Confirmed email for {email}." if r.status_code == 200 else f"[-] Failed: {r.status_code}")
    else:
        print(f"Unknown action '{action}'. Use: list | promote | demote | confirm")


if __name__ == "__main__":
    main()
