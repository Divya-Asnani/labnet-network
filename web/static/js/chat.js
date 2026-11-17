/**
 * LabNet Private Isolated Chat Client (web/static/js/chat.js)
 * ===========================================================
 * Architecture:
 *  - Every conversation has a unique UUID (`conversation_id`) backed by
 *    Supabase `conversations` + `conversation_participants` + `chat_messages`.
 *  - Selecting a conversation clears the previous panel, authorizes and joins
 *    ONLY Socket.IO room `conversation:<conversation_id>`, and fetches ONLY
 *    `GET /api/chat/conversations/<conversation_id>/messages`.
 *  - Messages arriving on `chat_message` are verified against `activeConvId`
 *    and deduplicated by canonical DB UUID (`renderedMsgIds`).
 */

let socket = null;
let renderedMsgIds = new Set();
let userConversations = [];
let availableContacts = [];
let unreadCounts = {};  // {conversation_id: int}
let activeConvId = null;
let convFilter = "all";
let oldestCursor = null;
let hasMoreHistory = false;

const CURRENT_USER = window.LABNET_USER || {};

document.addEventListener("DOMContentLoaded", function () {
    initChatSocket();
    loadConversationsList(true);
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

function setComposerEnabled(enabled, targetTitle) {
    const input = document.getElementById("chat-text-input");
    const sendBtn = document.getElementById("chat-send-btn");
    const reloadBtn = document.getElementById("btn-reload-conv");
    const badge = document.getElementById("room-isolation-badge");
    if (input) {
        input.disabled = !enabled;
        input.placeholder = enabled
            ? `Message ${targetTitle || "conversation"} (private room • TCP :5001)...`
            : "Select a conversation first...";
    }
    if (sendBtn) sendBtn.disabled = !enabled;
    if (reloadBtn) reloadBtn.disabled = !enabled;
    if (badge) badge.style.display = enabled ? "inline-flex" : "none";
}

function initChatSocket() {
    socket = io({
        reconnection: true,
        reconnectionDelay: 1500,
        reconnectionAttempts: 30
    });

    socket.on("connect", function () {
        // Re-join ONLY the currently selected conversation room upon reconnect
        if (activeConvId) {
            socket.emit("join_conversation", { conversation_id: activeConvId });
            loadConversationHistory(activeConvId, false, true);
        }
    });

    socket.on("disconnect", function () {
        updateBridgePill(false, false);
        setDeliveryStatus('<span style="color: var(--danger);">WebSocket disconnected — retrying...</span>');
    });

    socket.on("connection_status", function (data) {
        updateBridgePill(true, Boolean(data.tcp_bridge_connected));
    });

    socket.on("presence_update", function (evt) {
        const strip = document.getElementById("presence-status-text");
        if (strip && evt.active_clients !== undefined && evt.active_clients !== null) {
            strip.innerHTML = `<i class="fa-solid fa-circle-nodes" style="color: var(--accent); margin-right: 4px;"></i>TCP Chat Server sockets: <strong>${evt.active_clients}</strong> • Updated ${escapeHtml(formatShortTime(evt.timestamp))}`;
        }
    });

    socket.on("joined_conversation", function (data) {
        if (data && data.conversation_id === activeConvId) {
            setDeliveryStatus(`<span style="color:var(--success);"><i class="fa-solid fa-lock"></i> Joined private room</span>`);
        }
    });

    socket.on("conversation_error", function (err) {
        setDeliveryStatus(`<span style="color:var(--danger);"><i class="fa-solid fa-ban"></i> ${escapeHtml(err.error || "Unauthorized room")}</span>`);
    });

    // Strictly isolated real-time message event (delivered ONLY to `conversation:<id>` room)
    socket.on("chat_message", function (msg) {
        if (!msg || !msg.conversation_id) return;
        // Strict defense-in-depth check: only render into active panel if conversation_id matches
        if (msg.conversation_id !== activeConvId) {
            return;
        }
        appendMessageIfNew(msg, true);
    });

    // Personal sidebar notification (delivered ONLY to authorized participants via `user:<uid>`)
    socket.on("conversation_updated", function (evt) {
        if (!evt || !evt.conversation_id) return;
        if (evt.conversation_id !== activeConvId && evt.sender_user_id !== CURRENT_USER.id) {
            unreadCounts[evt.conversation_id] = (unreadCounts[evt.conversation_id] || 0) + 1;
        }
        loadConversationsList(false);
    });

    socket.on("chat_ack", function (ack) {
        if (ack.conversation_id === activeConvId && ack.message) {
            appendMessageIfNew(ack.message, true);
            updateMessageStatusBadge(ack.id, "delivered");
        }
        setDeliveryStatus(`<span style="color: var(--success);"><i class="fa-solid fa-check-double"></i> Delivered via TCP :5001 (${escapeHtml(formatShortTime(ack.timestamp))})</span>`);
        loadConversationsList(false);
    });

    socket.on("chat_error", function (err) {
        if (err.conversation_id === activeConvId && err.message) {
            appendMessageIfNew(err.message, true);
            updateMessageStatusBadge(err.id, "failed");
        }
        setDeliveryStatus(`<span style="color: var(--danger);"><i class="fa-solid fa-triangle-exclamation"></i> ${escapeHtml(err.error || "Delivery failed")}</span>`);
    });
}

async function loadConversationsList(autoSelectFirst = false) {
    try {
        const resp = await fetch("/api/chat/conversations");
        if (!resp.ok) return;
        const data = await resp.json();
        userConversations = data.conversations || [];
        availableContacts = data.contacts || [];
        updateBridgePill(Boolean(socket && socket.connected), Boolean(data.tcp_bridge_connected));
        renderSidebarLists();

        if (autoSelectFirst && !activeConvId && userConversations.length > 0) {
            selectConversation(userConversations[0].id);
        }
    } catch (e) {
        console.warn("Failed to load conversations:", e);
    }
}

function setConvFilter(mode) {
    convFilter = mode;
    document.getElementById("filter-all").classList.toggle("active", mode === "all");
    document.getElementById("filter-unread").classList.toggle("active", mode === "unread");
    renderSidebarLists();
}

function renderSidebarLists() {
    const listEl = document.getElementById("conv-list");
    if (!listEl) return;

    const q = (document.getElementById("conv-search-input").value || "").toLowerCase();

    const filteredConvs = userConversations.filter(c => {
        if (convFilter === "unread" && !(unreadCounts[c.id] > 0)) return false;
        if (!q) return true;
        return (c.title || "").toLowerCase().includes(q) || (c.last_message || "").toLowerCase().includes(q);
    });

    // Exclude contacts who already have an existing direct conversation in userConversations
    const existingPeerIds = new Set(
        userConversations
            .filter(c => c.other_user && c.other_user.user_id)
            .map(c => c.other_user.user_id)
    );
    const newContacts = availableContacts.filter(ct => {
        if (existingPeerIds.has(ct.user_id)) return false;
        if (convFilter === "unread") return false;
        if (!q) return true;
        return (ct.display_name || "").toLowerCase().includes(q);
    });

    let html = "";

    if (filteredConvs.length) {
        html += `<div class="nav-section-label" style="padding: 6px 8px 4px;">Private Conversations</div>`;
        html += filteredConvs.map(c => {
            const isActive = c.id === activeConvId;
            const unread = unreadCounts[c.id] || 0;
            return `
            <div class="conv-item ${isActive ? "active" : ""}" onclick="selectConversation('${escapeHtml(c.id)}')">
                <div class="conv-avatar-wrap">
                    <div class="avatar-circle">${escapeHtml(getInitials(c.title))}</div>
                    <span class="conv-online-dot ${c.online ? "online" : ""}"></span>
                </div>
                <div class="conv-info">
                    <div class="conv-top-row">
                        <span class="conv-title">${escapeHtml(c.title)}</span>
                        <span class="conv-time">${escapeHtml(formatShortTime(c.last_timestamp))}</span>
                    </div>
                    <div style="display:flex; justify-content:space-between; align-items:center; gap:6px;">
                        <div class="conv-preview">${escapeHtml(c.last_message)}</div>
                        ${unread > 0 ? `<span class="badge badge-primary" style="font-size:10px; padding:1px 6px;">${unread}</span>` : ""}
                    </div>
                </div>
            </div>`;
        }).join("");
    } else if (convFilter === "unread") {
        html += `<div style="padding: 18px; text-align: center; color: var(--text-muted); font-size: 12.5px;">No unread messages.</div>`;
    }

    if (newContacts.length) {
        html += `<div class="nav-section-label" style="padding: 12px 8px 4px;">Start New Direct Chat</div>`;
        html += newContacts.map(ct => `
            <div class="conv-item" onclick="startDirectConversation('${escapeHtml(ct.user_id)}')">
                <div class="conv-avatar-wrap">
                    <div class="avatar-circle" style="background: var(--surface-subtle); color: var(--text-secondary); border-color: var(--border);">${escapeHtml(getInitials(ct.display_name))}</div>
                    <span class="conv-online-dot ${ct.online ? "online" : ""}"></span>
                </div>
                <div class="conv-info">
                    <div class="conv-top-row">
                        <span class="conv-title">${escapeHtml(ct.display_name)}</span>
                        <span class="badge badge-${escapeHtml(ct.role)}" style="font-size:10px; padding:1px 6px;">${escapeHtml(ct.role)}</span>
                    </div>
                    <div class="conv-preview"><i class="fa-solid fa-plus" style="font-size:10px;"></i> Start private chat</div>
                </div>
            </div>
        `).join("");
    }

    if (!html) {
        html = `<div style="padding: 20px; text-align: center; color: var(--text-muted); font-size: 12.5px;">No other registered lab users found yet. Register a second user to start a private conversation.</div>`;
    }

    listEl.innerHTML = html;
}

async function startDirectConversation(recipientUserId) {
    try {
        setDeliveryStatus('<i class="fa-solid fa-spinner fa-spin"></i> Opening private conversation...');
        const resp = await fetch("/api/chat/conversations/direct", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ recipient_user_id: recipientUserId })
        });
        const data = await resp.json();
        if (!resp.ok) {
            setDeliveryStatus(`<span style="color:var(--danger);">${escapeHtml(data.error || "Could not create conversation")}</span>`);
            return;
        }
        const conv = data.conversation;
        await loadConversationsList(false);
        if (conv && conv.id) {
            selectConversation(conv.id);
        }
    } catch (e) {
        setDeliveryStatus(`<span style="color:var(--danger);">Error: ${escapeHtml(e.message)}</span>`);
    }
}

