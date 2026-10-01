
function sendChat(sender, inputId) {
    const input = document.getElementById(inputId);
    const msg = input.value.trim();
    if(!msg) return;
    
    // Add locally to the sender's window
    appendChat(sender.toLowerCase() + '-history', 'You', msg);
    input.value = '';
    
    // Call Flask backend -> TCP Socket
    fetch('/api/chat/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: msg, sender: sender })
    })
    .then(res => res.json())
    .then(data => {
        if(data.status === 'delivered') {
            appendChat(sender.toLowerCase() + '-history', 'Server', data.response);
        } else {
            appendChat(sender.toLowerCase() + '-history', 'System Error', data.error);
        }
    })
    .catch(err => {
        appendChat(sender.toLowerCase() + '-history', 'System Error', 'Failed to connect to Flask');
    });
}

function appendChat(containerId, author, text) {
    const container = document.getElementById(containerId);
    if(!container) return;
    
    const div = document.createElement('div');
    div.className = 'chat-msg';
    div.innerHTML = `<strong>${author}:</strong> ${text}`;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

document.getElementById('student-input')?.addEventListener('keypress', e => { if (e.key === 'Enter') sendChat('Student', 'student-input'); });
document.getElementById('faculty-input')?.addEventListener('keypress', e => { if (e.key === 'Enter') sendChat('Faculty', 'faculty-input'); });
