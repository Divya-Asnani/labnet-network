import os
import uuid
import datetime
import hashlib
import tempfile
from pathlib import Path
from flask import (
    Flask, render_template, request, jsonify,
    session, redirect, url_for, send_from_directory
)
from flask_socketio import SocketIO, emit, join_room, leave_room, rooms
from dotenv import dotenv_values

# Services
from web.services.tcp_service import send_tcp_message, upload_file_tcp, chat_bridge
from web.services.udp_file_service import upload_file_udp
from web.services.monitoring_service import (
    get_network_status, get_events, run_diagnostic, start_monitoring,
    get_stats, get_dashboard_overview, register_peer_node,
    get_registered_peers, add_event, register_ws_device, unregister_ws_device
)
from web.services.auth_service import (
    login_required, admin_required, login_user, register_user,
    logout_user, send_password_reset, refresh_session_user
)
from server.dns_service import resolve_domain
from server.smtp_service import send_mail_smtp
from server.pop3_service import fetch_pop3_inbox
from server.mail_test_server import start_mail_servers
from clients.p2p_node import send_direct_p2p

# Database Repositories
from web.database.repositories.profile_repository import list_profiles, update_profile
from web.database.repositories.device_repository import register_device, get_device_by_ip, update_device_status
from web.database.repositories.conversation_repository import (
    is_user_participant,
    get_or_create_direct_conversation,
    get_conversation_detail,
    get_conversation_participants,
    list_user_conversations,
    list_available_contacts
)
from web.database.repositories.chat_repository import (
    record_chat_message, update_chat_message_status,
    list_conversation_messages, count_messages
)
from web.database.repositories.file_repository import (
    record_file_transfer, update_transfer_status, get_transfer_by_id,
    get_transfer_by_filename, list_transfers
)
from web.database.repositories.heartbeat_repository import list_heartbeats
from web.database.repositories.session_repository import record_session, update_session, list_sessions
from web.database.repositories.event_repository import record_event
from web.database.supabase_client import get_config_val

# Environment setup
root_dir = Path(__file__).resolve().parent.parent
env_path = root_dir / ".env"
config = dotenv_values(env_path) if env_path.exists() else {}

UPLOAD_FOLDER = os.path.join(str(root_dir), config.get("UPLOAD_FOLDER", "uploads"))
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)
app.config["SECRET_KEY"] = config.get("SECRET_KEY", "labnet-cnt-project-key-2026")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["MAX_CONTENT_LENGTH"] = int(config.get("MAX_UPLOAD_SIZE_MB", 100)) * 1024 * 1024
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Start passive port monitoring & timeout reaper + local mail test servers
start_monitoring()
try:
    start_mail_servers()
except Exception as e:
    print(f"[*] Note on local mail test servers: {e}")


# Bridge TCP Chat frames strictly to authorized conversation Socket.IO rooms
# SECURITY: Private chat messages are NEVER emitted globally to all clients.
def on_tcp_chat_broadcast(message_obj):
    with app.app_context():
        msg_type = message_obj.get("type", "chat")
        if msg_type in ("presence", "system"):
            socketio.emit("presence_update", {
                "event": message_obj.get("event", "info"),
                "active_clients": message_obj.get("active_clients"),
                "timestamp": message_obj.get("timestamp"),
                "text": message_obj.get("text", "")
            })
            return

        conv_id = message_obj.get("conversation_id")
        sender_uid = message_obj.get("sender_user_id")
        if not conv_id:
            # Drop any chat frame missing a valid conversation_id (no global broadcast)
            return

        # Re-verify sender membership before emitting to room
        if sender_uid and not is_user_participant(conv_id, sender_uid):
            return

        room_name = f"conversation:{conv_id}"
        socketio.emit("chat_message", message_obj, to=room_name)

        # Notify ONLY authorized participants' personal sidebar rooms for preview/unread updates
        participant_ids = message_obj.get("participant_ids") or []
        for pid in participant_ids:
            socketio.emit("conversation_updated", {
                "conversation_id": conv_id,
                "sender_user_id": sender_uid,
                "sender": message_obj.get("sender", ""),
                "last_message": message_obj.get("text", ""),
                "timestamp": message_obj.get("timestamp", "")
            }, to=f"user:{pid}")


chat_bridge.add_message_callback(on_tcp_chat_broadcast)
chat_bridge.start()


def _format_chat_row(row: dict) -> dict:
    prof = row.get("profiles") or {}
    dev = row.get("devices") or {}
    sent_at = row.get("sent_at") or ""
    readable = sent_at.replace("T", " ")[:19] if sent_at else ""
    return {
        "id": row.get("id"),
        "conversation_id": row.get("conversation_id"),
        "type": "chat",
        "sender_user_id": row.get("sender_user_id"),
        "sender": prof.get("display_name") or "LabNet User",
        "sender_role": prof.get("role") or "student",
        "device_name": dev.get("device_name") or "",
        "device_ip": dev.get("ip_address") or "",
        "text": row.get("message") or "",
        "protocol": row.get("protocol") or "TCP",
        "status": row.get("status") or "delivered",
        "sent_at": sent_at,
        "timestamp": readable
    }


