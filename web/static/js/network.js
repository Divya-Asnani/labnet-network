/**
 * LabNet Network Diagnostics & Services Frontend (web/static/js/network.js)
 * =========================================================================
 * Drives ICMP, DNS, SMTP, POP3, and live workstation device monitoring.
 */

function runCustomDiag(command) {
    const term = document.getElementById('diag-terminal');
    const target = document.getElementById('diag-target-ip').value || '192.168.66.128';
    term.textContent = `student@labnet:~$ ${command} ${target}\nExecuting diagnostic tool over kernel sockets...\n\n`;

    fetch(`/api/diagnostics/${command}?target=${encodeURIComponent(target)}`)
        .then(res => res.json())
        .then(data => {
            if (data.output) {
                term.textContent += data.output;
            } else if (data.error) {
                term.textContent += 'Error: ' + data.error;
            }
            term.scrollTop = term.scrollHeight;
        })
        .catch(err => {
            term.textContent += 'Failed to execute diagnostic command: ' + err.message;
        });
}

function runDnsLookup() {
    const domain = document.getElementById('dns-domain').value || 'localhost';
    const rtype = document.getElementById('dns-type').value || 'A';
    const term = document.getElementById('dns-terminal');

    term.textContent = `[*] Querying DNS for '${domain}' (${rtype})...\n`;

    fetch('/api/dns/lookup', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({domain: domain, record_type: rtype})
    })
    .then(res => res.json())
    .then(data => {
        if (data.status) {
            term.textContent = `[DNS RESULT] Status: ${data.status}\n`;
            term.textContent += `  Domain:      ${data.domain}\n`;
            term.textContent += `  Nameserver:  ${data.nameserver}\n`;
            term.textContent += `  Latency:     ${data.duration_ms} ms\n`;
            term.textContent += `  Addresses:   ${JSON.stringify(data.resolved_addresses)}\n`;
            if (data.error) {
                term.textContent += `  Note:        ${data.error}\n`;
            }
        } else {
            term.textContent += `[-] Error: ${data.error || 'Lookup failed'}\n`;
        }
    })
    .catch(err => {
        term.textContent += `[-] DNS request error: ${err.message}\n`;
    });
}

function sendSmtpEmail() {
    const recipient = document.getElementById('smtp-to').value;
    const subject = document.getElementById('smtp-subject').value;
    const body = document.getElementById('smtp-body').value;
    const resBox = document.getElementById('smtp-result');

    resBox.textContent = "Connecting to SMTP server on port 1025...";

    fetch('/api/mail/send', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            sender: 'researcher@labnet.local',
            recipient: recipient,
            subject: subject,
            body: body
        })
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === 'DELIVERED') {
            resBox.innerHTML = `<span style="color: #16a34a; font-weight: 600;">[OK] Delivered in ${data.duration_ms}ms</span><br>EHLO: ${data.ehlo_response}<br>Queued to ${data.recipient}`;
        } else {
            resBox.innerHTML = `<span style="color: #dc2626;">[FAIL] ${data.error || 'Delivery failed'}</span>`;
        }
    })
    .catch(err => {
        resBox.innerHTML = `<span style="color: #dc2626;">Network error: ${err.message}</span>`;
    });
}

function fetchPop3Mail() {
    const resBox = document.getElementById('pop3-result');
    resBox.textContent = "Connecting to POP3 service on port 1100...";

    fetch('/api/mail/inbox?user=student')
    .then(res => res.json())
    .then(data => {
        if (data.status === 'SUCCESS') {
            let html = `<div style="font-weight: 600; color: #16a34a; margin-bottom: 4px;">Inbox: ${data.message_count} messages (${data.total_octets} octets)</div>`;
            if (data.messages && data.messages.length > 0) {
                data.messages.forEach(m => {
                    html += `
                    <div style="background: #fff; border: 1px solid #cbd5e1; padding: 6px; border-radius: 4px; margin-bottom: 6px;">
                        <div style="font-weight: 600; color: #1e293b;">Message #${m.index} (${m.size_bytes}B)</div>
                        <pre style="margin: 0; font-size: 0.7rem; color: #475569; white-space: pre-wrap;">${m.content}</pre>
                    </div>`;
                });
            } else {
                html += `<div>Mailbox is empty.</div>`;
            }
            resBox.innerHTML = html;
        } else {
            resBox.innerHTML = `<span style="color: #dc2626;">[POP3 Error] ${data.error}</span>`;
        }
    })
    .catch(err => {
        resBox.innerHTML = `<span style="color: #dc2626;">Network error: ${err.message}</span>`;
    });
}
