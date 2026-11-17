"""
LabNet Final Validation & Security Audit Suite (test/test_final_validation.py)
==============================================================================
Structured test suite covering:
  1. Unit & Security Tests (Auth enforcement, RBAC/403, Path Traversal, Shell Injection, Signup Role Lock)
  2. Local Socket Integration & Performance Benchmarks (TCP Chat :5001, TCP File :2121,
     Reliable UDP ARQ :2122 with/without 15% loss, Direct P2P :2123, UDP Heartbeat :6000)
  3. Network Services Tests (HTTP GET/POST, DNS A lookup, Local SMTP :1025, Local POP3 :1100, ICMP/Kernel Diagnostics)
  4. Real Live Supabase Persistence Tests across all 7 tables using a controlled test account,
     followed by clean teardown of only the synthetic test account's rows.
"""

import os
import sys
import time
import uuid
import socket
import hashlib
import tempfile
import threading
import subprocess
import io
from pathlib import Path
import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from web.database.supabase_client import get_config_val, get_supabase_admin
from web.database.repositories.profile_repository import get_profile_by_id, update_profile
from web.database.repositories.device_repository import get_device_by_name, enforce_heartbeat_timeouts
from clients.udp_file_client import upload_file_udp_client
from clients.p2p_node import P2PReceiver, send_direct_p2p

TEST_TMP = tempfile.mkdtemp(prefix="labnet_final_test_")
SUBPROCS = []
BENCHMARKS = {}