@app.context_processor
def inject_globals():
    return {
        "current_user": session.get("user"),
        "server_ip": config.get("SERVER_HOST", "192.168.66.128"),
        "flask_port": int(get_config_val("FLASK_PORT", 5050))
    }


# ============================================================================
# Authentication Routes (Supabase Auth)
# ============================================================================

def _detect_os_label(user_agent: str) -> str:
    ua = (user_agent or "").lower()
    if "ubuntu" in ua:
        return "Ubuntu Linux"
    if "linux" in ua:
        return "Linux"
    if "windows" in ua:
        return "Windows"
    if "mac os" in ua or "macintosh" in ua:
        return "macOS"
    return "Ubuntu Linux"


def _normalize_client_ip(remote_addr: str) -> str:
    ip = (remote_addr or "").strip()
    if ip in ("", "127.0.0.1", "::1", "localhost"):
        return config.get("SERVER_HOST", "192.168.66.128")
    return ip


def _register_workstation_for_session(user: dict, remote_addr: str, user_agent: str):
    """
    Registers or refreshes the authenticated user's workstation in Supabase `devices`,
    closes any older active HTTP session for that same device, and opens a fresh session.
    """
    if not user or not user.get("id"):
        return None

    client_ip = _normalize_client_ip(remote_addr)
    server_ip = config.get("SERVER_HOST", "192.168.66.128")
    os_label = _detect_os_label(user_agent)
    role_tag = "Server" if client_ip == server_ip else "Workstation"
    device_name = f"{user.get('display_name', 'LabNet User')} ({client_ip})"
    hostname = f"labnet-{role_tag.lower()}-{client_ip.split('.')[-1]}"

    ok_dev, dev = register_device(
        user_id=user["id"],
        device_name=device_name,
        hostname=hostname,
        ip_address=client_ip,
        os_name=os_label,
        status="online"
    )
    dev_id = dev["id"] if (ok_dev and isinstance(dev, dict) and "id" in dev) else None
    if dev_id:
        session["device_id"] = dev_id
        # Close older duplicate active HTTP sessions for this device so active count stays accurate
        try:
            for s in list_sessions(limit=50):
                if s.get("status") == "active" and s.get("protocol") == "HTTP" and s.get("source_device_id") == dev_id:
                    update_session(s["id"], status="completed")
        except Exception:
            pass

    ok_sess, sess_row = record_session(
        protocol="HTTP",
        status="active",
        source_device_id=dev_id,
        destination_port=int(get_config_val("FLASK_PORT", 5050))
    )
    if ok_sess and isinstance(sess_row, dict) and "id" in sess_row:
        session["web_session_id"] = sess_row["id"]

    return dev_id


@app.route("/login")
def login_page():
    if session.get("user"):
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/logout")
def logout_page():
    user = session.get("user")
    web_session_id = session.get("web_session_id")
    dev_id = session.get("device_id")
    if web_session_id:
        update_session(web_session_id, status="completed")
    if dev_id:
        update_device_status(dev_id, "offline", update_last_seen=False)
    logout_user(user)
    session.clear()
    return redirect(url_for("login_page"))


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    data = request.json or {}
    email = data.get("email", "")
    password = data.get("password", "")

    success, result, status_code = login_user(email, password)
    if success:
        session["user"] = result
        _register_workstation_for_session(result, request.remote_addr, request.headers.get("User-Agent", ""))
        return jsonify({"status": "success", "user": {
            "id": result["id"],
            "email": result["email"],
            "display_name": result["display_name"],
            "role": result["role"]
        }}), 200
    else:
        return jsonify(result), status_code


@app.route("/api/auth/register", methods=["POST"])
def api_auth_register():
    data = request.json or {}
    email = data.get("email", "")
    password = data.get("password", "")
    display_name = data.get("display_name", "")

    # SECURITY: Never accept or trust client-supplied `role`. All public signups are `student`.
    success, result, status_code = register_user(email, password, display_name=display_name)
    if success:
        if not result.get("requires_confirmation") and result.get("user", {}).get("access_token"):
            session["user"] = result["user"]
            _register_workstation_for_session(result["user"], request.remote_addr, request.headers.get("User-Agent", ""))
        return jsonify({
            "status": "success",
            "requires_confirmation": result.get("requires_confirmation", False),
            "message": result.get("message"),
            "user": {
                "id": result["user"]["id"],
                "email": result["user"]["email"],
                "display_name": result["user"]["display_name"],
                "role": result["user"]["role"]
            }
        }), status_code
    else:
        return jsonify(result), status_code


@app.route("/api/auth/reset-password", methods=["POST"])
def api_auth_reset_password():
    data = request.json or {}
    email = data.get("email", "")
    ok, msg, status_code = send_password_reset(email)
    return jsonify({"status": "success" if ok else "error", "message": msg}), status_code


