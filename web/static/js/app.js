
function fetchNetworkStatus() {
    fetch('/api/network/status')
        .then(res => res.json())
        .then(data => {
            // Update stats table if it exists
            const statIp = document.getElementById('stat-server-ip');
            if(statIp) {
                document.getElementById('stat-clients').textContent = data.stats.active_clients;
                document.getElementById('stat-tcp').textContent = data.stats.tcp_connections;
                document.getElementById('stat-udp').textContent = data.stats.udp_messages;
                document.getElementById('stat-bytes').textContent = data.stats.bytes_transferred + " B";
                document.getElementById('stat-heartbeat').textContent = data.stats.last_heartbeat;
            }
            
            // Update activity feed if it exists
            const feed = document.getElementById('activity-feed');
            if(feed && data.events) {
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
        });
}

// Poll every 2 seconds
setInterval(fetchNetworkStatus, 2000);
fetchNetworkStatus();
