/**
 * LabNet Persistent & Real-Time TCP Chat Client (web/static/js/chat.js)
 * =====================================================================
 * Architecture:
 *  - Persistent history loaded chronologically from Supabase `/api/chat/history`
 *    (so messages sent while VM2 was offline appear immediately upon return).
 *  - Live delivery flows over Socket.IO -> Flask -> Raw TCP Socket (:5001) -> Broadcast.
 *  - Deduplication keyed strictly by canonical Supabase message UUID (`renderedMsgIds`).
 *  - Connection / presence events stay in the subtle status strip, NEVER polluting messages.
 */

let socket = null;
let renderedMsgIds = new Set();
let allConversations = [];
let activeConvId = "general";
let convFilter = "all";
let oldestCursor = null;
let hasMoreHistory = false;

const CURRENT_USER = window.LABNET_USER || {};

document.addEventListener("DOMContentLoaded", function () {
    initChatSocket();
    loadConversations();
    loadHistory(true);
});

function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function getInitials(name) {
    if (!name) return "LN";
    const parts = String(name).trim().split(/\s+/);
    if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
    return String(name).slice(0, 2).toUpperCase();
}

function formatShortTime(ts) {
    if (!ts) return "";
    const s = String(ts).replace("T", " ");
    return s.length >= 16 ? s.slice(11, 16) : s;
}

function updateBridgePill(wsConnected, tcpConnected) {
    const pill = document.getElementById("tcp-bridge-pill");
    const dot = document.getElementById("tcp-bridge-dot");
    const txt = document.getElementById("tcp-bridge-text");
    if (!pill || !dot || !txt) return;

    if (wsConnected && tcpConnected) {
        pill.className = "badge badge-online";
        dot.className = "status-dot online";
        txt.textContent = "TCP :5001 Connected";
    } else if (wsConnected && !tcpConnected) {
        pill.className = "badge badge-warning";
        dot.className = "status-dot warn";
        txt.textContent = "TCP Server Offline (History Active)";
    } else {
        pill.className = "badge badge-offline";
        dot.className = "status-dot offline";
        txt.textContent = "Gateway Reconnecting...";
    }
}

function setDeliveryStatus(htmlText) {
    const el = document.getElementById("delivery-status-text");
    if (el) el.innerHTML = htmlText;
}

function initChatSocket() {
    socket = io({
        reconnection: true,
        reconnectionDelay: 1500,
        reconnectionAttempts: 30
    });

    socket.on("connect", function () {
        // Re-sync missed messages cleanly upon reconnection
        loadHistory(false, true);
    });

    socket.on("disconnect", function () {
        updateBridgePill(false, false);
        setDeliveryStatus('<span style="color: var(--danger);">WebSocket disconnected — retrying...</span>');
    });

    socket.on("connection_status", function (data) {
        updateBridgePill(true, Boolean(data.tcp_bridge_connected));
    });

    // Separate channel for socket presence events (never inserted as chat bubbles)
    socket.on("presence_update", function (evt) {
        const strip = document.getElementById("presence-status-text");
        if (strip && evt.active_clients !== undefined && evt.active_clients !== null) {
            strip.innerHTML = `<i class="fa-solid fa-circle-nodes" style="color: var(--accent); margin-right: 4px;"></i>TCP Chat Server active sockets: <strong>${evt.active_clients}</strong> • Last update ${escapeHtml(formatShortTime(evt.timestamp))}`;
        }
    });

    // Live chat broadcast from TCP Chat Server (:5001)
    socket.on("chat_broadcast", function (msg) {
        if (!msg || msg.type !== "chat") return;

        // If user is currently viewing a filtered participant view, only append matching sender
        if (activeConvId !== "general" && msg.sender_user_id && msg.sender_user_id !== activeConvId) {
            loadConversations();
            return;
        }

        appendMessageIfNew(msg, true);
        loadConversations();
    });

    // Delivery acknowledgment for outgoing messages
    socket.on("chat_ack", function (ack) {
        if (ack.message) {
            appendMessageIfNew(ack.message, true);
            updateMessageStatusBadge(ack.id, "delivered");
        }
        setDeliveryStatus(`<span style="color: var(--success);"><i class="fa-solid fa-check-double"></i> Delivered via TCP :5001 (${escapeHtml(formatShortTime(ack.timestamp))})</span>`);
        loadConversations();
    });

    // Explicit failure handling when TCP server is unreachable
    socket.on("chat_error", function (err) {
        if (err.message) {
            appendMessageIfNew(err.message, true);
            updateMessageStatusBadge(err.id, "failed");
        }
        setDeliveryStatus(`<span style="color: var(--danger);"><i class="fa-solid fa-triangle-exclamation"></i> ${escapeHtml(err.error || "Delivery failed")}</span>`);
    });
}

async function loadConversations() {
    try {
        const resp = await fetch("/api/chat/conversations");
        if (!resp.ok) return;
        const data = await resp.json();
        allConversations = data.conversations || [];
        updateBridgePill(Boolean(socket && socket.connected), Boolean(data.tcp_bridge_connected));
        renderConversations();
    } catch (e) {
        console.warn("Failed to load conversations:", e);
    }
}

