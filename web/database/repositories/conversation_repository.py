"""
LabNet Conversation Repository (web/database/repositories/conversation_repository.py)
===================================================================================
Manages strictly isolated private conversations (`conversations` and
`conversation_participants`) in Supabase PostgreSQL.

Security Invariant:
  Every backend operation explicitly checks participant membership before
  returning conversation metadata, participants, or messages, matching
  PostgreSQL Row Level Security (RLS) policies even when executing via
  the trusted backend service-role client.
"""

import time
import threading
import uuid
from web.database.supabase_client import get_supabase_admin

_member_cache_lock = threading.Lock()
_member_cache = {}  # {(conversation_id, user_id): expiry_timestamp}
_MEMBER_CACHE_TTL = 60.0


def _is_valid_uuid(val: str) -> bool:
    try:
        uuid.UUID(str(val))
        return True
    except (ValueError, TypeError):
        return False


def _canonical_direct_key(user_a: str, user_b: str) -> str:
    a, b = str(user_a).lower(), str(user_b).lower()
    return f"{a}:{b}" if a < b else f"{b}:{a}"


def is_user_participant(conversation_id: str, user_id: str, bypass_cache: bool = False) -> bool:
    """Authoritatively verifies whether `user_id` is a participant in `conversation_id`."""
    if not _is_valid_uuid(conversation_id) or not _is_valid_uuid(user_id):
        return False

    cache_key = (str(conversation_id), str(user_id))
    now = time.time()
    if not bypass_cache:
        with _member_cache_lock:
            exp = _member_cache.get(cache_key)
            if exp and exp > now:
                return True

    client = get_supabase_admin()
    if not client:
        return False
    try:
        res = (
            client.table("conversation_participants")
            .select("conversation_id")
            .eq("conversation_id", str(conversation_id))
            .eq("user_id", str(user_id))
            .limit(1)
            .execute()
        )
        is_member = bool(res.data)
        if is_member:
            with _member_cache_lock:
                _member_cache[cache_key] = now + _MEMBER_CACHE_TTL
        return is_member
    except Exception as e:
        print(f"[REPO ERROR: is_user_participant] {e}")
        return False


def get_conversation_participants(conversation_id: str, requesting_user_id: str):
    """Returns participants for a conversation ONLY if requesting_user_id is a member."""
    if not is_user_participant(conversation_id, requesting_user_id):
        return False, "Forbidden: you are not a participant in this conversation.", 403

    client = get_supabase_admin()
    if not client:
        return False, "Database unavailable.", 503
    try:
        res = (
            client.table("conversation_participants")
            .select("conversation_id, user_id, joined_at, profiles(id, display_name, role)")
            .eq("conversation_id", str(conversation_id))
            .execute()
        )
        participants = []
        for row in (res.data or []):
            prof = row.get("profiles") or {}
            participants.append({
                "user_id": row["user_id"],
                "display_name": prof.get("display_name") or "LabNet User",
                "role": prof.get("role") or "student",
                "joined_at": row.get("joined_at")
            })
        return True, participants, 200
    except Exception as e:
        print(f"[REPO ERROR: get_conversation_participants] {e}")
        return False, str(e), 500


def get_or_create_direct_conversation(user_a: str, user_b: str):
    """
    Atomically retrieves or creates the single direct conversation between `user_a`
    and `user_b`. Guarantees no duplicate direct conversations exist for the same pair.
    """
    if not _is_valid_uuid(user_a) or not _is_valid_uuid(user_b):
        return False, "Valid user UUIDs are required.", 400
    if str(user_a) == str(user_b):
        return False, "Cannot start a direct conversation with yourself.", 400

    client = get_supabase_admin()
    if not client:
        return False, "Database unavailable.", 503

    # Verify target user exists in profiles
    try:
        target_check = client.table("profiles").select("id, display_name, role").eq("id", str(user_b)).limit(1).execute()
        if not target_check.data:
            return False, "Target recipient user does not exist.", 404
    except Exception as e:
        return False, str(e), 500

    # Primary atomic path: PostgreSQL transactional RPC function
    conv_id = None
    try:
        rpc_res = client.rpc(
            "get_or_create_direct_conversation",
            {"user_a": str(user_a), "user_b": str(user_b)}
        ).execute()
        if rpc_res.data:
            conv_id = str(rpc_res.data)
    except Exception as rpc_err:
        # Fallback using unique `direct_key` constraint if PostgREST schema cache is still refreshing
        print(f"[CONV RPC FALLBACK] {rpc_err}")
        dkey = _canonical_direct_key(user_a, user_b)
        try:
            existing = (
                client.table("conversations")
                .select("id")
                .eq("type", "direct")
                .eq("direct_key", dkey)
                .limit(1)
                .execute()
            )
            if existing.data:
                conv_id = existing.data[0]["id"]
            else:
                created = (
                    client.table("conversations")
                    .insert({"type": "direct", "direct_key": dkey})
                    .execute()
                )
                conv_id = created.data[0]["id"]

            # Ensure both participants exist idempotently
            client.table("conversation_participants").upsert(
                [
                    {"conversation_id": conv_id, "user_id": str(user_a)},
                    {"conversation_id": conv_id, "user_id": str(user_b)}
                ],
                on_conflict="conversation_id,user_id"
            ).execute()
        except Exception as fb_err:
            # Handle concurrent insert race gracefully by re-selecting on direct_key
            try:
                existing = (
                    client.table("conversations")
                    .select("id")
                    .eq("direct_key", dkey)
                    .limit(1)
                    .execute()
                )
                if existing.data:
                    conv_id = existing.data[0]["id"]
                else:
                    return False, f"Failed to create direct conversation: {fb_err}", 500
            except Exception as err2:
                return False, f"Failed to resolve direct conversation: {err2}", 500

    return get_conversation_detail(conv_id, user_a)


