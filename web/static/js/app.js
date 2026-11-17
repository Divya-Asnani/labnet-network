function fetchNetworkStatus() {
    fetch('/api/network/status')
        .then(res => res.json())
        .then(data => {
            // Update stats table if it exists
            const statIp = document.getElementById('stat-server-ip');
            if (statIp && data.stats) {
                const clientsEl = document.getElementById('stat-clients');
                if (clientsEl) clientsEl.textContent = (data.stats.active_clients || 0) + " Workstations";
                const tcpEl = document.getElementById('stat-tcp');
                if (tcpEl) tcpEl.textContent = data.stats.tcp_connections || 0;
                const udpEl = document.getElementById('stat-udp');
                if (udpEl) udpEl.textContent = data.stats.udp_messages || 0;
                const bytesEl = document.getElementById('stat-bytes');
                if (bytesEl) bytesEl.textContent = (data.stats.bytes_transferred || 0) + " B";
                const hbEl = document.getElementById('stat-heartbeat');
                if (hbEl) hbEl.textContent = data.stats.last_heartbeat || "-";
            }

            // Update registered workstations table if it exists
            const nodesTbody = document.getElementById('nodes-tbody');
            if (nodesTbody && data.status) {
                if (data.status.length === 0) {
                    nodesTbody.innerHTML = '<tr><td colspan="5" class="text-muted text-center" style="padding: 10px;">No registered nodes yet.</td></tr>';
                } else {
                    let html = '';
                    data.status.forEach(node => {
                        const isOnline = (node.status === 'ONLINE' || node.status === 'CONNECTED');
                        const statusBadge = isOnline ?
                            '<span class="badge badge-success" style="font-size:0.75rem;">ONLINE</span>' :
                            '<span class="badge badge-danger" style="font-size:0.75rem;">OFFLINE</span>';
                        html += `
                        <tr style="border-bottom: 1px solid #f1f5f9;">
                            <td style="padding: 8px 10px; font-weight: 500;">
                                <i class="fa-solid fa-desktop" style="color: ${isOnline ? '#16a34a' : '#94a3b8'}; margin-right: 6px;"></i>
                                ${node.id}
                            </td>
                            <td style="padding: 8px 10px; font-family: monospace; font-size: 0.8rem; color: #475569;">${node.ip}</td>
                            <td style="padding: 8px 10px;"><span class="badge badge-secondary" style="font-size: 0.7rem;">${node.protocol || 'TCP/UDP'}</span></td>
                            <td style="padding: 8px 10px;">${statusBadge}</td>
                            <td style="padding: 8px 10px; font-size: 0.8rem; color: #64748b;">${node.last_seen || 'N/A'}</td>
                        </tr>
                        `;
                    });
                    nodesTbody.innerHTML = html;
                }
            }

            // Update activity feed if it exists
            const feed = document.getElementById('activity-feed');
            if (feed && data.events) {
                feed.innerHTML = '';
                data.events.forEach(evt => {
                    const item = document.createElement('div');
                    item.className = 'feed-item';
                    item.innerHTML = `
                        <div class="feed-header">[${evt.timestamp}] ${evt.title}</div>
                        <div class="feed-detail">${evt.detail}</div>
                    `;
                    feed.appendChild(item);
                });
            }
        })
        .catch(err => {
            console.warn("Failed to fetch network status:", err);
        });
}

// Poll every 2 seconds
setInterval(fetchNetworkStatus, 2000);
fetchNetworkStatus();