@app.route("/api/auth/user", methods=["GET"])
def api_auth_user():
    user = refresh_session_user()
    if user:
        return jsonify({
            "authenticated": True,
            "user": {
                "id": user["id"],
                "email": user["email"],
                "display_name": user["display_name"],
                "role": user["role"]
            }
        })
    return jsonify({"authenticated": False})


@app.route("/api/auth/profile", methods=["POST"])
@login_required
def api_auth_update_profile():
    user = session["user"]
    data = request.json or {}
    new_name = (data.get("display_name") or "").strip()
    if not new_name or len(new_name) < 2:
        return jsonify({"error": "Display name must be at least 2 characters."}), 400

    # SECURITY: Only allow updating display_name here, never role
    ok, res = update_profile(user["id"], display_name=new_name)
    if ok:
        user["display_name"] = new_name
        session["user"] = user
        return jsonify({"status": "updated", "display_name": new_name})
    return jsonify({"error": str(res)}), 500


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    user = session.get("user")
    web_session_id = session.get("web_session_id")
    dev_id = session.get("device_id")
    if web_session_id:
        update_session(web_session_id, status="completed")
    if dev_id:
        update_device_status(dev_id, "offline", update_last_seen=False)
    logout_user(user)
    session.clear()
    return jsonify({"status": "logged_out"})


# ============================================================================
# Protected Application Page Routes (Shared Shell)
# ============================================================================

@app.route("/")
@login_required
def index():
    user = refresh_session_user()
    if user and not session.get("device_id"):
        _register_workstation_for_session(user, request.remote_addr, request.headers.get("User-Agent", ""))
    return render_template("dashboard.html")


@app.route("/monitoring")
@login_required
def monitoring():
    refresh_session_user()
    return render_template("monitoring.html")


@app.route("/network")
@login_required
def network_redirect():
    return redirect(url_for("monitoring"))


@app.route("/chat")
@login_required
def chat():
    refresh_session_user()
    return render_template("chat.html")


@app.route("/files")
@login_required
def files():
    refresh_session_user()
    return render_template("files.html")


@app.route("/repository")
@login_required
def repository():
    refresh_session_user()
    return render_template("repository.html")


@app.route("/diagnostics")
@login_required
def diagnostics():
    refresh_session_user()
    return render_template("diagnostics.html")


@app.route("/protocols")
@login_required
def protocols():
    refresh_session_user()
    return render_template("protocols.html")


@app.route("/activity")
@admin_required
def activity():
    refresh_session_user()
    return render_template("activity.html")


@app.route("/account")
@login_required
def account():
    refresh_session_user()
    return render_template("account.html")


# ============================================================================
# Dashboard, Monitoring & Admin APIs
# ============================================================================

def _touch_request_workstation():
    """Ensures an authenticated browser's workstation is registered and freshly stamped online."""
    user = session.get("user")
    if not user or not user.get("id"):
        return
    dev_id = session.get("device_id")
    if not dev_id:
        _register_workstation_for_session(
            user, request.remote_addr, request.headers.get("User-Agent", "")
        )
    else:
        update_device_status(dev_id, "online", update_last_seen=True)


@app.route("/api/dashboard/overview", methods=["GET"])
@login_required
def api_dashboard_overview():
    _touch_request_workstation()
    overview = get_dashboard_overview()
    overview["tcp_chat_bridge"] = chat_bridge.connected
    return jsonify(overview)


@app.route("/api/network/status", methods=["GET"])
@login_required
def api_network_status():
    _touch_request_workstation()
    return jsonify({
        "status": get_network_status(),
        "events": get_events(limit=30),
        "stats": get_stats(),
        "tcp_chat_bridge": chat_bridge.connected
    })


@app.route("/api/monitoring/devices", methods=["GET"])
@login_required
def api_monitoring_devices():
    _touch_request_workstation()
    return jsonify({
        "devices": get_network_status(),
        "heartbeat_timeout": float(get_config_val("HEARTBEAT_TIMEOUT", 10.0))
    })


@app.route("/api/monitoring/heartbeats", methods=["GET"])
@login_required
def api_monitoring_heartbeats():
    device_id = request.args.get("device_id")
    limit = min(int(request.args.get("limit", 50)), 200)
    return jsonify({"heartbeats": list_heartbeats(device_id=device_id, limit=limit)})


@app.route("/api/monitoring/sessions", methods=["GET"])
@login_required
def api_monitoring_sessions():
    limit = min(int(request.args.get("limit", 50)), 200)
    return jsonify({"sessions": list_sessions(limit=limit)})


@app.route("/api/monitoring/events", methods=["GET"])
@login_required
def api_monitoring_events():
    event_type = request.args.get("event_type") or None
    protocol = request.args.get("protocol") or None
    limit = min(int(request.args.get("limit", 60)), 200)
    return jsonify({"events": get_events(limit=limit, event_type=event_type, protocol=protocol)})


