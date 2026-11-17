import os
import uuid
import datetime
import hashlib
from pathlib import Path
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_from_directory
from flask_socketio import SocketIO, emit
from dotenv import dotenv_values

# Services
from web.services.tcp_service import send_tcp_message, upload_file_tcp, chat_bridge
from web.services.udp_file_service import upload_file_udp
from web.services.monitoring_service import (
    get_network_status, get_events, run_diagnostic, start_monitoring,
    get_stats, register_peer_node, get_registered_peers, add_event
)
from web.services.auth_service import login_required, login_user, register_user, logout_user
from server.dns_service import resolve_domain
from server.smtp_service import send_mail_smtp
from server.pop3_service import fetch_pop3_inbox
from server.mail_test_server import start_mail_servers

# Database Repositories
from web.database.repositories.chat_repository import record_chat_message, list_recent_messages
from web.database.repositories.file_repository import record_file_transfer, list_transfers
from web.database.supabase_client import get_config_val, get_supabase_admin

# Environment setup
root_dir = Path(__file__).resolve().parent.parent
env_path = root_dir / '.env'
config = dotenv_values(env_path) if env_path.exists() else {}

UPLOAD_FOLDER = os.path.join(str(root_dir), config.get("UPLOAD_FOLDER", "uploads"))
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)
app.config['SECRET_KEY'] = config.get('SECRET_KEY', 'labnet-cnt-project-key-2026')
app.config['MAX_CONTENT_LENGTH'] = int(config.get('MAX_UPLOAD_SIZE_MB', 100)) * 1024 * 1024
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Start background monitoring and mail test servers
start_monitoring()
try:
    start_mail_servers()
except Exception as e:
    print(f"[*] Note on mail servers: {e}")


# Bridge TCP Chat broadcasts to WebSocket clients
def on_tcp_chat_broadcast(message_obj):
    with app.app_context():
        socketio.emit('chat_broadcast', message_obj)


chat_bridge.add_message_callback(on_tcp_chat_broadcast)
chat_bridge.start()


# Helper to get current or default user ID for audit tables
def get_current_user_id():
    user = session.get('user')
    if user and 'id' in user:
        return user['id']
    admin_client = get_supabase_admin()
    if admin_client:
        try:
            res = admin_client.table('profiles').select('id').limit(1).execute()
            if res.data:
                return res.data[0]['id']
        except Exception:
            pass
    return None


# ============================================================================
# Authentication Routes (Supabase Auth)
# ============================================================================

@app.route('/login')
def login_page():
    if session.get('user'):
        return redirect(url_for('index'))
    return render_template('login.html')


@app.route('/logout')
def logout_page():
    logout_user()
    session.clear()
    return redirect(url_for('login_page'))


@app.route('/api/auth/login', methods=['POST'])
def api_auth_login():
    data = request.json or {}
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()

    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400

    success, result = login_user(email, password)
    if success:
        session['user'] = result
        return jsonify({'status': 'success', 'user': result})
    else:
        return jsonify({'error': result}), 401


@app.route('/api/auth/register', methods=['POST'])
def api_auth_register():
    data = request.json or {}
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()
    display_name = data.get('display_name', '').strip()
    role = data.get('role', 'student').strip()

    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400

    success, result = register_user(email, password, display_name=display_name, role=role)
    if success:
        session['user'] = result
        return jsonify({'status': 'success', 'user': result})
    else:
        return jsonify({'error': result}), 400


@app.route('/api/auth/user', methods=['GET'])
def api_auth_user():
    user = session.get('user')
    if user:
        return jsonify({'authenticated': True, 'user': user})
    return jsonify({'authenticated': False})


# ============================================================================
# HTTP Page Routes (Protected by Auth where applicable)
# ============================================================================

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/chat')
@login_required
def chat():
    return render_template('chat.html')


@app.route('/files')
@login_required
def files():
    return render_template('files.html')


@app.route('/network')
@login_required
def network():
    return render_template('network.html')


# ============================================================================
# REST API Endpoints
# ============================================================================

@app.route('/api/network/status', methods=['GET'])
def api_network_status():
    return jsonify({
        'status': get_network_status(),
        'events': get_events(),
        'stats': get_stats(),
        'tcp_chat_bridge': chat_bridge.connected
    })