def get_conversation_detail(conversation_id: str, requesting_user_id: str):
    """Retrieves full conversation metadata + participants strictly for an authorized member."""
    if not is_user_participant(conversation_id, requesting_user_id):
        return False, "Forbidden: you are not a participant in this conversation.", 403

    client = get_supabase_admin()
    if not client:
        return False, "Database unavailable.", 503

    try:
        conv_res = (
            client.table("conversations")
            .select("id, type, title, created_at")
            .eq("id", str(conversation_id))
            .limit(1)
            .execute()
        )
        if not conv_res.data:
            return False, "Conversation not found.", 404
        conv = conv_res.data[0]

        ok_p, participants, status_p = get_conversation_participants(conversation_id, requesting_user_id)
        if not ok_p:
            return False, participants, status_p

        others = [p for p in participants if p["user_id"] != str(requesting_user_id)]
        other = others[0] if others else None
        display_title = conv.get("title") or (other["display_name"] if other else "Conversation")

        return True, {
            "id": conv["id"],
            "type": conv["type"],
            "title": display_title,
            "other_user": other,
            "participants": participants,
            "created_at": conv.get("created_at")
        }, 200
    except Exception as e:
        print(f"[REPO ERROR: get_conversation_detail] {e}")
        return False, str(e), 500


def list_user_conversations(user_id: str, online_user_names: set = None):
    """
    Returns ONLY conversations where `user_id` is an authorized participant,
    enriched with other participants' profiles and the latest message belonging
    strictly to each individual conversation.
    """
    if not _is_valid_uuid(user_id):
        return []
    client = get_supabase_admin()
    if not client:
        return []

    online_names = online_user_names or set()
    try:
        # 1. Fetch strictly the conversation IDs this user participates in
        mem_res = (
            client.table("conversation_participants")
            .select("conversation_id")
            .eq("user_id", str(user_id))
            .execute()
        )
        conv_ids = [r["conversation_id"] for r in (mem_res.data or [])]
        if not conv_ids:
            return []

        # 2. Fetch conversation metadata
        conv_res = (
            client.table("conversations")
            .select("id, type, title, created_at")
            .in_("id", conv_ids)
            .execute()
        )
        conv_map = {c["id"]: c for c in (conv_res.data or [])}

        # 3. Fetch participants for ONLY those authorized conversations
        part_res = (
            client.table("conversation_participants")
            .select("conversation_id, user_id, joined_at, profiles(id, display_name, role)")
            .in_("conversation_id", conv_ids)
            .execute()
        )
        parts_by_conv = {cid: [] for cid in conv_ids}
        for row in (part_res.data or []):
            prof = row.get("profiles") or {}
            parts_by_conv.setdefault(row["conversation_id"], []).append({
                "user_id": row["user_id"],
                "display_name": prof.get("display_name") or "LabNet User",
                "role": prof.get("role") or "student"
            })

        # 4. Build each conversation with its OWN isolated latest message
        results = []
        for cid in conv_ids:
            conv = conv_map.get(cid)
            if not conv:
                continue
            participants = parts_by_conv.get(cid, [])
            others = [p for p in participants if p["user_id"] != str(user_id)]
            other = others[0] if others else None
            title = conv.get("title") or (other["display_name"] if other else "Direct Chat")

            # Fetch strictly the newest message belonging to THIS conversation_id
            last_res = (
                client.table("chat_messages")
                .select("id, sender_user_id, message, sent_at, status, profiles(display_name)")
                .eq("conversation_id", cid)
                .order("sent_at", desc=True)
                .limit(1)
                .execute()
            )
            last_row = last_res.data[0] if last_res.data else None
            last_sent_at = last_row["sent_at"] if last_row else conv.get("created_at", "")

            results.append({
                "id": cid,
                "type": conv.get("type", "direct"),
                "title": title,
                "subtitle": f"{(other['role']).capitalize()} • Private Direct Chat" if other else "Private Conversation",
                "other_user": other,
                "participants": participants,
                "online": bool(other and other["display_name"] in online_names),
                "last_message": last_row["message"] if last_row else "No messages yet",
                "last_sender": ((last_row.get("profiles") or {}).get("display_name") or "") if last_row else "",
                "last_timestamp": last_sent_at.replace("T", " ")[:19] if last_sent_at else "",
                "sort_key": last_sent_at or ""
            })

        # Order newest active conversation first
        results.sort(key=lambda c: c["sort_key"], reverse=True)
        return results
    except Exception as e:
        print(f"[REPO ERROR: list_user_conversations] {e}")
        return []


def list_available_contacts(current_user_id: str, online_user_names: set = None):
    """Lists registered LabNet users (other than current_user_id) to start private direct chats."""
    client = get_supabase_admin()
    if not client:
        return []
    online_names = online_user_names or set()
    try:
        res = client.table("profiles").select("id, display_name, role, created_at").order("display_name").execute()
        contacts = []
        for p in (res.data or []):
            if str(p["id"]) == str(current_user_id):
                continue
            contacts.append({
                "user_id": p["id"],
                "display_name": p.get("display_name") or "LabNet User",
                "role": p.get("role") or "student",
                "online": p.get("display_name") in online_names
            })
        return contacts
    except Exception as e:
        print(f"[REPO ERROR: list_available_contacts] {e}")
        return []