function setConvFilter(mode) {
    convFilter = mode;
    document.getElementById("filter-all").classList.toggle("active", mode === "all");
    document.getElementById("filter-online").classList.toggle("active", mode === "online");
    renderConversations();
}

function filterConversations() {
    renderConversations();
}

function renderConversations() {
    const listEl = document.getElementById("conv-list");
    if (!listEl) return;

    const q = (document.getElementById("conv-search-input").value || "").toLowerCase();
    const filtered = allConversations.filter(c => {
        if (convFilter === "online" && !c.online) return false;
        if (!q) return true;
        return (c.title || "").toLowerCase().includes(q) || (c.last_message || "").toLowerCase().includes(q);
    });

    if (!filtered.length) {
        listEl.innerHTML = `<div style="padding: 20px; text-align: center; color: var(--text-muted); font-size: 12.5px;">No matching conversations.</div>`;
        return;
    }

    listEl.innerHTML = filtered.map(c => {
        const isActive = c.id === activeConvId;
        const avatarContent = c.type === "channel"
            ? '<i class="fa-solid fa-hashtag"></i>'
            : escapeHtml(getInitials(c.title));
        return `
        <div class="conv-item ${isActive ? "active" : ""}" onclick="selectConversation('${escapeHtml(c.id)}')">
            <div class="conv-avatar-wrap">
                <div class="avatar-circle">${avatarContent}</div>
                <span class="conv-online-dot ${c.online ? "online" : ""}"></span>
            </div>
            <div class="conv-info">
                <div class="conv-top-row">
                    <span class="conv-title">${escapeHtml(c.title)}</span>
                    <span class="conv-time">${escapeHtml(formatShortTime(c.last_timestamp))}</span>
                </div>
                <div class="conv-preview">${escapeHtml(c.last_message)}</div>
            </div>
        </div>`;
    }).join("");
}

function selectConversation(convId) {
    activeConvId = convId;
    const conv = allConversations.find(c => c.id === convId);
    if (conv) {
        document.getElementById("active-conv-title").textContent = conv.title;
        document.getElementById("active-conv-sub").textContent = conv.type === "channel"
            ? "TCP Broadcast (:5001) • Shared persistent channel across LabNet VMs"
            : `${conv.subtitle} • Filtered messages from this participant`;
    }
    renderConversations();
    loadHistory(true);
}

function reloadActiveConversation() {
    loadHistory(true);
}

async function loadHistory(reset = true, silentMerge = false) {
    const container = document.getElementById("chat-messages");
    if (!container) return;

    if (reset && !silentMerge) {
        renderedMsgIds.clear();
        oldestCursor = null;
        container.innerHTML = `<div style="text-align:center; padding:30px; color:var(--text-muted);"><i class="fa-solid fa-spinner fa-spin"></i> Loading persistent chat history...</div>`;
    }

    let url = `/api/chat/history?limit=50`;
    if (activeConvId !== "general") {
        url += `&sender_user_id=${encodeURIComponent(activeConvId)}`;
    }
    if (!reset && !silentMerge && oldestCursor) {
        url += `&before=${encodeURIComponent(oldestCursor)}`;
    }

    try {
        const resp = await fetch(url);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();

        updateBridgePill(Boolean(socket && socket.connected), Boolean(data.tcp_bridge_connected));
        const messages = data.messages || [];

        if (silentMerge) {
            messages.forEach(m => appendMessageIfNew(m, true));
            return;
        }

        hasMoreHistory = Boolean(data.has_more);
        if (data.oldest_cursor) {
            oldestCursor = data.oldest_cursor;
        }

        if (reset) {
            container.innerHTML = "";
            if (!messages.length) {
                container.innerHTML = `
                <div id="chat-empty-state" style="margin:auto; text-align:center; color:var(--text-muted); max-width:340px;">
                    <i class="fa-regular fa-comments" style="font-size:32px; color:var(--border-strong); margin-bottom:10px;"></i>
                    <div style="font-weight:600; color:var(--text-primary); margin-bottom:4px;">No messages yet</div>
                    <div style="font-size:12.5px;">Send a message below to persist it in Supabase and stream it live over TCP port 5001.</div>
                </div>`;
                return;
            }

            if (hasMoreHistory) {
                insertLoadOlderButton(container);
            }
            messages.forEach(m => appendMessageIfNew(m, false));
            container.scrollTop = container.scrollHeight;
            setDeliveryStatus(`Loaded ${messages.length} persisted message(s)`);
        } else {
            // Prepend older paginated messages above current items
            const oldBtn = document.getElementById("btn-load-older-wrap");
            if (oldBtn) oldBtn.remove();

            const prevHeight = container.scrollHeight;
            const frag = document.createDocumentFragment();
            if (hasMoreHistory) {
                const btnWrap = createLoadOlderElement();
                frag.appendChild(btnWrap);
            }
            messages.forEach(m => {
                if (m.id && renderedMsgIds.has(m.id)) return;
                if (m.id) renderedMsgIds.add(m.id);
                frag.appendChild(buildMessageElement(m));
            });
            container.insertBefore(frag, container.firstChild);
            container.scrollTop = container.scrollHeight - prevHeight;
        }
    } catch (err) {
        if (reset && !silentMerge) {
            container.innerHTML = `<div class="alert alert-danger" style="margin:20px;">Failed to load chat history: ${escapeHtml(err.message)}</div>`;
        }
    }
}

