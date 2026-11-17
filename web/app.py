import os
import uuid
import datetime
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
from services.tcp_service import send_tcp_message, upload_file_tcp, chat_bridge
from services.monitoring_service import get_network_status, get_events, run_diagnostic, start_monitoring, get_stats

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'labnet-cnt-project-key-2026')
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB max upload

# Initialize SocketIO with threading async mode
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Start monitoring in background
start_monitoring()


# Bridge TCP Chat broadcasts to WebSocket clients
def on_tcp_chat_broadcast(message_obj):
    """
    Invoked whenever server/tcp_chat_server.py broadcasts a frame
    over the persistent TCP socket. Forwards the event to all
    browser WebSocket connections.
    """
    with app.app_context():
        socketio.emit('chat_broadcast', message_obj)


chat_bridge.add_message_callback(on_tcp_chat_broadcast)
chat_bridge.start()


# ============================================================================
# HTTP Page Routes
# ============================================================================

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/chat')
def chat():
    return render_template('chat.html')


@app.route('/files')
def files():
    return render_template('files.html')


@app.route('/network')
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
    """
    HTTP POST chat endpoint. Bridges incoming HTTP message to the
    persistent TCP chat socket.
    """
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

    # Send over raw TCP socket to tcp_chat_server.py
    success, resp = chat_bridge.send_chat_message(frame)
    if success:
        return jsonify({'status': 'delivered', 'message': frame, 'response': resp})
    else:
        # Fallback to direct TCP connection if bridge is reconnecting
        return jsonify({'status': 'error', 'error': resp}), 503


@app.route('/api/files/upload', methods=['POST'])
def api_files_upload():
    """
    Receives file upload via multipart/form-data and streams it
    directly over a raw TCP socket to server/tcp_file_server.py on port 2121.
    """
    if 'file' not in request.files:
        return jsonify({'error': 'No file part in request'}), 400

    uploaded_file = request.files['file']
    if uploaded_file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    protocol = request.form.get('protocol', 'TCP')
    sender = request.form.get('sender', 'Student')
    sender_ip = request.form.get('sender_ip', request.remote_addr)

    if protocol == 'TCP':
        # Measure stream size
        uploaded_file.seek(0, os.SEEK_END)
        filesize = uploaded_file.tell()
        uploaded_file.seek(0)

        def progress_tracker(bytes_sent, total_bytes, percent, speed_kbps):
            socketio.emit('file_progress', {
                'filename': uploaded_file.filename,
                'bytes_sent': bytes_sent,
                'total_bytes': total_bytes,
                'percent': percent,
                'speed_kbps': speed_kbps,
                'status': 'STREAMING'
            })

        success, result = upload_file_tcp(
            file_stream=uploaded_file.stream,
            filename=uploaded_file.filename,
            filesize=filesize,
            sender=sender,
            sender_ip=sender_ip,
            progress_callback=progress_tracker
        )

        if success:
            # Emit completed event
            socketio.emit('file_progress', {
                'filename': uploaded_file.filename,
                'percent': 100.0,
                'status': 'COMPLETED',
                'sha256': result.get('sha256'),
                'verified': result.get('verified'),
                'save_path': result.get('save_path')
            })
            return jsonify({
                'status': 'completed',
                'result': result
            })
        else:
            return jsonify({
                'status': 'error',
                'error': str(result)
            }), 500

    else:
        return jsonify({'error': 'UDP file transfer is planned for Phase 3.'}), 501


@app.route('/api/diagnostics/<command>', methods=['GET'])
def api_diagnostics(command):
    if command not in ['ping', 'traceroute', 'tcp_connections', 'udp_status']:
        return jsonify({'error': 'Invalid command'}), 400

    result = run_diagnostic(command)
    return jsonify({'output': result})


# ============================================================================
# WebSocket (Socket.IO) Events
# ============================================================================

@socketio.on('connect')
def handle_connect():
    emit('connection_status', {
        'status': 'CONNECTED',
        'tcp_bridge_connected': chat_bridge.connected,
        'server_ip': os.getenv('LABNET_SERVER_IP', '192.168.66.128'),
        'timestamp': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })


@socketio.on('chat_send')
def handle_chat_send(data):
    """
    Handles real-time WebSocket chat message from browser.
    Formats frame and transmits over TCP socket to tcp_chat_server.py.
    """
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

    # Transmit through raw TCP socket to server/tcp_chat_server.py
    success, resp = chat_bridge.send_chat_message(frame)
    if success:
        emit('chat_ack', {'id': frame['id'], 'status': 'delivered', 'timestamp': frame['timestamp']})
    else:
        emit('chat_error', {'id': frame['id'], 'error': f"TCP Socket failure: {resp}"})


if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    print(f"[*] Starting LabNet Flask Gateway with SocketIO on port {port}...")
    socketio.run(app, host='0.0.0.0', port=port, debug=False, use_reloader=False)