@app.route("/api/admin/users", methods=["GET"])
@admin_required
def api_admin_users():
    return jsonify({"profiles": list_profiles()})


# ============================================================================
# Private Conversation & Persistent TCP Chat APIs (Strict Isolation)
# ============================================================================

def _send_conversation_message_core(user: dict, conversation_id: str, message_text: str, client_ip: str):
    """
    Shared core for REST and Socket.IO message transmission:
    1. Strictly verifies participant membership in `conversation_id`.
    2. Persists message in Supabase `chat_messages` with `conversation_id`.
    3. Dispatches canonical frame (including `conversation_id` and `participant_ids`)
       over raw TCP socket (:5001).
    4. Updates delivery state (`delivered` vs `failed`).
    """
    if not conversation_id:
        return False, {"code": "missing_conversation", "error": "conversation_id is required."}, 400
    if not message_text:
        return False, {"code": "empty_message", "error": "Message cannot be empty."}, 400
    if len(message_text) > 4000:
        return False, {"code": "too_long", "error": "Message exceeds maximum length (4000 characters)."}, 400

    # Mandatory participant authorization check (never trust frontend conversation_id)
    ok_parts, participants, status_p = get_conversation_participants(conversation_id, user["id"])
    if not ok_parts:
        return False, {"code": "forbidden", "error": participants}, status_p

    participant_ids = [p["user_id"] for p in participants]
    dev = get_device_by_ip(client_ip)
    dev_id = dev["id"] if dev else None

    msg_id = str(uuid.uuid4())
    ok_db, db_row, status_db = record_chat_message(
        conversation_id=conversation_id,
        sender_user_id=user["id"],
        message=message_text,
        protocol="TCP",
        status="sent",
        sender_device_id=dev_id,
        message_id=msg_id
    )
    if not ok_db:
        return False, {"code": "db_error", "error": f"Database persistence failed: {db_row}"}, status_db

    sent_at = db_row.get("sent_at") if isinstance(db_row, dict) else datetime.datetime.now(datetime.timezone.utc).isoformat()
    timestamp = sent_at.replace("T", " ")[:19]

    frame = {
        "id": msg_id,
        "conversation_id": str(conversation_id),
        "participant_ids": participant_ids,
        "type": "chat",
        "sender_user_id": user["id"],
        "sender": user["display_name"],
        "sender_role": user["role"],
        "device_ip": client_ip,
        "text": message_text,
        "protocol": "TCP",
        "status": "delivered",
        "sent_at": sent_at,
        "timestamp": timestamp
    }

    success, resp = chat_bridge.send_chat_message(frame)
    if success:
        update_chat_message_status(msg_id, "delivered")
        return True, {"status": "delivered", "message": frame, "response": resp}, 200
    else:
        frame["status"] = "failed"
        update_chat_message_status(msg_id, "failed")
        return False, {
            "status": "failed",
            "code": "tcp_unreachable",
            "message": frame,
            "error": f"Message saved to conversation history, but live TCP (:5001) failed: {resp}"
        }, 503


@app.route("/api/chat/conversations", methods=["GET"])
@login_required
def api_chat_conversations():
    """
    Returns ONLY private conversations where the authenticated user is a participant,
    plus available registered lab contacts to initiate new direct conversations.
    """
    user = session["user"]
    devices = get_network_status()
    online_user_names = {d.get("user_name") for d in devices if d.get("status") == "ONLINE" and d.get("user_name")}

    conversations = list_user_conversations(user["id"], online_user_names=online_user_names)
    contacts = list_available_contacts(user["id"], online_user_names=online_user_names)

    return jsonify({
        "conversations": conversations,
        "contacts": contacts,
        "tcp_bridge_connected": chat_bridge.connected
    })


@app.route("/api/chat/conversations/direct", methods=["POST"])
@login_required
def api_chat_create_direct():
    """Atomically creates or retrieves the unique direct conversation between current user and recipient."""
    user = session["user"]
    data = request.json or {}
    recipient_user_id = (data.get("recipient_user_id") or "").strip()
    if not recipient_user_id:
        return jsonify({"error": "recipient_user_id is required."}), 400

    ok, result, status_code = get_or_create_direct_conversation(user["id"], recipient_user_id)
    if ok:
        return jsonify({"status": "ok", "conversation": result}), 200
    return jsonify({"error": result}), status_code


@app.route("/api/chat/conversations/<conversation_id>", methods=["GET"])
@login_required
def api_chat_conversation_detail(conversation_id):
    """Returns conversation metadata and participants ONLY if authenticated user is a member."""
    user = session["user"]
    ok, result, status_code = get_conversation_detail(conversation_id, user["id"])
    if ok:
        return jsonify({"conversation": result}), 200
    return jsonify({"error": result}), status_code