function selectConversation(convId) {
    if (!convId) return;
    const prevConvId = activeConvId;

    // 1. Leave previous conversation room explicitly
    if (prevConvId && prevConvId !== convId && socket && socket.connected) {
        socket.emit("leave_conversation", { conversation_id: prevConvId });
    }

    activeConvId = convId;
    unreadCounts[convId] = 0;

    // 2. Immediately clear previous conversation's rendered messages (zero cross-conversation bleed)
    renderedMsgIds.clear();
    oldestCursor = null;
    hasMoreHistory = false;

    const conv = userConversations.find(c => c.id === convId);
    const title = conv ? conv.title : "Private Conversation";
    const sub = conv
        ? `${conv.subtitle} • Isolated Room ${convId.slice(0, 8)}`
        : `Private Conversation • ${convId.slice(0, 8)}`;

    document.getElementById("active-conv-title").textContent = title;
    document.getElementById("active-conv-sub").textContent = sub;
    setComposerEnabled(true, title);
    renderSidebarLists();

    // 3. Join ONLY the newly selected authorized Socket.IO conversation room
    if (socket && socket.connected) {
        socket.emit("join_conversation", { conversation_id: convId });
    }

    // 4. Fetch ONLY this conversation's message history from Supabase
    loadConversationHistory(convId, true, false);
}

