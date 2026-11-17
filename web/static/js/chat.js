/**
 * LabNet Real-Time TCP Chat Client (web/static/js/chat.js)
 * ========================================================
 * Connects the browser to the Flask-SocketIO gateway.
 * Messages flow: Browser -> WebSocket -> Flask -> TCP Socket -> TCP Chat Server -> Broadcast.
 */

let socket = null;
let packetsSent = 0;
let packetsRecv = 0;
let renderedMsgIds = new Set();

document.addEventListener("DOMContentLoaded", function () {
    initChatSocket();
    setupUserIdentity();
});

function getActiveUsername() {
    const select = document.getElementById("user-select");
    if (!select) return "Researcher";
    if (select.value === "Custom") {
        const customInput = document.getElementById("custom-user-input");
        return (customInput && customInput.value.trim()) || "CustomUser";
    }
    return select.value;
}

function getActiveDeviceIP() {
    const user = getActiveUsername();
    if (user === "Researcher") return "192.168.66.128";
    if (user === "Student") return "192.168.66.130";
    if (user === "Faculty") return "192.168.66.131";
    return window.location.hostname || "192.168.66.128";
}

function setupUserIdentity() {
    // If running on port 8000 on a specific host or query param, detect persona
    const urlParams = new URLSearchParams(window.location.search);
    const userParam = urlParams.get("user");
    const select = document.getElementById("user-select");
    if (userParam && select) {
        for (let i = 0; i < select.options.length; i++) {
            if (select.options[i].value.toLowerCase() === userParam.toLowerCase()) {
                select.selectedIndex = i;
                break;
            }
        }
    }
    changeUser();
}

function changeUser() {
    const select = document.getElementById("user-select");
    const customWrap = document.getElementById("custom-user-wrap");
    const devIpSpan = document.getElementById("disp-device-ip");

    if (select && select.value === "Custom") {
        if (customWrap) customWrap.style.display = "block";
    } else {
        if (customWrap) customWrap.style.display = "none";
    }

    if (devIpSpan) {
        devIpSpan.textContent = getActiveDeviceIP();
    }
}

function initChatSocket() {
    const badge = document.getElementById("conn-badge");

    // Initialize Socket.IO connection to Flask
    socket = io({
        reconnection: true,
        reconnectionDelay: 1000,
        reconnectionAttempts: 20
    });

    socket.on("connect", function () {
        console.log("[SOCKET.IO] Connected to Flask gateway.");
        updateConnectionBadge(true);
        addSystemNotice("Connected to Flask Web Gateway & TCP Chat Bridge.");
    });

    socket.on("disconnect", function () {
        console.warn("[SOCKET.IO] Disconnected from Flask gateway.");
        updateConnectionBadge(false);
        addSystemNotice("Disconnected from gateway. Reconnecting...");
    });

    socket.on("connect_error", function (err) {
        console.error("[SOCKET.IO] Connection error:", err);
        updateConnectionBadge(false);
    });

    socket.on("connection_status", function (data) {
        if (data.server_ip) {
            const ipEl = document.getElementById("disp-server-ip");
            if (ipEl) ipEl.textContent = data.server_ip;
        }
    });

    // Handle real-time broadcast frames from TCP Chat Server
    socket.on("chat_broadcast", function (msg) {
        packetsRecv++;
        updatePacketCounters();

        // Prevent duplicate rendering if already rendered
        if (msg.id && renderedMsgIds.has(msg.id)) {
            return;
        }
        if (msg.id) renderedMsgIds.add(msg.id);

        renderChatMessage(msg);
    });

    // Handle delivery acknowledgments
    socket.on("chat_ack", function (ack) {
        const statusEl = document.getElementById("chat-delivery-status");
        if (statusEl) {
            statusEl.innerHTML = `<span class="text-green"><i class="fa-solid fa-check-double"></i> Delivered via TCP Socket [5001] at ${ack.timestamp}</span>`;
        }
    });

    socket.on("chat_error", function (err) {
        const statusEl = document.getElementById("chat-delivery-status");
        if (statusEl) {
            statusEl.innerHTML = `<span class="text-red"><i class="fa-solid fa-triangle-exclamation"></i> Error: ${err.error}</span>`;
        }
    });
}