@app.route("/api/chat/conversations/<conversation_id>/messages", methods=["GET"])
@login_required
def api_chat_conversation_messages(conversation_id):
    """
    Retrieves paginated chronological messages strictly for ONE authorized conversation.
    Returns 403 Forbidden if authenticated user is not a participant.
    """
    user = session["user"]
    limit = min(max(int(request.args.get("limit", 50)), 1), 200)
    before_iso = request.args.get("before") or None

    ok, rows_or_err, status_code = list_conversation_messages(
        conversation_id=conversation_id,
        requesting_user_id=user["id"],
        limit=limit,
        before_iso=before_iso
    )
    if not ok:
        return jsonify({"error": rows_or_err}), status_code

    formatted = [_format_chat_row(r) for r in rows_or_err]
    has_more = len(rows_or_err) == limit
    oldest_cursor = formatted[0]["sent_at"] if formatted else None

    return jsonify({
        "conversation_id": conversation_id,
        "messages": formatted,
        "has_more": has_more,
        "oldest_cursor": oldest_cursor,
        "tcp_bridge_connected": chat_bridge.connected
    }), 200


@app.route("/api/chat/history", methods=["GET"])
@login_required
def api_chat_history():
    """
    Backwards-compatible history endpoint requiring explicit `conversation_id`
    and enforcing strict participant authorization. Never returns global messages.
    """
    conversation_id = (request.args.get("conversation_id") or "").strip()
    if not conversation_id:
        return jsonify({"error": "conversation_id query parameter is required."}), 400
    return api_chat_conversation_messages(conversation_id)


@app.route("/api/chat/conversations/<conversation_id>/messages", methods=["POST"])
@login_required
def api_chat_conversation_send(conversation_id):
    user = session["user"]
    data = request.json or {}
    message_text = (data.get("message") or data.get("text") or "").strip()
    client_ip = request.remote_addr or "127.0.0.1"

    ok, payload, status_code = _send_conversation_message_core(user, conversation_id, message_text, client_ip)
    return jsonify(payload), status_code


@app.route("/api/chat/send", methods=["POST"])
@login_required
def api_chat_send():
    user = session["user"]
    data = request.json or {}
    conversation_id = (data.get("conversation_id") or "").strip()
    message_text = (data.get("message") or data.get("text") or "").strip()
    client_ip = request.remote_addr or "127.0.0.1"

    ok, payload, status_code = _send_conversation_message_core(user, conversation_id, message_text, client_ip)
    return jsonify(payload), status_code


# ============================================================================
# File Transfer (TCP :2121, Reliable UDP :2122, Direct P2P :2123) & Repository
# ============================================================================

