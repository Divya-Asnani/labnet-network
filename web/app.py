import os
from flask import Flask, render_template, request, jsonify
from services.tcp_service import send_tcp_message, upload_file_tcp
from services.monitoring_service import get_network_status, get_events, run_diagnostic, start_monitoring, get_stats

app = Flask(__name__)

# Start monitoring in background
start_monitoring()

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

@app.route('/api/network/status', methods=['GET'])
def api_network_status():
    return jsonify({
        'status': get_network_status(),
        'events': get_events(),
        'stats': get_stats()
    })

@app.route('/api/chat/send', methods=['POST'])
def api_chat_send():
    data = request.json
    message = data.get('message', '')
    sender = data.get('sender', 'Student')
    
    if not message:
        return jsonify({'error': 'No message provided'}), 400
    
    success, response = send_tcp_message(message, sender)
    if success:
        return jsonify({'status': 'delivered', 'response': response})
    else:
        return jsonify({'status': 'error', 'error': response}), 500

@app.route('/api/files/upload', methods=['POST'])
def api_files_upload():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    protocol = request.form.get('protocol', 'TCP')
    sender = request.form.get('sender', 'Student')
    
    if protocol == 'TCP':
        file_content = file.read()
        success, response = upload_file_tcp(file.filename, file_content, sender)
        if success:
            return jsonify({'status': 'completed', 'message': 'File transferred via TCP'})
        else:
            return jsonify({'status': 'error', 'error': response}), 500
    else:
        return jsonify({'error': 'Protocol not implemented yet'}), 501

@app.route('/api/diagnostics/<command>', methods=['GET'])
def api_diagnostics(command):
    if command not in ['ping', 'traceroute', 'tcp_connections', 'udp_status']:
        return jsonify({'error': 'Invalid command'}), 400
    
    result = run_diagnostic(command)
    return jsonify({'output': result})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True, use_reloader=False)