@app.route('/api/chat/send', methods=['POST'])
def api_chat_send():
    data = request.json or {}
    message_text = data.get('message', '').strip()
    sender = data.get('sender', 'Student')
    device_ip = data.get('device_ip', request.remote_addr)

    if not message_text:
        return jsonify({'error': 'No message provided'}), 400

    msg_id = str(uuid.uuid4())
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    frame = {
        "id": msg_id,
        "type": "chat",
        "sender": sender,
        "device_ip": device_ip,
        "text": message_text,
        "timestamp": timestamp
    }

    # Transmit through raw TCP socket to tcp_chat_server.py
    success, resp = chat_bridge.send_chat_message(frame)

    # Persist in Supabase chat_messages table
    uid = get_current_user_id()
    if uid:
        try:
            record_chat_message(
                sender_user_id=uid,
                message=message_text,
                protocol='TCP',
                status='delivered' if success else 'failed'
            )
        except Exception:
            pass

    if success:
        return jsonify({'status': 'delivered', 'message': frame, 'response': resp})
    else:
        return jsonify({'status': 'error', 'error': resp}), 503


@app.route('/api/files/upload', methods=['POST'])
def api_files_upload():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part in request'}), 400

    uploaded_file = request.files['file']
    if uploaded_file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    protocol = request.form.get('protocol', 'TCP').upper()
    sender = request.form.get('sender', 'Student')
    sender_ip = request.form.get('sender_ip', request.remote_addr)
    dest_mode = request.form.get('dest_mode', 'server')

    # Measure stream size
    uploaded_file.seek(0, os.SEEK_END)
    filesize = uploaded_file.tell()
    uploaded_file.seek(0)

    def progress_tracker(bytes_sent, total_bytes, percent, speed_kbps, retries=0):
        socketio.emit('file_progress', {
            'filename': uploaded_file.filename,
            'bytes_sent': bytes_sent,
            'total_bytes': total_bytes,
            'percent': percent,
            'speed_kbps': speed_kbps,
            'retries': retries,
            'status': 'STREAMING'
        })

    uid = get_current_user_id()

    if protocol == 'TCP':
        success, result = upload_file_tcp(
            file_stream=uploaded_file.stream,
            filename=uploaded_file.filename,
            filesize=filesize,
            sender=sender,
            sender_ip=sender_ip,
            progress_callback=progress_tracker
        )
    elif protocol == 'UDP':
        success, result = upload_file_udp(
            file_stream=uploaded_file.stream,
            filename=uploaded_file.filename,
            filesize=filesize,
            sender=sender,
            sender_ip=sender_ip,
            progress_callback=progress_tracker
        )
    else:
        return jsonify({'error': f"Unsupported protocol '{protocol}'"}), 400

    if success and isinstance(result, dict):
        socketio.emit('file_progress', {
            'filename': uploaded_file.filename,
            'percent': 100.0,
            'status': 'COMPLETED',
            'sha256': result.get('sha256'),
            'verified': result.get('verified'),
            'save_path': result.get('save_path')
        })

        # Persist in Supabase file_transfers table
        if uid:
            try:
                record_file_transfer(
                    sender_user_id=uid,
                    file_name=uploaded_file.filename,
                    file_size=filesize,
                    protocol=protocol,
                    checksum=result.get('sha256'),
                    storage_path=result.get('save_path'),
                    status='completed' if result.get('verified') else 'failed'
                )
            except Exception as e:
                print(f"[DB LOG ERROR] {e}")

        return jsonify({'status': 'completed', 'result': result})
    else:
        return jsonify({'status': 'error', 'error': str(result)}), 500


# ============================================================================
# Section 5.7: File Repository & Remote File Retrieval / Download
# ============================================================================

@app.route('/api/files/list', methods=['GET'])
def api_files_list():
    """Returns directory listing of server's uploads/ folder with metadata."""
    files = []
    if os.path.exists(UPLOAD_FOLDER):
        for fname in os.listdir(UPLOAD_FOLDER):
            fpath = os.path.join(UPLOAD_FOLDER, fname)
            if os.path.isfile(fpath) and fname != '.gitkeep':
                size = os.path.getsize(fpath)
                mtime = os.path.getmtime(fpath)

                # Compute quick sha256
                hasher = hashlib.sha256()
                with open(fpath, "rb") as f:
                    while chunk := f.read(65536):
                        hasher.update(chunk)

                files.append({
                    "filename": fname,
                    "size": size,
                    "sha256": hasher.hexdigest(),
                    "protocol": "TCP/UDP",
                    "modified": datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
                })
    return jsonify({'files': files})


@app.route('/api/files/download/<path:filename>', methods=['GET'])
def api_files_download(filename):
    """
    Secure file retrieval endpoint. Enforces path traversal protection
    and logs download events.
    """
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(UPLOAD_FOLDER, safe_filename)

    # Validate that path is strictly within UPLOAD_FOLDER
    abs_upload = os.path.abspath(UPLOAD_FOLDER)
    abs_file = os.path.abspath(file_path)
    if not abs_file.startswith(abs_upload) or not os.path.isfile(abs_file):
        return jsonify({'error': 'File not found or access denied'}), 404

    add_event('FILE DOWNLOAD', f"Workstation retrieved '{safe_filename}'\nSize: {os.path.getsize(abs_file) / 1024:.2f} KB\nPath: {safe_filename}")

    return send_from_directory(UPLOAD_FOLDER, safe_filename, as_attachment=True)