@app.route("/api/files/upload", methods=["POST"])
@login_required
def api_files_upload():
    user = session["user"]
    if "file" not in request.files:
        return jsonify({"error": "No file attached in request"}), 400

    uploaded_file = request.files["file"]
    if not uploaded_file or uploaded_file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    safe_name = os.path.basename(uploaded_file.filename)
    protocol = (request.form.get("protocol") or "TCP").upper()
    dest_mode = (request.form.get("dest_mode") or "server").lower()
    sender_ip = request.remote_addr or "127.0.0.1"
    sender_name = user["display_name"]

    uploaded_file.seek(0, os.SEEK_END)
    filesize = uploaded_file.tell()
    uploaded_file.seek(0)

    max_bytes = int(get_config_val("MAX_UPLOAD_SIZE_MB", 100)) * 1024 * 1024
    if filesize > max_bytes:
        return jsonify({"error": f"File exceeds maximum allowed size ({max_bytes // (1024*1024)} MB)."}), 413

    sender_dev = get_device_by_ip(sender_ip)
    sender_dev_id = sender_dev["id"] if sender_dev else None

    def progress_tracker(bytes_sent, total_bytes, percent, speed_kbps, retries=0):
        socketio.emit("file_progress", {
            "filename": safe_name,
            "protocol": protocol,
            "bytes_sent": bytes_sent,
            "total_bytes": total_bytes,
            "percent": percent,
            "speed_kbps": speed_kbps,
            "retries": retries,
            "status": "STREAMING"
        })

    # Mode 1: Direct Peer-to-Peer Transfer (Node -> Peer IP:Port)
    if dest_mode == "p2p":
        target_ip = (request.form.get("target_ip") or "").strip()
        target_port = int(request.form.get("target_port") or 2123)
        if not target_ip:
            return jsonify({"error": "Destination peer IP address is required for P2P transfer."}), 400

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix="_" + safe_name) as tmp:
                uploaded_file.save(tmp)
                tmp_path = tmp.name

            # Stream directly via BSD TCP socket to destination peer
            p2p_result = send_direct_p2p(
                filepath=tmp_path,
                target_ip=target_ip,
                target_port=target_port,
                sender_name=sender_name
            )
            p2p_result["filename"] = safe_name
            p2p_result["transfer_mode"] = "DIRECT_P2P"
            p2p_result["target"] = f"{target_ip}:{target_port}"

            recv_dev = get_device_by_ip(target_ip)
            record_file_transfer(
                sender_user_id=user["id"],
                sender_device_id=sender_dev_id,
                receiver_device_id=recv_dev["id"] if recv_dev else None,
                file_name=safe_name,
                file_size=filesize,
                protocol="TCP",
                checksum=p2p_result.get("sha256"),
                storage_path=f"p2p://{target_ip}:{target_port}/{safe_name}",
                status="completed" if p2p_result.get("verified") else "failed"
            )
            record_event(
                event_type="transfer",
                protocol="TCP",
                source_ip=sender_ip,
                destination_ip=target_ip,
                device_id=sender_dev_id,
                details={
                    "mode": "DIRECT_P2P",
                    "filename": safe_name,
                    "filesize": filesize,
                    "target_port": target_port,
                    "verified": p2p_result.get("verified"),
                    "speed_kbps": p2p_result.get("speed_kbps")
                }
            )
            return jsonify({"status": "completed", "result": p2p_result}), 200
        except Exception as e:
            record_file_transfer(
                sender_user_id=user["id"],
                sender_device_id=sender_dev_id,
                file_name=safe_name,
                file_size=filesize,
                protocol="TCP",
                status="failed"
            )
            return jsonify({"status": "error", "error": f"Direct P2P transfer to {target_ip}:{target_port} failed: {e}"}), 502
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)

    # Mode 2: Central Server Transfer (TCP :2121 or Reliable UDP :2122)
    if protocol == "TCP":
        success, result = upload_file_tcp(
            file_stream=uploaded_file.stream,
            filename=safe_name,
            filesize=filesize,
            sender=sender_name,
            sender_ip=sender_ip,
            progress_callback=progress_tracker
        )
    elif protocol == "UDP":
        success, result = upload_file_udp(
            file_stream=uploaded_file.stream,
            filename=safe_name,
            filesize=filesize,
            sender=sender_name,
            sender_ip=sender_ip,
            progress_callback=progress_tracker
        )
    else:
        return jsonify({"error": f"Unsupported protocol '{protocol}'. Choose TCP or UDP."}), 400

    if success and isinstance(result, dict):
        is_verified = bool(result.get("verified", False))
        socketio.emit("file_progress", {
            "filename": safe_name,
            "protocol": protocol,
            "percent": 100.0,
            "status": "COMPLETED" if is_verified else "FAILED",
            "sha256": result.get("sha256"),
            "verified": is_verified,
            "save_path": result.get("save_path")
        })

        ok_db, db_row = record_file_transfer(
            sender_user_id=user["id"],
            sender_device_id=sender_dev_id,
            file_name=safe_name,
            file_size=filesize,
            protocol=protocol,
            checksum=result.get("sha256"),
            storage_path=result.get("save_path"),
            status="completed" if is_verified else "failed"
        )
        if not ok_db:
            result["db_warning"] = f"File transferred, but database persistence failed: {db_row}"
        elif isinstance(db_row, dict):
            result["transfer_id"] = db_row.get("id")

        record_session(
            protocol=protocol,
            status="completed",
            source_device_id=sender_dev_id,
            destination_port=2121 if protocol == "TCP" else 2122,
            bytes_sent=filesize,
            bytes_received=filesize
        )

        return jsonify({"status": "completed", "result": result}), 200
    else:
        record_file_transfer(
            sender_user_id=user["id"],
            sender_device_id=sender_dev_id,
            file_name=safe_name,
            file_size=filesize,
            protocol=protocol,
            status="failed"
        )
        return jsonify({"status": "error", "error": str(result)}), 500


@app.route("/api/files/list", methods=["GET"])
@login_required
def api_files_list():
    """
    Returns stored files in server's `uploads/` directory enriched with
    Supabase `file_transfers` audit metadata (sender, protocol, status, transfer ID).
    """
    db_transfers = list_transfers(limit=100)
    db_by_name = {}
    for tr in db_transfers:
        fn = tr.get("file_name")
        if fn and fn not in db_by_name:
            db_by_name[fn] = tr

    files = []
    if os.path.exists(UPLOAD_FOLDER):
        for fname in sorted(os.listdir(UPLOAD_FOLDER)):
            fpath = os.path.join(UPLOAD_FOLDER, fname)
            if os.path.isfile(fpath) and fname != ".gitkeep":
                size = os.path.getsize(fpath)
                mtime = os.path.getmtime(fpath)
                tr = db_by_name.get(fname, {})
                prof = tr.get("profiles") or {}

                sha = tr.get("checksum")
                if not sha:
                    hasher = hashlib.sha256()
                    with open(fpath, "rb") as f:
                        while chunk := f.read(65536):
                            hasher.update(chunk)
                    sha = hasher.hexdigest()

                files.append({
                    "transfer_id": tr.get("id"),
                    "filename": fname,
                    "size": size,
                    "sha256": sha,
                    "protocol": tr.get("protocol") or "TCP",
                    "status": tr.get("status") or "completed",
                    "sender": prof.get("display_name") or "LabNet User",
                    "uploaded_at": (tr.get("started_at") or "").replace("T", " ")[:19]
                        or datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
                })

    return jsonify({"files": files, "transfers": db_transfers})


