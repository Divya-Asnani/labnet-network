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
from flask_socketio import SocketIO, emit
from dotenv import dotenv_values

# Services
from web.services.tcp_service import send_tcp_message, upload_file_tcp, chat_bridge
from web.services.udp_file_service import upload_file_udp
from web.services.monitoring_service import (
    get_network_status, get_events, run_diagnostic, start_monitoring,
    get_stats, get_dashboard_overview, register_peer_node,
    get_registered_peers, add_event
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
from web.database.repositories.device_repository import register_device, get_device_by_ip
from web.database.repositories.chat_repository import (
    record_chat_message, update_chat_message_status, list_recent_messages, count_messages
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


# Bridge TCP Chat broadcasts to WebSocket clients cleanly
def on_tcp_chat_broadcast(message_obj):
    with app.app_context():
        msg_type = message_obj.get("type", "chat")
        if msg_type in ("presence", "system"):
            # Keep connection/presence notifications strictly separated from chat messages
            socketio.emit("presence_update", {
                "event": message_obj.get("event", "info"),
                "active_clients": message_obj.get("active_clients"),
                "timestamp": message_obj.get("timestamp"),
                "text": message_obj.get("text", "")
            })
        else:
            socketio.emit("chat_broadcast", message_obj)


chat_bridge.add_message_callback(on_tcp_chat_broadcast)
chat_bridge.start()


def _format_chat_row(row: dict) -> dict:
    prof = row.get("profiles") or {}
    dev = row.get("devices") or {}
    sent_at = row.get("sent_at") or ""
    # Display-friendly HH:MM / YYYY-MM-DD HH:MM:SS
    readable = sent_at.replace("T", " ")[:19] if sent_at else ""
    return {
        "id": row.get("id"),
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

@app.route("/login")
def login_page():
    if session.get("user"):
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/logout")
def logout_page():
    user = session.get("user")
    web_session_id = session.get("web_session_id")
    if web_session_id:
        update_session(web_session_id, status="completed")
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
        # Record active HTTP session in Supabase network_sessions
        client_ip = request.remote_addr or "127.0.0.1"
        dev = get_device_by_ip(client_ip)
        ok_sess, sess_row = record_session(
            protocol="HTTP",
            status="active",
            source_device_id=dev["id"] if dev else None,
            destination_port=int(get_config_val("FLASK_PORT", 5050))
        )
        if ok_sess and isinstance(sess_row, dict) and "id" in sess_row:
            session["web_session_id"] = sess_row["id"]

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
        # Only create Flask session if Supabase confirmed the user and issued an auth session
        if not result.get("requires_confirmation") and result.get("user", {}).get("access_token"):
            session["user"] = result["user"]
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
    if web_session_id:
        update_session(web_session_id, status="completed")
    logout_user(user)
    session.clear()
    return jsonify({"status": "logged_out"})


# ============================================================================
# Protected Application Page Routes (Shared Shell)
# ============================================================================

@app.route("/")
@login_required
def index():
    refresh_session_user()
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

@app.route("/api/dashboard/overview", methods=["GET"])
@login_required
def api_dashboard_overview():
    overview = get_dashboard_overview()
    overview["tcp_chat_bridge"] = chat_bridge.connected
    return jsonify(overview)


@app.route("/api/network/status", methods=["GET"])
@login_required
def api_network_status():
    return jsonify({
        "status": get_network_status(),
        "events": get_events(limit=30),
        "stats": get_stats(),
        "tcp_chat_bridge": chat_bridge.connected
    })


@app.route("/api/monitoring/devices", methods=["GET"])
@login_required
def api_monitoring_devices():
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
# Persistent + Real-Time TCP Chat APIs
# ============================================================================

@app.route("/api/chat/history", methods=["GET"])
@login_required
def api_chat_history():
    """
    Loads persistent conversation history from Supabase `chat_messages` in
    chronological order with cursor-based pagination (`before`).
    """
    limit = min(max(int(request.args.get("limit", 50)), 1), 200)
    before_iso = request.args.get("before") or None
    sender_filter = request.args.get("sender_user_id") or None

    rows = list_recent_messages(limit=limit, before_iso=before_iso, sender_user_id=sender_filter)
    formatted = [_format_chat_row(r) for r in rows]
    has_more = len(rows) == limit
    oldest_cursor = formatted[0]["sent_at"] if formatted else None

    return jsonify({
        "messages": formatted,
        "has_more": has_more,
        "oldest_cursor": oldest_cursor,
        "total_count": count_messages(),
        "tcp_bridge_connected": chat_bridge.connected
    })


@app.route("/api/chat/conversations", methods=["GET"])
@login_required
def api_chat_conversations():
    """
    Builds conversation sidebar list from real Supabase data:
    - General Lab Channel (all broadcast messages in `chat_messages`)
    - Registered lab participants from `profiles` + live online status from `devices`
    """
    latest_rows = list_recent_messages(limit=50)
    last_msg = _format_chat_row(latest_rows[-1]) if latest_rows else None
    total = count_messages()

    profiles = list_profiles()
    devices = get_network_status()
    online_by_user = {}
    ip_by_user = {}
    for d in devices:
        uname = d.get("user_name")
        if d.get("status") == "ONLINE":
            online_by_user[uname] = True
        if uname and d.get("ip"):
            ip_by_user[uname] = d.get("ip")

    conversations = [
        {
            "id": "general",
            "title": "LabNet General Channel",
            "subtitle": "Multithreaded TCP Broadcast (:5001)",
            "type": "channel",
            "online": chat_bridge.connected,
            "last_message": last_msg["text"] if last_msg else "No messages yet. Start the conversation.",
            "last_sender": last_msg["sender"] if last_msg else "",
            "last_timestamp": last_msg["timestamp"] if last_msg else "",
            "message_count": total
        }
    ]

    # Add participant filter views backed by real `profiles`
    for p in profiles:
        pid = p.get("id")
        pname = p.get("display_name") or "User"
        p_msgs = [r for r in latest_rows if r.get("sender_user_id") == pid]
        p_last = _format_chat_row(p_msgs[-1]) if p_msgs else None
        conversations.append({
            "id": pid,
            "title": pname,
            "subtitle": f"{(p.get('role') or 'student').capitalize()} • {ip_by_user.get(pname, 'Workstation')}",
            "type": "participant",
            "role": p.get("role") or "student",
            "online": bool(online_by_user.get(pname, False)),
            "last_message": p_last["text"] if p_last else "No messages from this user yet",
            "last_sender": pname,
            "last_timestamp": p_last["timestamp"] if p_last else "",
            "message_count": len(p_msgs)
        })

    return jsonify({"conversations": conversations, "tcp_bridge_connected": chat_bridge.connected})


@app.route("/api/chat/send", methods=["POST"])
@login_required
def api_chat_send():
    """
    HTTP POST chat endpoint. Persists message to Supabase `chat_messages` FIRST
    using the authenticated session user (preventing impersonation), then
    transmits over raw TCP socket (:5001) and updates delivery status.
    """
    user = session["user"]
    data = request.json or {}
    message_text = (data.get("message") or data.get("text") or "").strip()

    if not message_text:
        return jsonify({"error": "Message cannot be empty"}), 400
    if len(message_text) > 4000:
        return jsonify({"error": "Message exceeds maximum length (4000 characters)"}), 400

    client_ip = request.remote_addr or "127.0.0.1"
    dev = get_device_by_ip(client_ip)
    dev_id = dev["id"] if dev else None

    msg_id = str(uuid.uuid4())
    ok_db, db_row = record_chat_message(
        sender_user_id=user["id"],
        message=message_text,
        protocol="TCP",
        status="sent",
        sender_device_id=dev_id,
        message_id=msg_id
    )
    if not ok_db:
        return jsonify({"status": "error", "error": f"Database persistence failed: {db_row}"}), 500

    sent_at = db_row.get("sent_at") if isinstance(db_row, dict) else datetime.datetime.now(datetime.timezone.utc).isoformat()
    timestamp = sent_at.replace("T", " ")[:19]

    frame = {
        "id": msg_id,
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
        return jsonify({"status": "delivered", "message": frame, "response": resp}), 200
    else:
        frame["status"] = "failed"
        update_chat_message_status(msg_id, "failed")
        return jsonify({
            "status": "failed",
            "message": frame,
            "error": f"Message saved to history, but live TCP delivery failed: {resp}"
        }), 503


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
# WebSocket (Socket.IO) Events
# ============================================================================

@socketio.on("connect")
def handle_connect():
    user = session.get("user")
    emit("connection_status", {
        "status": "CONNECTED",
        "authenticated": bool(user),
        "tcp_bridge_connected": chat_bridge.connected,
        "server_ip": config.get("SERVER_HOST", "192.168.66.128"),
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })


@socketio.on("chat_send")
def handle_chat_send(data):
    """
    Handles real-time WebSocket chat message from authenticated browser.
    1. Persists message in Supabase `chat_messages` (authoritative sender from session).
    2. Dispatches canonical frame (with DB UUID) over raw TCP socket to port 5001.
    3. Updates delivery state (`delivered` vs `failed`) cleanly.
    """
    user = session.get("user")
    if not user or not user.get("id"):
        emit("chat_error", {"error": "Authentication required to send messages."})
        return

    text = (data.get("text") or "").strip()
    if not text:
        emit("chat_error", {"error": "Empty message cannot be transmitted."})
        return
    if len(text) > 4000:
        emit("chat_error", {"error": "Message exceeds maximum length (4000 chars)."})
        return

    client_ip = request.remote_addr or "127.0.0.1"
    dev = get_device_by_ip(client_ip)
    dev_id = dev["id"] if dev else None

    msg_id = str(uuid.uuid4())
    ok_db, db_row = record_chat_message(
        sender_user_id=user["id"],
        message=text,
        protocol="TCP",
        status="sent",
        sender_device_id=dev_id,
        message_id=msg_id
    )
    if not ok_db:
        emit("chat_error", {"id": msg_id, "error": f"Failed to persist message to database: {db_row}"})
        return

    sent_at = db_row.get("sent_at") if isinstance(db_row, dict) else datetime.datetime.now(datetime.timezone.utc).isoformat()
    timestamp = sent_at.replace("T", " ")[:19]

    frame = {
        "id": msg_id,
        "type": "chat",
        "sender_user_id": user["id"],
        "sender": user["display_name"],
        "sender_role": user["role"],
        "device_ip": client_ip,
        "text": text,
        "protocol": "TCP",
        "status": "delivered",
        "sent_at": sent_at,
        "timestamp": timestamp
    }

    success, resp = chat_bridge.send_chat_message(frame)
    if success:
        update_chat_message_status(msg_id, "delivered")
        emit("chat_ack", {
            "id": msg_id,
            "status": "delivered",
            "timestamp": timestamp,
            "message": frame
        })
    else:
        frame["status"] = "failed"
        update_chat_message_status(msg_id, "failed")
        # Still emit the failed message to the sender so UI shows failed state honestly
        emit("chat_error", {
            "id": msg_id,
            "status": "failed",
            "message": frame,
            "error": f"Saved in history, but TCP Chat Server (:5001) is unreachable: {resp}"
        })


if __name__ == "__main__":
    port = int(get_config_val("FLASK_PORT", 5050))
    print(f"[*] Starting LabNet Flask Gateway with SocketIO on port {port}...")
    socketio.run(app, host="0.0.0.0", port=port, debug=False, use_reloader=False)