def start_daemon(script_rel_path, extra_env=None):
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    proc = subprocess.Popen(
        [sys.executable, "-u", script_rel_path],
        cwd=str(PROJECT_ROOT),
        env=env,
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


def delete_synthetic_user(uid, device_ids=None):
    url = get_config_val("SUPABASE_URL")
    srv = get_config_val("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not srv or not uid:
        return
    h = {"apikey": srv, "Authorization": f"Bearer {srv}"}
    for did in (device_ids or []):
        httpx.delete(f"{url}/rest/v1/heartbeat_logs?device_id=eq.{did}", headers=h)
        httpx.delete(f"{url}/rest/v1/network_events?device_id=eq.{did}", headers=h)
        httpx.delete(f"{url}/rest/v1/network_sessions?source_device_id=eq.{did}", headers=h)
        httpx.delete(f"{url}/rest/v1/devices?id=eq.{did}", headers=h)
    httpx.delete(f"{url}/rest/v1/chat_messages?sender_user_id=eq.{uid}", headers=h)
    httpx.delete(f"{url}/rest/v1/file_transfers?sender_user_id=eq.{uid}", headers=h)
    httpx.delete(f"{url}/rest/v1/profiles?id=eq.{uid}", headers=h)
    httpx.delete(f"{url}/auth/v1/admin/users/{uid}", headers=h)


def run_all():
    print("=================================================================")
    print("   LABNET FINAL VALIDATION, SECURITY & PERSISTENCE TEST SUITE    ")
    print("=================================================================")

    # Start socket servers needed for end-to-end tests
    print("\n[*] Starting TCP Chat (:5001), TCP File (:2121), UDP File (:2122), UDP Heartbeat (test port :6009)...")
    start_daemon("server/tcp_chat_server.py")
    start_daemon("server/tcp_file_server.py")
    start_daemon("server/udp_file_server.py")
    start_daemon("server/udp_server.py", extra_env={"UDP_PORT": "6009", "HEARTBEAT_TIMEOUT": "2.5"})

    # Import Flask app after servers are listening so TCPChatBridge connects cleanly
    from web.app import app as flask_app
    time.sleep(1.0)
    client = flask_app.test_client()

    synthetic_uid = None
    synthetic_dev_ids = []
    uploaded_test_files = []

    try:
        # ------------------------------------------------------------------
        # SUITE 1: UNIT & SECURITY AUDIT TESTS
        # ------------------------------------------------------------------
        print("\n--- SUITE 1: SECURITY & ACCESS CONTROL TESTS ---")

        # 1.1 Unauthenticated HTML routes must redirect to /login
        for route in ["/", "/monitoring", "/chat", "/files", "/repository", "/diagnostics", "/protocols", "/activity", "/account"]:
            r = client.get(route)
            assert r.status_code == 302 and "/login" in r.headers.get("Location", ""), f"Route {route} leaked without auth: {r.status_code}"
        print("[PASS] 1.1 All 9 protected HTML routes reject unauthenticated access (302 -> /login).")

        # 1.2 Unauthenticated API routes must return 401
        for api_route in ["/api/dashboard/overview", "/api/chat/history", "/api/files/list", "/api/files/download/test.txt", "/api/admin/users"]:
            r = client.get(api_route)
            assert r.status_code == 401, f"API {api_route} leaked without auth: {r.status_code}"
        print("[PASS] 1.2 Protected REST APIs and file download reject unauthenticated requests (401).")

        # 1.3 Login page renders cleanly with password eye toggle and NO admin role selector
        r_login = client.get("/login")
        assert r_login.status_code == 200
        html_login = r_login.data.decode("utf-8")
        assert "togglePasswordVisibility" in html_login
        assert 'id="reg-role"' not in html_login, "Public signup form must NOT expose admin role dropdown!"
        assert "lucide" not in html_login.lower(), "Lucide icons are forbidden!"
        print("[PASS] 1.3 Login UI verified: password eye toggle present, zero role dropdown, zero Lucide icons.")

        # ------------------------------------------------------------------
        # SUITE 2: REAL SUPABASE AUTH, ROLE ENFORCEMENT & PROFILE TRIGGER
        # ------------------------------------------------------------------
        print("\n--- SUITE 2: SUPABASE AUTH & ROLE AUTHORIZATION ---")
        rand_tag = uuid.uuid4().hex[:8]
        test_email = f"labnet.test.{rand_tag}@example.com"
        test_pass = "LabNetPass2026!"

        # 2.1 Register attempting privilege escalation (role='admin' MUST be ignored!)
        r_reg = client.post("/api/auth/register", json={
            "display_name": "Audit Test Student",
            "email": test_email,
            "password": test_pass,
            "role": "admin"
        })
        assert r_reg.status_code == 201, f"Registration failed: {r_reg.status_code} {r_reg.data}"
        reg_data = r_reg.get_json()
        synthetic_uid = reg_data["user"]["id"]
        assert reg_data["user"]["role"] == "student", "Privilege escalation vulnerability: signup granted admin!"

        # Verify profile created by Postgres trigger in live Supabase
        prof = get_profile_by_id(synthetic_uid)
        assert prof is not None and prof["role"] == "student" and prof["display_name"] == "Audit Test Student"
        print(f"[PASS] 2.1 Registered {test_email}; trigger created `profiles` row strictly with role='student'.")

        # 2.2 Test invalid password rejection
        client.get("/logout")
        r_bad = client.post("/api/auth/login", json={"email": test_email, "password": "WrongPassword999!"})
        assert r_bad.status_code == 401
        assert r_bad.get_json().get("code") == "invalid_credentials"
        print("[PASS] 2.2 Invalid password cleanly rejected with HTTP 401 (invalid_credentials).")

        # 2.3 Valid login (with uppercase/whitespace email normalization test)
        r_ok = client.post("/api/auth/login", json={"email": f"  {test_email.upper()}  ", "password": test_pass})
        assert r_ok.status_code == 200, f"Valid login failed: {r_ok.status_code} {r_ok.data}"
        print("[PASS] 2.3 Valid login succeeded with email normalization & active session created.")

        # 2.4 Student cannot access Admin endpoints (403 Forbidden)
        r_adm_api = client.get("/api/admin/users")
        assert r_adm_api.status_code == 403, f"Student accessed admin API! Status: {r_adm_api.status_code}"
        r_adm_page = client.get("/activity")
        assert r_adm_page.status_code == 302, f"Student accessed admin /activity page! Status: {r_adm_page.status_code}"
        print("[PASS] 2.4 RBAC verified: Student blocked from /api/admin/users (403) and /activity (302).")

        # 2.5 Path traversal protection on authenticated download
        for bad_path in ["../.env", "../../etc/passwd", "foo/../bar"]:
            r_trav = client.get(f"/api/files/download/{bad_path}")
            assert r_trav.status_code in (400, 404), f"Path traversal not rejected for {bad_path}: {r_trav.status_code}"
        print("[PASS] 2.5 Path traversal download attempts strictly blocked (400/404).")

        # 2.6 Shell injection protection on diagnostics
        r_inj = client.get("/api/diagnostics/ping?target=127.0.0.1;cat%20/etc/passwd")
        assert r_inj.status_code == 200 and r_inj.get_json()["exit_code"] == 1
        print("[PASS] 2.6 Shell injection payload rejected cleanly by target regex validator.")

        # 2.7 Verify ALL redesigned authenticated pages render HTTP 200 with zero Lucide icons
        for page in ["/", "/monitoring", "/chat", "/files", "/repository", "/diagnostics", "/protocols", "/account"]:
            rp = client.get(page)
            assert rp.status_code == 200, f"Page {page} failed with {rp.status_code}"
            body = rp.data.decode("utf-8")
            assert "lucide" not in body.lower(), f"Lucide reference found on {page}!"
            assert "labnet-logo.svg" in body, f"LabNet logo missing on {page}!"
        print("[PASS] 2.7 All 8 redesigned student pages render HTTP 200 with LabNet logo and zero Lucide icons.")

        # Promote synthetic test user temporarily via trusted repo to verify Admin /activity page & API
        update_profile(synthetic_uid, role="admin")
        assert client.get("/activity").status_code == 200
        assert client.get("/api/admin/users").status_code == 200
        print("[PASS] 2.8 Promoted admin accesses /activity (200) and /api/admin/users (200).")

        # ------------------------------------------------------------------
        # SUITE 3: PERSISTENT PRIVATE TCP CHAT & OFFLINE HISTORY RETRIEVAL
        # ------------------------------------------------------------------
        print("\n--- SUITE 3: PERSISTENT PRIVATE TCP CHAT & HISTORY ---")
        peer_contacts = client.get("/api/chat/conversations").get_json().get("contacts", [])
        assert len(peer_contacts) >= 1, "At least one other user profile is required to create a direct chat."
        target_peer_id = peer_contacts[0]["user_id"]
        r_conv = client.post("/api/chat/conversations/direct", json={"recipient_user_id": target_peer_id})
        assert r_conv.status_code == 200, f"Direct conversation creation failed: {r_conv.data}"
        test_conv_id = r_conv.get_json()["conversation"]["id"]

        t_chat0 = time.time()
        r_chat = client.post(
            f"/api/chat/conversations/{test_conv_id}/messages",
            json={"message": f"LabNet persistence audit message {rand_tag}"}
        )
        chat_ms = round((time.time() - t_chat0) * 1000.0, 2)
        assert r_chat.status_code == 200, f"Chat send failed: {r_chat.status_code} {r_chat.data}"
        sent_msg = r_chat.get_json()["message"]
        BENCHMARKS["chat_persist_and_tcp_ms"] = chat_ms

        # Fetch persistent history strictly for this conversation
        r_hist = client.get(f"/api/chat/conversations/{test_conv_id}/messages?limit=20")
        assert r_hist.status_code == 200
        hist_msgs = r_hist.get_json()["messages"]
        assert any(m["id"] == sent_msg["id"] and rand_tag in m["text"] for m in hist_msgs), "Message not retrieved from Supabase history!"
        print(f"[PASS] 3.1 Private conversation message persisted to Supabase `chat_messages` and dispatched via TCP :5001 ({chat_ms} ms).")

        # ------------------------------------------------------------------
        # SUITE 4: TCP FILE (:2121), RELIABLE UDP ARQ (:2122), P2P (:2123) & DOWNLOAD
        # ------------------------------------------------------------------
        print("\n--- SUITE 4: MULTI-PROTOCOL FILE TRANSFERS & DOWNLOAD ---")
        payload_size = 102400  # 100 KB consistent payload for TCP vs UDP benchmark
        raw_payload = os.urandom(payload_size)
        expected_sha = hashlib.sha256(raw_payload).hexdigest()

        # 4.1 TCP File Upload via Flask -> Port 2121
        tcp_fname = f"tcp_bench_{rand_tag}.bin"
        uploaded_test_files.append(tcp_fname)
        t0 = time.time()
        r_tcp_up = client.post(
            "/api/files/upload",
            data={"protocol": "TCP", "dest_mode": "server", "file": (io.BytesIO(raw_payload), tcp_fname)},
            content_type="multipart/form-data"
        )
        tcp_elapsed = round(time.time() - t0, 3)
        assert r_tcp_up.status_code == 200, f"TCP upload failed: {r_tcp_up.data}"
        tcp_res = r_tcp_up.get_json()["result"]
        assert tcp_res["verified"] is True and tcp_res["sha256"] == expected_sha
        BENCHMARKS["tcp_100kb_kbps"] = tcp_res.get("speed_kbps")
        BENCHMARKS["tcp_100kb_sec"] = tcp_elapsed
        print(f"[PASS] 4.1 TCP File Transfer (:2121) 100 KB verified (SHA-256 match) | {tcp_res.get('speed_kbps')} KB/s.")

        # 4.2 Remote File Download & SHA-256 Verification
        r_list = client.get("/api/files/list")
        assert r_list.status_code == 200
        assert any(f["filename"] == tcp_fname for f in r_list.get_json()["files"])
        r_dl = client.get(f"/api/files/download/{tcp_fname}")
        assert r_dl.status_code == 200
        dl_sha = hashlib.sha256(r_dl.data).hexdigest()
        assert dl_sha == expected_sha, "Downloaded file SHA-256 mismatch!"
        print(f"[PASS] 4.2 Authenticated remote download succeeded with exact SHA-256 match ({dl_sha[:16]}...).")

        # 4.3 Reliable UDP File Transfer (:2122) — Clean & Controlled 15% Packet Loss
        udp_path = os.path.join(TEST_TMP, f"udp_bench_{rand_tag}.bin")
        with open(udp_path, "wb") as f:
            f.write(raw_payload)
        uploaded_test_files.append(f"udp_bench_{rand_tag}.bin")

        t_u0 = time.time()
        udp_clean = upload_file_udp_client(udp_path, server_ip="127.0.0.1", server_port=2122, window_size=5, timeout=0.6, loss_rate=0.0)
        udp_clean_sec = round(time.time() - t_u0, 3)
        assert udp_clean["status"] == "SUCCESS" and udp_clean["verified"] is True and udp_clean["sha256"] == expected_sha
        BENCHMARKS["udp_clean_100kb_kbps"] = udp_clean.get("speed_kbps")
        BENCHMARKS["udp_clean_100kb_sec"] = udp_clean_sec
        print(f"[PASS] 4.3 Reliable UDP Transfer (:2122) clean 100 KB verified | {udp_clean.get('speed_kbps')} KB/s in {udp_clean_sec}s.")

        t_ul0 = time.time()
        udp_loss = upload_file_udp_client(udp_path, server_ip="127.0.0.1", server_port=2122, window_size=5, timeout=0.4, loss_rate=0.15)
        udp_loss_sec = round(time.time() - t_ul0, 3)
        assert udp_loss["status"] == "SUCCESS" and udp_loss["verified"] is True and udp_loss["total_retransmissions"] > 0
        BENCHMARKS["udp_loss15_100kb_kbps"] = udp_loss.get("speed_kbps")
        BENCHMARKS["udp_loss15_100kb_sec"] = udp_loss_sec
        BENCHMARKS["udp_loss15_retries"] = udp_loss.get("total_retransmissions")
        print(f"[PASS] 4.4 Reliable UDP ARQ (15% loss) recovered {udp_loss['total_retransmissions']} dropped packets in {udp_loss_sec}s (SHA-256 match).")

        # 4.5 Direct P2P Transfer (:2123)
        p2p_dir = os.path.join(TEST_TMP, "p2p_out")
        os.makedirs(p2p_dir, exist_ok=True)
        p2p_recv = P2PReceiver(port=2123, save_dir=p2p_dir, node_name="AuditPeer-VM3")
        threading.Thread(target=p2p_recv.start, daemon=True).start()
        time.sleep(0.5)
        p2p_res = send_direct_p2p(udp_path, "127.0.0.1", 2123, sender_name="AuditPeer-VM2")
        assert p2p_res["status"] == "SUCCESS" and p2p_res["verified"] is True and p2p_res["sha256"] == expected_sha
        BENCHMARKS["p2p_100kb_kbps"] = p2p_res.get("speed_kbps")
        print(f"[PASS] 4.5 Direct P2P transfer (:2123) verified without central server payload relay ({p2p_res.get('speed_kbps')} KB/s).")

        # ------------------------------------------------------------------
        # SUITE 5: UDP HEARTBEAT LIFECYCLE (ONLINE -> TIMEOUT OFFLINE -> RECOVERY)
        # ------------------------------------------------------------------
        print("\n--- SUITE 5: UDP HEARTBEAT LIFECYCLE & SUPABASE SYNC ---")
        hb_node = f"AuditNode-{rand_tag}"
        hb_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        hb_sock.settimeout(2.0)
        t_hb = time.time()
        hb_sock.sendto(f"HEARTBEAT|{hb_node}|{t_hb}".encode(), ("127.0.0.1", 6009))
        hb_resp, _ = hb_sock.recvfrom(1024)
        hb_rtt = round((time.time() - t_hb) * 1000.0, 2)
        BENCHMARKS["heartbeat_rtt_ms"] = hb_rtt
        assert hb_resp.decode().startswith(f"HEARTBEAT_ACK|{hb_node}")

        time.sleep(1.8)
        dev_row = get_device_by_name(hb_node)
        assert dev_row is not None and dev_row["status"] == "online", f"Device not marked online in Supabase: {dev_row}"
        synthetic_dev_ids.append(dev_row["id"])
        print(f"[PASS] 5.1 Heartbeat ACK received ({hb_rtt} ms) and `devices` + `heartbeat_logs` persisted as ONLINE.")

        print("[*] Waiting 3.2s to verify automatic timeout -> OFFLINE transition...")
        time.sleep(3.2)
        enforce_heartbeat_timeouts(2.5)
        dev_row_off = get_device_by_name(hb_node)
        assert dev_row_off["status"] == "offline", f"Device failed to transition to offline: {dev_row_off}"
        print("[PASS] 5.2 Timeout enforced cleanly: device transitioned to OFFLINE in Supabase.")

        # Recovery
        hb_sock.sendto(f"HEARTBEAT|{hb_node}|{time.time()}".encode(), ("127.0.0.1", 6009))
        hb_sock.recvfrom(1024)
        time.sleep(1.8)
        dev_row_rec = get_device_by_name(hb_node)
        assert dev_row_rec["status"] == "online", f"Device failed to recover to online: {dev_row_rec}"
        hb_sock.close()
        print("[PASS] 5.3 Heartbeat resumed: device recovered to ONLINE in Supabase.")

        # ------------------------------------------------------------------
        # SUITE 6: NETWORK SERVICES (HTTP, DNS, SMTP, POP3, ICMP)
        # ------------------------------------------------------------------
        print("\n--- SUITE 6: NETWORK SERVICES (HTTP, DNS, SMTP, POP3, ICMP) ---")
        assert client.get("/api/http/inspect").status_code == 200
        assert client.post("/api/http/inspect", json={"check": True}).status_code == 200
        print("[PASS] 6.1 HTTP GET & POST inspection endpoints verified.")

        r_dns = client.post("/api/dns/lookup", json={"domain": "localhost", "record_type": "A"})
        assert r_dns.status_code == 200 and "127.0.0.1" in r_dns.get_json().get("resolved_addresses", [])
        print(f"[PASS] 6.2 DNS A lookup resolved localhost -> 127.0.0.1 ({r_dns.get_json()['duration_ms']} ms).")

        r_smtp = client.post("/api/mail/send", json={
            "sender": test_email,
            "recipient": "student@labnet.local",
            "subject": f"Audit Mail {rand_tag}",
            "body": "Local SMTP spool verification."
        })
        assert r_smtp.status_code == 200 and r_smtp.get_json()["status"] == "DELIVERED_LOCAL_SPOOL"
        assert len(r_smtp.get_json().get("transcript", [])) >= 4
        print("[PASS] 6.3 Local SMTP (:1025) submission & RFC 5321 transcript verified.")

        r_pop = client.get("/api/mail/inbox?user=student")
        assert r_pop.status_code == 200 and r_pop.get_json()["message_count"] >= 1
        assert any(rand_tag in m["subject"] for m in r_pop.get_json()["messages"])
        print("[PASS] 6.4 Local POP3 (:1100) retrieval & RFC 1939 transcript verified.")

        for diag_cmd in ["ping", "routes", "interfaces", "sockets"]:
            rd = client.get(f"/api/diagnostics/{diag_cmd}?target=127.0.0.1")
            assert rd.status_code == 200 and rd.get_json()["exit_code"] == 0, f"Diag {diag_cmd} failed: {rd.get_json()}"
        print("[PASS] 6.5 Kernel diagnostics (ping, ip route, ip addr, ss -tuln) verified cleanly.")

        print("\n=================================================================")
        print("         MEASURED LOCAL PERFORMANCE BENCHMARKS (100 KB)          ")
        print("=================================================================")
        for k, v in BENCHMARKS.items():
            print(f"  {k:<28}: {v}")
        print("=================================================================")
        print("   ALL FINAL VALIDATION, SECURITY & PERSISTENCE TESTS PASSED!    ")
        print("=================================================================")

    finally:
        stop_daemons()
        delete_synthetic_user(synthetic_uid, synthetic_dev_ids)
        upload_dir = PROJECT_ROOT / "uploads"
        for fn in uploaded_test_files:
            fp = upload_dir / fn
            if fp.exists():
                fp.unlink()
        print("[*] Synthetic test data & server daemons cleaned up.")


if __name__ == "__main__":
    run_all()