@app.route("/api/files/download/<path:identifier>", methods=["GET"])
@login_required
def api_files_download(identifier):
    """
    Secure authenticated file download endpoint.
    Accepts either a Supabase transfer UUID or a sanitized filename.
    Strictly prevents path traversal attacks outside UPLOAD_FOLDER.
    """
    # Reject explicit traversal patterns immediately
    if ".." in identifier or "/" in identifier or "\\" in identifier:
        return jsonify({"error": "Invalid file identifier. Path traversal is forbidden."}), 400

    # Check if identifier is a transfer UUID
    target_filename = identifier
    tr = None
    try:
        uuid.UUID(identifier)
        tr = get_transfer_by_id(identifier)
        if tr and tr.get("file_name"):
            target_filename = tr["file_name"]
    except ValueError:
        pass

    safe_filename = os.path.basename(target_filename)
    file_path = os.path.join(UPLOAD_FOLDER, safe_filename)

    abs_upload = os.path.abspath(UPLOAD_FOLDER)
    abs_file = os.path.abspath(file_path)
    if not abs_file.startswith(abs_upload + os.sep) or not os.path.isfile(abs_file):
        return jsonify({"error": "Requested file does not exist on server."}), 404

    user = session["user"]
    record_event(
        event_type="transfer",
        protocol="HTTP",
        source_ip=request.remote_addr,
        details={
            "action": "file_download",
            "filename": safe_filename,
            "size_bytes": os.path.getsize(abs_file),
            "downloaded_by": user["display_name"],
            "user_id": user["id"]
        }
    )

    return send_from_directory(UPLOAD_FOLDER, safe_filename, as_attachment=True)


# ============================================================================
# Peer-to-Peer (P2P) Coordination Endpoints
# ============================================================================

@app.route("/api/peers/register", methods=["POST"])
def api_peers_register():
    data = request.json or {}
    peer_name = (data.get("peer_name") or f"node-{request.remote_addr}").strip()
    ip = (data.get("ip") or request.remote_addr or "").strip()
    try:
        port = int(data.get("port", 2123))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid peer port"}), 400
    status = data.get("status", "READY")

    peer_info = register_peer_node(peer_name, ip, port, status)
    return jsonify({"status": "registered", "peer": peer_info}), 201


@app.route("/api/peers/list", methods=["GET"])
@login_required
def api_peers_list():
    return jsonify({"peers": get_registered_peers()})


# ============================================================================
# Network Services (HTTP, DNS, SMTP, POP3, ICMP Diagnostics)
# ============================================================================

