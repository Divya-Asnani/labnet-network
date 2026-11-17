"""
LabNet Mandatory Private Conversation Isolation Test Suite
==========================================================
Tests ALL 6 mandatory isolation scenarios using 3 real Supabase Auth test users
(User A, User B, User C), verifying:
  - Database relational model (`conversations`, `conversation_participants`, `chat_messages.conversation_id`)
  - Atomic duplicate-free direct conversation creation (A-B == B-A)
  - Flask REST API authorization & membership enforcement
  - Socket.IO room isolation (`conversation:<conversation_id>`)
  - Direct PostgreSQL Row Level Security (RLS) using real user JWTs
  - Clean teardown of only synthetic test accounts and rows
"""

import os
import sys
import time
import uuid
import threading
import subprocess
from pathlib import Path
import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from web.database.supabase_client import get_config_val, create_user_scoped_client

SUBPROCS = []


def start_tcp_chat_daemon():
    proc = subprocess.Popen(
        [sys.executable, "-u", "server/tcp_chat_server.py"],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    SUBPROCS.append(proc)
    time.sleep(1.2)
    return proc


def stop_daemons():
    for p in SUBPROCS:
        try:
            p.terminate()
            p.wait(timeout=2.0)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


def verify_migration_applied():
    url = get_config_val("SUPABASE_URL")
    srv = get_config_val("SUPABASE_SERVICE_ROLE_KEY")
    h = {"apikey": srv, "Authorization": f"Bearer {srv}"}
    spec = httpx.get(f"{url}/rest/v1/", headers=h, timeout=10).json()
    defs = spec.get("definitions", {})
    missing = []
    if "conversations" not in defs:
        missing.append("table public.conversations")
    if "conversation_participants" not in defs:
        missing.append("table public.conversation_participants")
    if "chat_messages" not in defs or "conversation_id" not in defs["chat_messages"].get("properties", {}):
        missing.append("column public.chat_messages.conversation_id")
    if missing:
        print("[BLOCKED] Migration 001 has not been applied to Supabase yet. Missing:", ", ".join(missing))
        print("Apply database/migrations/001_private_conversations.sql in Supabase SQL Editor and re-run.")
        sys.exit(2)


def cleanup_synthetic_data(user_ids, conv_ids):
    url = get_config_val("SUPABASE_URL")
    srv = get_config_val("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not srv:
        return
    h = {"apikey": srv, "Authorization": f"Bearer {srv}"}
    for cid in conv_ids:
        httpx.delete(f"{url}/rest/v1/chat_messages?conversation_id=eq.{cid}", headers=h)
        httpx.delete(f"{url}/rest/v1/conversation_participants?conversation_id=eq.{cid}", headers=h)
        httpx.delete(f"{url}/rest/v1/conversations?id=eq.{cid}", headers=h)
    for uid in user_ids:
        httpx.delete(f"{url}/rest/v1/chat_messages?sender_user_id=eq.{uid}", headers=h)
        httpx.delete(f"{url}/rest/v1/profiles?id=eq.{uid}", headers=h)
        httpx.delete(f"{url}/auth/v1/admin/users/{uid}", headers=h)


def create_and_login_user(app, label: str, tag: str):
    """Creates a confirmed Supabase user and returns (http_client, user_dict, access_token)."""
    client = app.test_client()
    email = f"iso.{label.lower()}.{tag}@example.com"
    password = f"IsoPass_{tag}_2026!"
    r_reg = client.post("/api/auth/register", json={
        "display_name": f"User {label}",
        "email": email,
        "password": password
    })
    assert r_reg.status_code == 201, f"Failed creating User {label}: {r_reg.status_code} {r_reg.data}"

    r_login = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r_login.status_code == 200, f"Failed logging in User {label}: {r_login.status_code} {r_login.data}"

    # Extract Supabase JWT from session for direct PostgREST RLS verification
    with client.session_transaction() as sess:
        jwt_token = sess["user"]["access_token"]
        user_info = sess["user"]

    return client, user_info, jwt_token, email, password


def drain_events(sio_client, event_name):
    return [e for e in sio_client.get_received() if e["name"] == event_name]


def run_isolation_tests():
    verify_migration_applied()

    print("=================================================================")
    print("   LABNET PRIVATE CONVERSATION ISOLATION TEST SUITE (6 TESTS)    ")
    print("=================================================================")

    start_tcp_chat_daemon()

    from web.app import app as flask_app, socketio
    time.sleep(1.0)

    tag = uuid.uuid4().hex[:8]
    created_user_ids = []
    created_conv_ids = []

    try:
        # Provision 3 isolated test users: A, B, C
        print(f"\n[*] Provisioning 3 live Supabase test users (A, B, C) [tag={tag}]...")
        client_a, user_a, jwt_a, email_a, pass_a = create_and_login_user(flask_app, "A", tag)
        client_b, user_b, jwt_b, email_b, pass_b = create_and_login_user(flask_app, "B", tag)
        client_c, user_c, jwt_c, email_c, pass_c = create_and_login_user(flask_app, "C", tag)
        created_user_ids.extend([user_a["id"], user_b["id"], user_c["id"]])
        print(f"[+] Users provisioned: A={user_a['id'][:8]}, B={user_b['id'][:8]}, C={user_c['id'][:8]}")

        # Verify atomic duplicate-free direct conversation creation (A-B == B-A)
        r_ab_1 = client_a.post("/api/chat/conversations/direct", json={"recipient_user_id": user_b["id"]})
        assert r_ab_1.status_code == 200, f"Create A-B failed: {r_ab_1.data}"
        conv_ab = r_ab_1.get_json()["conversation"]["id"]
        created_conv_ids.append(conv_ab)

        r_ba_2 = client_b.post("/api/chat/conversations/direct", json={"recipient_user_id": user_a["id"]})
        assert r_ba_2.status_code == 200
        assert r_ba_2.get_json()["conversation"]["id"] == conv_ab, "Duplicate direct conversation created for B-A!"

        r_ac = client_a.post("/api/chat/conversations/direct", json={"recipient_user_id": user_c["id"]})
        assert r_ac.status_code == 200
        conv_ac = r_ac.get_json()["conversation"]["id"]
        created_conv_ids.append(conv_ac)
        assert conv_ab != conv_ac
        print(f"[PASS] Atomic direct conversations verified (zero duplicates): A-B={conv_ab[:8]}, A-C={conv_ac[:8]}")

        # Connect Socket.IO test clients authenticated via Flask sessions
        sio_a = socketio.test_client(flask_app, flask_test_client=client_a)
        sio_b = socketio.test_client(flask_app, flask_test_client=client_b)
        sio_c = socketio.test_client(flask_app, flask_test_client=client_c)

        # Clear initial connection_status events
        sio_a.get_received()
        sio_b.get_received()
        sio_c.get_received()

        # ------------------------------------------------------------------
        # TEST 1: A-B CONVERSATION ISOLATION
        # ------------------------------------------------------------------
        print("\n--- TEST 1: A-B CONVERSATION ---")
        sio_a.emit("join_conversation", {"conversation_id": conv_ab})
        sio_b.emit("join_conversation", {"conversation_id": conv_ab})
        sio_c.emit("join_conversation", {"conversation_id": conv_ac})
        sio_a.get_received()
        sio_b.get_received()
        sio_c.get_received()

        sio_a.emit("chat_send", {"conversation_id": conv_ab, "text": f"Hello B {tag}"})
        time.sleep(0.6)

        b_msgs = drain_events(sio_b, "chat_message")
        c_msgs = drain_events(sio_c, "chat_message")
        assert any(f"Hello B {tag}" in e["args"][0]["text"] for e in b_msgs), f"B did not receive 'Hello B': {b_msgs}"
        assert not any(f"Hello B {tag}" in str(e) for e in c_msgs), f"LEAK! C received A-B message: {c_msgs}"

        # Confirm C does not see A-B in conversation list or A-C history
        c_convs = client_c.get("/api/chat/conversations").get_json()["conversations"]
        assert not any(c["id"] == conv_ab for c in c_convs), "LEAK! C sees A-B in conversation list!"
        c_ac_hist = client_c.get(f"/api/chat/conversations/{conv_ac}/messages").get_json()["messages"]
        assert not any(f"Hello B {tag}" in m["text"] for m in c_ac_hist), "LEAK! A-B message visible in A-C!"
        print("[PASS] TEST 1: A sent 'Hello B' -> B received; C received nothing and cannot see it anywhere.")

        # ------------------------------------------------------------------
        # TEST 2: A-C CONVERSATION ISOLATION
        # ------------------------------------------------------------------
        print("\n--- TEST 2: A-C CONVERSATION ---")
        sio_a.emit("join_conversation", {"conversation_id": conv_ac})
        sio_a.get_received()
        sio_b.get_received()
        sio_c.get_received()

        sio_a.emit("chat_send", {"conversation_id": conv_ac, "text": f"Hello C {tag}"})
        time.sleep(0.6)

        c_msgs_2 = drain_events(sio_c, "chat_message")
        b_msgs_2 = drain_events(sio_b, "chat_message")
        assert any(f"Hello C {tag}" in e["args"][0]["text"] for e in c_msgs_2), f"C did not receive 'Hello C': {c_msgs_2}"
        assert not any(f"Hello C {tag}" in str(e) for e in b_msgs_2), f"LEAK! B received A-C message: {b_msgs_2}"

        b_convs = client_b.get("/api/chat/conversations").get_json()["conversations"]
        assert not any(c["id"] == conv_ac for c in b_convs), "LEAK! B sees A-C in conversation list!"
        b_ab_hist = client_b.get(f"/api/chat/conversations/{conv_ab}/messages").get_json()["messages"]
        assert not any(f"Hello C {tag}" in m["text"] for m in b_ab_hist), "LEAK! A-C message visible in A-B!"
        print("[PASS] TEST 2: A sent 'Hello C' -> C received; B received nothing and cannot see it anywhere.")

        # ------------------------------------------------------------------
        # TEST 3: CONVERSATION SWITCHING (A: A-B -> A-C)
        # ------------------------------------------------------------------
        print("\n--- TEST 3: CONVERSATION SWITCHING ---")
        sio_a.emit("join_conversation", {"conversation_id": conv_ab})
        sio_a.emit("chat_send", {"conversation_id": conv_ab, "text": f"SwitchABOnly {tag}"})
        time.sleep(0.5)

        # Switch A to A-C and verify A-B messages are NOT present in A-C history
        sio_a.emit("join_conversation", {"conversation_id": conv_ac})
        a_ac_msgs = client_a.get(f"/api/chat/conversations/{conv_ac}/messages").get_json()["messages"]
        assert any(f"Hello C {tag}" in m["text"] for m in a_ac_msgs)
        assert not any(f"SwitchABOnly {tag}" in m["text"] for m in a_ac_msgs), "LEAK! A-B message appeared in A-C!"
        assert not any(f"Hello B {tag}" in m["text"] for m in a_ac_msgs), "LEAK! A-B message appeared in A-C!"

        # While A is in A-C, B sends to A-B -> A's socket must NOT receive A-B chat_message in A-C room
        sio_a.get_received()
        sio_b.emit("chat_send", {"conversation_id": conv_ab, "text": f"WhileAInAC {tag}"})
        time.sleep(0.5)
        a_live_in_ac = drain_events(sio_a, "chat_message")
        assert not any(f"WhileAInAC {tag}" in str(e) for e in a_live_in_ac), "LEAK! A received A-B room message while switched to A-C!"
        print("[PASS] TEST 3: Switching A from A-B to A-C cleanly isolated history and Socket.IO room delivery.")

        # ------------------------------------------------------------------
        # TEST 4: OFFLINE HISTORY PERSISTENCE & ISOLATION
        # ------------------------------------------------------------------
        print("\n--- TEST 4: OFFLINE HISTORY ---")
        sio_b.disconnect()
        client_b.get("/logout")

        r_off = client_a.post(f"/api/chat/conversations/{conv_ab}/messages", json={"message": f"OfflineForB {tag}"})
        assert r_off.status_code == 200

        # B logs back in later with a fresh client
        fresh_b = flask_app.test_client()
        assert fresh_b.post("/api/auth/login", json={"email": email_b, "password": pass_b}).status_code == 200

        b_reconn_msgs = fresh_b.get(f"/api/chat/conversations/{conv_ab}/messages").get_json()["messages"]
        assert any(f"OfflineForB {tag}" in m["text"] for m in b_reconn_msgs), "Offline message missing when B reconnected!"

        # Verify offline message did NOT leak into A-C
        ac_check = client_a.get(f"/api/chat/conversations/{conv_ac}/messages").get_json()["messages"]
        assert not any(f"OfflineForB {tag}" in m["text"] for m in ac_check), "Offline message leaked into A-C!"
        print("[PASS] TEST 4: Offline message retrieved cleanly by B in A-B upon re-login; absent from A-C.")

        # ------------------------------------------------------------------
        # TEST 5: UNAUTHORIZED ACCESS (REST + SOCKET.IO + DIRECT SUPABASE RLS)
        # ------------------------------------------------------------------
        print("\n--- TEST 5: UNAUTHORIZED ACCESS & LIVE SUPABASE RLS ---")
        # 5A: REST API rejection
        assert client_c.get(f"/api/chat/conversations/{conv_ab}").status_code == 403
        assert client_c.get(f"/api/chat/conversations/{conv_ab}/messages").status_code == 403
        assert client_c.post(f"/api/chat/conversations/{conv_ab}/messages", json={"message": "Intrusion"}).status_code == 403
        assert fresh_b.get(f"/api/chat/conversations/{conv_ac}/messages").status_code == 403
        assert fresh_b.post(f"/api/chat/conversations/{conv_ac}/messages", json={"message": "Intrusion"}).status_code == 403
        assert client_b.get(f"/api/chat/conversations/{conv_ac}/messages").status_code == 401

        # 5B: Socket.IO room join & send rejection
        sio_c.get_received()
        sio_c.emit("join_conversation", {"conversation_id": conv_ab})
        c_errs = drain_events(sio_c, "conversation_error")
        assert any(e["args"][0].get("code") == "forbidden" for e in c_errs), f"C was not rejected from joining A-B room: {c_errs}"

        sio_c.emit("chat_send", {"conversation_id": conv_ab, "text": "SocketIntrusion"})
        c_chat_errs = drain_events(sio_c, "chat_error")
        assert any(e["args"][0].get("code") == "forbidden" for e in c_chat_errs), f"C was not rejected sending to A-B: {c_chat_errs}"

        # 5C: DIRECT LIVE SUPABASE RLS VERIFICATION USING USER C'S JWT
        supa_c = create_user_scoped_client(jwt_c)
        rls_c_read_ab = supa_c.table("chat_messages").select("*").eq("conversation_id", conv_ab).execute()
        assert len(rls_c_read_ab.data or []) == 0, f"RLS BREACH! C read A-B rows directly via Supabase: {rls_c_read_ab.data}"

        rls_insert_blocked = False
        try:
            supa_c.table("chat_messages").insert({
                "conversation_id": conv_ab,
                "sender_user_id": user_c["id"],
                "message": "Direct RLS Intrusion",
                "protocol": "TCP",
                "status": "sent"
            }).execute()
        except Exception:
            rls_insert_blocked = True
        assert rls_insert_blocked, "RLS BREACH! C inserted directly into A-B via Supabase PostgREST!"

        # Verify authorized user A CAN read A-B directly under RLS
        supa_a = create_user_scoped_client(jwt_a)
        rls_a_read_ab = supa_a.table("chat_messages").select("*").eq("conversation_id", conv_ab).execute()
        assert len(rls_a_read_ab.data or []) >= 2, "Authorized member A could not read A-B under RLS!"
        print("[PASS] TEST 5: Unauthorized REST (403), Socket.IO (forbidden), and direct Supabase RLS read/insert strictly blocked.")

        # ------------------------------------------------------------------
        # TEST 6: CONCURRENT CONVERSATIONS (A-B & A-C SIMULTANEOUSLY)
        # ------------------------------------------------------------------
        print("\n--- TEST 6: CONCURRENT CONVERSATIONS ---")
        errors = []

        def send_batch(http_cli, conv_id, prefix):
            try:
                for i in range(1, 6):
                    resp = http_cli.post(f"/api/chat/conversations/{conv_id}/messages", json={"message": f"{prefix}-{i}-{tag}"})
                    assert resp.status_code == 200, f"Batch {prefix}-{i} failed: {resp.status_code}"
            except Exception as ex:
                errors.append(ex)

        t1 = threading.Thread(target=send_batch, args=(fresh_b, conv_ab, "CONC_AB"))
        t2 = threading.Thread(target=send_batch, args=(client_c, conv_ac, "CONC_AC"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert not errors, f"Concurrent thread errors: {errors}"

        final_ab = [m["text"] for m in fresh_b.get(f"/api/chat/conversations/{conv_ab}/messages").get_json()["messages"]]
        final_ac = [m["text"] for m in client_c.get(f"/api/chat/conversations/{conv_ac}/messages").get_json()["messages"]]

        for i in range(1, 6):
            assert f"CONC_AB-{i}-{tag}" in final_ab
            assert f"CONC_AB-{i}-{tag}" not in final_ac
            assert f"CONC_AC-{i}-{tag}" in final_ac
            assert f"CONC_AC-{i}-{tag}" not in final_ab

        print("[PASS] TEST 6: Concurrent A-B and A-C message batches persisted & isolated with zero cross-contamination.")

        sio_a.disconnect()
        sio_c.disconnect()

        print("\n=================================================================")
        print("   ALL 6 MANDATORY CONVERSATION ISOLATION TESTS PASSED (100%)!   ")
        print("=================================================================")

    finally:
        stop_daemons()
        cleanup_synthetic_data(created_user_ids, created_conv_ids)
        print("[*] Synthetic test users (A, B, C) and test conversations cleaned up.")


if __name__ == "__main__":
    run_isolation_tests()