function updateConnectionBadge(isConnected) {
    const badge = document.getElementById("conn-badge");
    if (!badge) return;

    if (isConnected) {
        badge.className = "badge badge-success";
        badge.innerHTML = '<i class="fa-solid fa-circle"></i> CONNECTED';
    } else {
        badge.className = "badge badge-danger";
        badge.innerHTML = '<i class="fa-solid fa-circle-xmark"></i> DISCONNECTED';
    }
}

function updatePacketCounters() {
    const sentEl = document.getElementById("stats-sent");
    const recvEl = document.getElementById("stats-recv");
    if (sentEl) sentEl.textContent = packetsSent;
    if (recvEl) recvEl.textContent = packetsRecv;
}

function sendChatMessage() {
    const input = document.getElementById("chat-text-input");
    if (!input) return;

    const text = input.value.trim();
    if (!text) return;

    const sender = getActiveUsername();
    const deviceIP = getActiveDeviceIP();

    // Check socket state
    if (!socket || !socket.connected) {
        alert("Cannot send message: WebSocket connection is currently disconnected.");
        return;
    }

    const payload = {
        text: text,
        sender: sender,
        device_ip: deviceIP
    };

    // Emit event to Flask -> Flask writes to TCP socket
    socket.emit("chat_send", payload);

    packetsSent++;
    updatePacketCounters();
    input.value = "";

    const statusEl = document.getElementById("chat-delivery-status");
    if (statusEl) {
        statusEl.innerHTML = `<span><i class="fa-solid fa-spinner fa-spin"></i> Transmitting over TCP socket to port 5001...</span>`;
    }
}

function renderChatMessage(msg) {
    const container = document.getElementById("chat-messages");
    if (!container) return;

    const currentUser = getActiveUsername();
    const isSelf = (msg.sender === currentUser);
    const msgType = msg.type || "chat";

    if (msgType === "system") {
        addSystemNotice(msg.text);
        return;
    }

    const item = document.createElement("div");
    item.className = `chat-bubble ${isSelf ? "bubble-outgoing" : "bubble-incoming"}`;

    const senderBadgeClass = getBadgeClassForUser(msg.sender);

    item.innerHTML = `
        <div class="bubble-header">
            <span class="bubble-sender ${senderBadgeClass}">${escapeHtml(msg.sender)}</span>
            <span class="bubble-ip">${escapeHtml(msg.device_ip || msg.origin_addr || "")}</span>
            <span class="bubble-time">${escapeHtml(msg.timestamp || "")}</span>
        </div>
        <div class="bubble-content">${escapeHtml(msg.text)}</div>
        <div class="bubble-footer">
            <span class="proto-tag"><i class="fa-solid fa-network-wired"></i> TCP</span>
            ${isSelf ? '<span class="status-tag"><i class="fa-solid fa-check"></i> Dispatched</span>' : ''}
        </div>
    `;

    container.appendChild(item);
    container.scrollTop = container.scrollHeight;
}

function addSystemNotice(text) {
    const container = document.getElementById("chat-messages");
    if (!container) return;

    const notice = document.createElement("div");
    notice.className = "system-message";
    notice.innerHTML = `<i class="fa-solid fa-bell"></i> ${escapeHtml(text)}`;
    container.appendChild(notice);
    container.scrollTop = container.scrollHeight;
}

function getBadgeClassForUser(username) {
    if (!username) return "user-default";
    const u = username.toLowerCase();
    if (u.includes("researcher")) return "user-researcher";
    if (u.includes("student")) return "user-student";
    if (u.includes("faculty")) return "user-faculty";
    return "user-default";
}

function escapeHtml(str) {
    if (!str) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// Support Enter key submission
document.addEventListener("keypress", function (e) {
    if (e.key === "Enter" && document.activeElement && document.activeElement.id === "chat-text-input") {
        sendChatMessage();
    }
});