# ============================================================================
# Section 3.3: Peer-to-Peer (P2P) Coordination Endpoints
# ============================================================================

@app.route('/api/peers/register', methods=['POST'])
def api_peers_register():
    data = request.json or {}
    peer_name = data.get('peer_name') or f"node-{request.remote_addr}"
    ip = data.get('ip') or request.remote_addr
    port = int(data.get('port', 2123))
    status = data.get('status', 'READY')

    peer_info = register_peer_node(peer_name, ip, port, status)
    return jsonify({'status': 'registered', 'peer': peer_info})


@app.route('/api/peers/list', methods=['GET'])
def api_peers_list():
    return jsonify({'peers': get_registered_peers()})


# ============================================================================
# Phase 4: Network Services (DNS, SMTP, POP3, Diagnostics)
# ============================================================================

@app.route('/api/dns/lookup', methods=['POST'])
def api_dns_lookup():
    data = request.json or {}
    domain = data.get('domain', 'localhost')
    record_type = data.get('record_type', 'A')

    result = resolve_domain(domain, record_type)
    add_event('DNS QUERY', f"Query: {domain} ({record_type})\nResult: {result.get('resolved_addresses')}\nDuration: {result.get('duration_ms')}ms")
    return jsonify(result)


@app.route('/api/mail/send', methods=['POST'])
def api_mail_send():
    data = request.json or {}
    sender = data.get('sender', 'researcher@labnet.local')
    recipient = data.get('recipient', 'student@labnet.local')
    subject = data.get('subject', 'LabNet Notification')
    body = data.get('body', '')

    success, result = send_mail_smtp(sender, recipient, subject, body)
    if success:
        add_event('SMTP TRANSMIT', f"From: {sender} → To: {recipient}\nSubject: {subject}\nStatus: DELIVERED ({result.get('duration_ms')}ms)")
        return jsonify(result)
    else:
        return jsonify(result), 500


@app.route('/api/mail/inbox', methods=['GET'])
def api_mail_inbox():
    user = request.args.get('user', 'student')
    success, result = fetch_pop3_inbox(user=user)
    if success:
        add_event('POP3 RETRIEVAL', f"User '{user}' fetched inbox\nMessages: {result.get('message_count')}\nOctets: {result.get('total_octets')}")
        return jsonify(result)
    else:
        return jsonify(result), 500


@app.route('/api/diagnostics/<command>', methods=['GET'])
def api_diagnostics(command):
    target = request.args.get('target', None)
    result = run_diagnostic(command, target=target)
    return jsonify({'output': result})


# ============================================================================
# WebSocket (Socket.IO) Events
# ============================================================================

@socketio.on('connect')
def handle_connect():
    emit('connection_status', {
        'status': 'CONNECTED',
        'tcp_bridge_connected': chat_bridge.connected,
        'server_ip': config.get('SERVER_HOST', '192.168.66.128'),
        'timestamp': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })


@socketio.on('chat_send')
def handle_chat_send(data):
    text = data.get('text', '').strip()
    sender = data.get('sender', 'Anonymous')
    device_ip = data.get('device_ip', request.remote_addr)

    if not text:
        emit('chat_error', {'error': 'Empty message cannot be transmitted'})
        return

    frame = {
        "id": str(uuid.uuid4()),
        "type": "chat",
        "sender": sender,
        "device_ip": device_ip,
        "text": text,
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    success, resp = chat_bridge.send_chat_message(frame)

    # Persist in Supabase chat_messages table
    uid = get_current_user_id()
    if uid:
        try:
            record_chat_message(
                sender_user_id=uid,
                message=text,
                protocol='TCP',
                status='delivered' if success else 'failed'
            )
        except Exception:
            pass

    if success:
        emit('chat_ack', {'id': frame['id'], 'status': 'delivered', 'timestamp': frame['timestamp']})
    else:
        emit('chat_error', {'id': frame['id'], 'error': f"TCP Socket failure: {resp}"})


if __name__ == '__main__':
    port = int(get_config_val('FLASK_PORT', 5050))
    print(f"[*] Starting LabNet Flask Gateway with SocketIO on port {port}...")
    socketio.run(app, host='0.0.0.0', port=port, debug=False, use_reloader=False)