function reloadActiveConversation() {
    if (activeConvId) {
        loadConversationHistory(activeConvId, true, false);
    }
}

async function loadConversationHistory(convId, reset = true, silentMerge = false) {
    if (!convId) return;
    const container = document.getElementById("chat-messages");
    if (!container) return;

    if (reset && !silentMerge) {
        renderedMsgIds.clear();
        oldestCursor = null;
        container.innerHTML = `<div style="text-align:center; padding:30px; color:var(--text-muted);"><i class="fa-solid fa-spinner fa-spin"></i> Loading conversation history...</div>`;
    }

    let url = `/api/chat/conversations/${encodeURIComponent(convId)}/messages?limit=50`;
    if (!reset && !silentMerge && oldestCursor) {
        url += `&before=${encodeURIComponent(oldestCursor)}`;
    }

    try {
        const resp = await fetch(url);
        const data = await resp.json();

        // Guard against stale async fetch completing after user already switched conversations!
        if (convId !== activeConvId) return;

        if (!resp.ok) {
            container.innerHTML = `<div class="alert alert-danger" style="margin:20px;">${escapeHtml(data.error || "Unauthorized or failed to load history.")}</div>`;
            return;
        }

        updateBridgePill(Boolean(socket && socket.connected), Boolean(data.tcp_bridge_connected));
        const messages = data.messages || [];

        if (silentMerge) {
            messages.forEach(m => appendMessageIfNew(m, true));
            return;
        }

        hasMoreHistory = Boolean(data.has_more);
        if (data.oldest_cursor) oldestCursor = data.oldest_cursor;

        if (reset) {
            container.innerHTML = "";
            if (!messages.length) {
                container.innerHTML = `
                <div id="chat-empty-state" style="margin:auto; text-align:center; color:var(--text-muted); max-width:340px;">
                    <i class="fa-regular fa-comments" style="font-size:30px; color:var(--border-strong); margin-bottom:10px;"></i>
                    <div style="font-weight:600; color:var(--text-primary); margin-bottom:4px;">Start this private conversation</div>
                    <div style="font-size:12.5px;">Messages sent here are stored exclusively in this conversation and routed only to its participants.</div>
                </div>`;
                setDeliveryStatus("0 messages in this conversation");
                return;
            }

            if (hasMoreHistory) {
                container.insertBefore(createLoadOlderElement(convId), container.firstChild);
            }
            messages.forEach(m => appendMessageIfNew(m, false));
            container.scrollTop = container.scrollHeight;
            setDeliveryStatus(`Loaded ${messages.length} private message(s)`);
        } else {
            const oldBtn = document.getElementById("btn-load-older-wrap");
            if (oldBtn) oldBtn.remove();

            const prevHeight = container.scrollHeight;
            const frag = document.createDocumentFragment();
            if (hasMoreHistory) {
                frag.appendChild(createLoadOlderElement(convId));
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
        if (convId === activeConvId && reset && !silentMerge) {
            container.innerHTML = `<div class="alert alert-danger" style="margin:20px;">Failed to load messages: ${escapeHtml(err.message)}</div>`;
        }
    }
}

function createLoadOlderElement(convId) {
    const div = document.createElement("div");
    div.id = "btn-load-older-wrap";
    div.className = "load-older-wrap";
    div.innerHTML = `<button type="button" class="btn btn-secondary btn-sm" onclick="loadConversationHistory('${escapeHtml(convId)}', false, false)"><i class="fa-solid fa-clock-rotate-left"></i> Load Older Messages</button>`;
    return div;
}

function appendMessageIfNew(msg, shouldScroll) {
    if (!msg) return;
    if (msg.conversation_id && msg.conversation_id !== activeConvId) return;
    if (msg.id && renderedMsgIds.has(msg.id)) {
        updateMessageStatusBadge(msg.id, msg.status);
        return;
    }
    if (msg.id) renderedMsgIds.add(msg.id);

    const container = document.getElementById("chat-messages");
    if (!container) return;

    const emptyEl = document.getElementById("chat-empty-state");
    if (emptyEl) emptyEl.remove();

    container.appendChild(buildMessageElement(msg));
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
    if (el) el.innerHTML = renderStatusIcon(status);
}

function buildMessageElement(msg) {
    const isSelf = CURRENT_USER.id && msg.sender_user_id === CURRENT_USER.id;
    const row = document.createElement("div");
    row.className = `msg-row ${isSelf ? "outgoing" : "incoming"}`;
    if (msg.id) row.setAttribute("data-msg-id", msg.id);

    const senderName = msg.sender || "LabNet User";
    const roleLabel = msg.sender_role
        ? `<span class="badge badge-${escapeHtml(msg.sender_role)}" style="font-size:10px; padding:1px 6px;">${escapeHtml(msg.sender_role)}</span>`
        : "";
    const timeLabel = formatShortTime(msg.timestamp || msg.sent_at);

    row.innerHTML = `
        <div class="avatar-circle" style="width:32px; height:32px; font-size:12px;">${escapeHtml(getInitials(senderName))}</div>
        <div style="min-width: 0;">
            <div class="msg-meta">
                <span class="msg-sender">${escapeHtml(senderName)}</span>
                ${roleLabel}
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
    if (!activeConvId) {
        setDeliveryStatus('<span style="color:var(--warning);">Select a conversation before sending.</span>');
        return;
    }

    const input = document.getElementById("chat-text-input");
    const text = (input.value || "").trim();
    if (!text) return;

    if (socket && socket.connected) {
        socket.emit("chat_send", {
            conversation_id: activeConvId,
            text: text
        });
        input.value = "";
        setDeliveryStatus('<i class="fa-solid fa-spinner fa-spin"></i> Persisting &amp; transmitting to private room...');
    } else {
        fetch(`/api/chat/conversations/${encodeURIComponent(activeConvId)}/messages`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: text })
        })
        .then(r => r.json())
        .then(data => {
            if (data.message && data.message.conversation_id === activeConvId) {
                appendMessageIfNew(data.message, true);
            }
            if (data.status === "delivered") {
                setDeliveryStatus('<span style="color:var(--success);"><i class="fa-solid fa-check-double"></i> Delivered via TCP :5001</span>');
            } else {
                setDeliveryStatus(`<span style="color:var(--danger);">${escapeHtml(data.error || "Saved offline; live TCP failed")}</span>`);
            }
            input.value = "";
            loadConversationsList(false);
        })
        .catch(err => {
            setDeliveryStatus(`<span style="color:var(--danger);">Transmission error: ${escapeHtml(err.message)}</span>`);
        });
    }
}