@app.route("/api/http/inspect", methods=["GET", "POST"])
@login_required
def api_http_inspect():
    """HTTP GET/POST protocol demonstration returning live request/response metadata."""
    body_payload = request.get_json(silent=True) if request.method == "POST" else None
    record_event(
        event_type="diagnostic",
        protocol="HTTP",
        source_ip=request.remote_addr,
        details={"method": request.method, "path": request.path}
    )
    return jsonify({
        "status": "OK",
        "http_status_code": 200,
        "method": request.method,
        "path": request.path,
        "client_ip": request.remote_addr,
        "user_agent": request.headers.get("User-Agent", ""),
        "content_type": request.headers.get("Content-Type", ""),
        "received_payload": body_payload,
        "server_timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }), 200


@app.route("/api/dns/lookup", methods=["POST"])
@login_required
def api_dns_lookup():
    data = request.json or {}
    domain = (data.get("domain") or "localhost").strip()
    record_type = (data.get("record_type") or "A").strip().upper()

    result = resolve_domain(domain, record_type)
    record_event(
        event_type="diagnostic",
        protocol="DNS",
        source_ip=request.remote_addr,
        details={
            "domain": domain,
            "record_type": record_type,
            "nameserver": result.get("nameserver"),
            "resolved": result.get("resolved_addresses"),
            "duration_ms": result.get("duration_ms")
        }
    )
    return jsonify(result)


@app.route("/api/mail/send", methods=["POST"])
@login_required
def api_mail_send():
    user = session["user"]
    data = request.json or {}
    sender = (data.get("sender") or user.get("email") or "researcher@labnet.local").strip()
    recipient = (data.get("recipient") or "student@labnet.local").strip()
    subject = (data.get("subject") or "LabNet Notification").strip()
    body = (data.get("body") or "").strip()

    if not recipient or not body:
        return jsonify({"error": "Recipient and message body are required."}), 400

    success, result = send_mail_smtp(sender, recipient, subject, body)
    record_event(
        event_type="diagnostic",
        protocol="SMTP",
        source_ip=request.remote_addr,
        details={
            "sender": sender,
            "recipient": recipient,
            "subject": subject,
            "status": result.get("status"),
            "duration_ms": result.get("duration_ms")
        }
    )
    return jsonify(result), (200 if success else 500)


@app.route("/api/mail/inbox", methods=["GET"])
@login_required
def api_mail_inbox():
    user_box = (request.args.get("user") or "student").strip()
    idx_param = request.args.get("index")
    idx = int(idx_param) if (idx_param and idx_param.isdigit()) else None

    success, result = fetch_pop3_inbox(user=user_box, retrieve_index=idx)
    record_event(
        event_type="diagnostic",
        protocol="POP3",
        source_ip=request.remote_addr,
        details={
            "mailbox": user_box,
            "message_count": result.get("message_count", 0),
            "status": result.get("status")
        }
    )
    return jsonify(result), (200 if success else 500)


@app.route("/api/diagnostics/<command>", methods=["GET"])
@login_required
def api_diagnostics(command):
    target = request.args.get("target") or None
    result = run_diagnostic(command, target=target)
    return jsonify(result)


# ============================================================================
# WebSocket (Socket.IO) Events — Strictly Isolated Conversation Rooms
# ============================================================================

@socketio.on("connect")
def handle_connect():
    user = session.get("user")
    if user and user.get("id"):
        # Join ONLY this authenticated user's private notification room
        join_room(f"user:{user['id']}")
        dev_id = session.get("device_id")
        if not dev_id:
            dev_id = _register_workstation_for_session(
                user, request.remote_addr, request.headers.get("User-Agent", "")
            )
        if dev_id:
            register_ws_device(request.sid, dev_id)
            update_device_status(dev_id, "online", update_last_seen=True)
    emit("connection_status", {
        "status": "CONNECTED",
        "authenticated": bool(user),
        "tcp_bridge_connected": chat_bridge.connected,
        "server_ip": config.get("SERVER_HOST", "192.168.66.128"),
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })


@socketio.on("disconnect")
def handle_disconnect():
    unregister_ws_device(request.sid)


@socketio.on("join_conversation")
def handle_join_conversation(data):
    """
    Authorizes and joins a browser socket strictly to `conversation:<conversation_id>`.
    Leaves any previously active `conversation:*` rooms on this socket so switching
    from A-B to A-C cleanly stops delivering A-B live events to the A-C panel.
    """
    user = session.get("user")
    if not user or not user.get("id"):
        emit("conversation_error", {"code": "unauthorized", "error": "Authentication required."})
        return

    conv_id = ((data or {}).get("conversation_id") or "").strip()
    if not conv_id:
        emit("conversation_error", {"code": "missing_conversation", "error": "conversation_id is required."})
        return

    # Mandatory participant authorization check
    if not is_user_participant(conv_id, user["id"]):
        emit("conversation_error", {
            "code": "forbidden",
            "conversation_id": conv_id,
            "error": "Forbidden: you are not a participant in this conversation."
        })
        return

    target_room = f"conversation:{conv_id}"
    # Leave all previously joined conversation rooms on this socket
    for r in list(rooms()):
        if isinstance(r, str) and r.startswith("conversation:") and r != target_room:
            leave_room(r)

    join_room(target_room)
    emit("joined_conversation", {
        "conversation_id": conv_id,
        "room": target_room,
        "status": "joined"
    })


@socketio.on("leave_conversation")
def handle_leave_conversation(data):
    conv_id = ((data or {}).get("conversation_id") or "").strip()
    if conv_id:
        leave_room(f"conversation:{conv_id}")
        emit("left_conversation", {"conversation_id": conv_id})


@socketio.on("chat_send")
def handle_chat_send(data):
    """
    Handles real-time WebSocket chat message strictly scoped to an authorized `conversation_id`.
    1. Verifies session authentication AND participant membership in `conversation_id`.
    2. Persists message in Supabase `chat_messages` with `conversation_id`.
    3. Dispatches canonical frame over raw TCP socket (:5001); on_tcp_chat_broadcast emits
       strictly `to="conversation:<conversation_id>"` (NEVER global broadcast).
    """
    user = session.get("user")
    if not user or not user.get("id"):
        emit("chat_error", {"code": "unauthorized", "error": "Authentication required to send messages."})
        return

    payload_in = data or {}
    conversation_id = (payload_in.get("conversation_id") or "").strip()
    text = (payload_in.get("text") or payload_in.get("message") or "").strip()
    client_ip = request.remote_addr or "127.0.0.1"

    ok, result, status_code = _send_conversation_message_core(user, conversation_id, text, client_ip)
    if ok:
        frame = result["message"]
        emit("chat_ack", {
            "id": frame["id"],
            "conversation_id": conversation_id,
            "status": "delivered",
            "timestamp": frame["timestamp"],
            "message": frame
        })
    else:
        emit("chat_error", {
            "code": result.get("code", "error"),
            "conversation_id": conversation_id,
            "status": result.get("status", "failed"),
            "message": result.get("message"),
            "error": result.get("error", "Message transmission failed.")
        })


if __name__ == "__main__":
    port = int(get_config_val("FLASK_PORT", 5050))
    print(f"[*] Starting LabNet Flask Gateway with SocketIO on port {port}...")
    socketio.run(app, host="0.0.0.0", port=port, debug=False, use_reloader=False)