function createLoadOlderElement() {
    const div = document.createElement("div");
    div.id = "btn-load-older-wrap";
    div.className = "load-older-wrap";
    div.innerHTML = `<button type="button" class="btn btn-secondary btn-sm" onclick="loadHistory(false, false)"><i class="fa-solid fa-clock-rotate-left"></i> Load Older Messages</button>`;
    return div;
}

function insertLoadOlderButton(container) {
    container.insertBefore(createLoadOlderElement(), container.firstChild);
}

function appendMessageIfNew(msg, shouldScroll) {
    if (!msg) return;
    if (msg.id && renderedMsgIds.has(msg.id)) {
        updateMessageStatusBadge(msg.id, msg.status);
        return;
    }
    if (msg.id) renderedMsgIds.add(msg.id);

    const container = document.getElementById("chat-messages");
    if (!container) return;

    const emptyEl = document.getElementById("chat-empty-state");
    if (emptyEl) emptyEl.remove();

    const el = buildMessageElement(msg);
    container.appendChild(el);
    if (shouldScroll) {
        container.scrollTop = container.scrollHeight;
    }
}

function renderStatusIcon(status) {
    if (status === "failed") {
        return `<span style="color:#fecaca; font-weight:600;"><i class="fa-solid fa-circle-exclamation"></i> Failed</span>`;
    }
    if (status === "sent") {
        return `<i class="fa-solid fa-check"></i> Sent`;
    }
    return `<i class="fa-solid fa-check-double"></i> Delivered`;
}

function updateMessageStatusBadge(msgId, status) {
    if (!msgId || !status) return;
    const el = document.querySelector(`[data-msg-status="${msgId}"]`);
    if (el) {
        el.innerHTML = renderStatusIcon(status);
    }
}

function buildMessageElement(msg) {
    const isSelf = CURRENT_USER.id && msg.sender_user_id === CURRENT_USER.id;
    const row = document.createElement("div");
    row.className = `msg-row ${isSelf ? "outgoing" : "incoming"}`;
    if (msg.id) row.setAttribute("data-msg-id", msg.id);

    const senderName = msg.sender || "LabNet User";
    const roleLabel = msg.sender_role ? `<span class="badge badge-${escapeHtml(msg.sender_role)}" style="font-size:10px; padding:1px 6px;">${escapeHtml(msg.sender_role)}</span>` : "";
    const ipLabel = msg.device_ip ? `<span class="mono" style="font-size:10.5px;">${escapeHtml(msg.device_ip)}</span>` : "";
    const timeLabel = formatShortTime(msg.timestamp || msg.sent_at);

    row.innerHTML = `
        <div class="avatar-circle" style="width:32px; height:32px; font-size:12px;">${escapeHtml(getInitials(senderName))}</div>
        <div style="min-width: 0;">
            <div class="msg-meta">
                <span class="msg-sender">${escapeHtml(senderName)}</span>
                ${roleLabel}
                ${ipLabel}
                <span>${escapeHtml(timeLabel)}</span>
            </div>
            <div class="msg-bubble">
                <div>${escapeHtml(msg.text)}</div>
                <div class="msg-footer">
                    <span class="mono" style="font-size:10px;">${escapeHtml(msg.protocol || "TCP")}</span>
                    ${isSelf ? `<span data-msg-status="${escapeHtml(msg.id || "")}">${renderStatusIcon(msg.status)}</span>` : ""}
                </div>
            </div>
        </div>
    `;
    return row;
}

function handleChatFormSubmit(e) {
    e.preventDefault();
    const input = document.getElementById("chat-text-input");
    const text = (input.value || "").trim();
    if (!text) return;

    if (socket && socket.connected) {
        socket.emit("chat_send", { text: text });
        input.value = "";
        setDeliveryStatus('<i class="fa-solid fa-spinner fa-spin"></i> Persisting &amp; transmitting via TCP :5001...');
    } else {
        // Fallback to authenticated HTTP POST so message still persists even if WebSocket is reconnecting
        fetch("/api/chat/send", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: text })
        })
        .then(r => r.json())
        .then(data => {
            if (data.message) {
                appendMessageIfNew(data.message, true);
            }
            if (data.status === "delivered") {
                setDeliveryStatus('<span style="color:var(--success);"><i class="fa-solid fa-check-double"></i> Delivered via TCP :5001</span>');
            } else {
                setDeliveryStatus(`<span style="color:var(--danger);">${escapeHtml(data.error || "Saved offline; live TCP failed")}</span>`);
            }
            input.value = "";
            loadConversations();
        })
        .catch(err => {
            setDeliveryStatus(`<span style="color:var(--danger);">Transmission error: ${escapeHtml(err.message)}</span>`);
        });
    }
}
