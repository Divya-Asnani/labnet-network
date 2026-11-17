/**
 * LabNet Real Multi-Protocol File Transfer Client (web/static/js/files.js)
 * =========================================================================
 * Connects the redesigned File Transfer page to genuine backend implementations:
 *  - TCP Stream (:2121)
 *  - Reliable Sliding-Window UDP ARQ (:2122)
 *  - Direct Peer-to-Peer (:2123)
 * All progress bars and metrics come from real byte streaming events.
 */

let socket = null;
let selectedProtocol = "TCP";
let selectedDestMode = "server";

document.addEventListener("DOMContentLoaded", function () {
    if (typeof io !== "undefined") {
        socket = io();
        socket.on("file_progress", handleBackendProgress);
    }
    loadRegisteredPeers();
});

function formatBytes(bytes) {
    if (!bytes) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}

function selectTransferMode(proto, destMode) {
    selectedProtocol = proto;
    selectedDestMode = destMode;

    document.getElementById("card-proto-tcp").classList.toggle("selected", proto === "TCP" && destMode === "server");
    document.getElementById("card-proto-udp").classList.toggle("selected", proto === "UDP" && destMode === "server");
    document.getElementById("card-proto-p2p").classList.toggle("selected", destMode === "p2p");

    const p2pBox = document.getElementById("p2p-config-box");
    const badge = document.getElementById("active-proto-badge");
    const pathEl = document.getElementById("metric-path");
    const btn = document.getElementById("btn-start-transfer");

    if (destMode === "p2p") {
        p2pBox.style.display = "block";
        badge.className = "badge badge-p2p";
        badge.textContent = "DIRECT P2P :2123";
        const peerIp = document.getElementById("p2p-target-ip").value || "<peer-ip>";
        const peerPort = document.getElementById("p2p-target-port").value || "2123";
        pathEl.textContent = `Workstation → ${peerIp}:${peerPort} (Direct P2P)`;
        btn.innerHTML = `<i class="fa-solid fa-share-nodes"></i><span>Transmit Directly to Peer (:2123)</span>`;
    } else if (proto === "UDP") {
        p2pBox.style.display = "none";
        badge.className = "badge badge-udp";
        badge.textContent = "RELIABLE UDP :2122";
        pathEl.textContent = `Client → ${window.LABNET_SERVER_IP}:2122 (Sliding-Window ARQ)`;
        btn.innerHTML = `<i class="fa-solid fa-bolt"></i><span>Transmit File via Reliable UDP (:2122)</span>`;
    } else {
        p2pBox.style.display = "none";
        badge.className = "badge badge-tcp";
        badge.textContent = "TCP :2121";
        pathEl.textContent = `Client → ${window.LABNET_SERVER_IP}:2121 (TCP Stream)`;
        btn.innerHTML = `<i class="fa-solid fa-paper-plane"></i><span>Transmit File via TCP (:2121)</span>`;
    }
}

async function loadRegisteredPeers() {
    const sel = document.getElementById("p2p-peer-select");
    if (!sel) return;
    try {
        const resp = await fetch("/api/peers/list");
        if (!resp.ok) return;
        const data = await resp.json();
        const peers = data.peers || [];
        sel.innerHTML = `<option value="">— ${peers.length ? "Select a discovered peer" : "No listening peers discovered yet — enter IP manually"} —</option>`;
        peers.forEach(p => {
            const opt = document.createElement("option");
            opt.value = `${p.ip}:${p.port}`;
            opt.textContent = `${p.peer_name} (${p.ip}:${p.port}) — ${p.status}`;
            sel.appendChild(opt);
        });
    } catch (e) {
        console.warn("Peer discovery error:", e);
    }
}

function onPeerSelected() {
    const val = document.getElementById("p2p-peer-select").value;
    if (!val) return;
    const [ip, port] = val.split(":");
    if (ip) document.getElementById("p2p-target-ip").value = ip;
    if (port) document.getElementById("p2p-target-port").value = port;
    selectTransferMode("TCP", "p2p");
}

function onFileSelected() {
    const fileInput = document.getElementById("file-upload");
    const box = document.getElementById("selected-file-summary");
    if (fileInput && fileInput.files.length > 0) {
        const file = fileInput.files[0];
        document.getElementById("meta-filename").textContent = file.name;
        document.getElementById("meta-filesize").textContent = formatBytes(file.size);
        box.style.display = "block";
    } else {
        box.style.display = "none";
    }
}

function startRealFileTransfer() {
    const fileInput = document.getElementById("file-upload");
    if (!fileInput || fileInput.files.length === 0) {
        alert("Please choose a file to transfer first.");
        return;
    }

    const file = fileInput.files[0];
    const btn = document.getElementById("btn-start-transfer");
    const logEl = document.getElementById("transfer-log");
    const statusBadge = document.getElementById("metric-status-badge");
    const barFill = document.getElementById("progress-bar-fill");

    if (selectedDestMode === "p2p") {
        const targetIp = (document.getElementById("p2p-target-ip").value || "").trim();
        if (!targetIp) {
            alert("Please enter or select a destination peer IP address.");
            return;
        }
    }

    btn.disabled = true;
    statusBadge.className = "badge badge-primary";
    statusBadge.textContent = "STREAMING";
    barFill.style.width = "0%";
    document.getElementById("metric-progress").textContent = "0% (0 B)";
    document.getElementById("metric-speed").textContent = "Measuring...";
    document.getElementById("metric-retries").textContent = "0";
    document.getElementById("metric-sha256").textContent = "Computing SHA-256...";
    document.getElementById("metric-savepath").textContent = "In transit...";

    const modeLabel = selectedDestMode === "p2p" ? "DIRECT P2P (:2123)" : `${selectedProtocol} (${selectedProtocol === "UDP" ? ":2122" : ":2121"})`;
    logEl.textContent = `[INIT] Initiating ${modeLabel} transfer for '${file.name}' (${formatBytes(file.size)})...\n`;

    const formData = new FormData();
    formData.append("file", file);
    formData.append("protocol", selectedProtocol);
    formData.append("dest_mode", selectedDestMode);

    if (selectedDestMode === "p2p") {
        formData.append("target_ip", document.getElementById("p2p-target-ip").value.trim());
        formData.append("target_port", document.getElementById("p2p-target-port").value.trim() || "2123");
    }

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/files/upload", true);

    xhr.onload = function () {
        btn.disabled = false;
        let parsed = {};
        try { parsed = JSON.parse(xhr.responseText); } catch (e) {}

        if (xhr.status === 200 && parsed.status === "completed") {
            const res = parsed.result || {};
            const verified = Boolean(res.verified);
            const sha = res.sha256 || res.local_sha256 || "—";

            barFill.style.width = "100%";
            document.getElementById("metric-progress").textContent = `100% (${formatBytes(file.size)})`;
            document.getElementById("metric-speed").textContent = `${res.speed_kbps || 0} KB/s`;
            document.getElementById("metric-retries").textContent = String(res.total_retransmissions ?? res.retransmissions ?? 0);
            document.getElementById("metric-sha256").textContent = sha;
            document.getElementById("metric-savepath").textContent = res.save_path || "uploads/" + file.name;

            if (verified) {
                statusBadge.className = "badge badge-success";
                statusBadge.textContent = "VERIFIED";
                logEl.textContent += `[SUCCESS] Transfer verified via SHA-256 checksum match.\n`;
            } else {
                statusBadge.className = "badge badge-danger";
                statusBadge.textContent = "CHECKSUM MISMATCH";
                logEl.textContent += `[ERROR] SHA-256 verification failed!\n`;
            }
            logEl.textContent += `[SHA-256] ${sha}\n`;
            logEl.textContent += `[SPEED]   ${res.speed_kbps || 0} KB/s\n`;
            if (res.total_retransmissions !== undefined) {
                logEl.textContent += `[ARQ]     Retransmissions: ${res.total_retransmissions}\n`;
            }
            if (res.db_warning) {
                logEl.textContent += `[DB WARN] ${res.db_warning}\n`;
            }
            logEl.scrollTop = logEl.scrollHeight;
        } else {
            statusBadge.className = "badge badge-danger";
            statusBadge.textContent = "FAILED";
            logEl.textContent += `[ERROR HTTP ${xhr.status}] ${parsed.error || xhr.responseText}\n`;
            logEl.scrollTop = logEl.scrollHeight;
        }
    };

    xhr.onerror = function () {
        btn.disabled = false;
        statusBadge.className = "badge badge-danger";
        statusBadge.textContent = "NETWORK ERROR";
        logEl.textContent += `[FATAL] Failed to reach LabNet upload endpoint.\n`;
    };

    xhr.send(formData);
}

function handleBackendProgress(data) {
    if (!data || data.percent === undefined) return;
    const pct = Math.min(100, Math.max(0, parseFloat(data.percent)));
    const barFill = document.getElementById("progress-bar-fill");
    if (barFill) barFill.style.width = pct + "%";

    const progEl = document.getElementById("metric-progress");
    if (progEl) progEl.textContent = `${pct.toFixed(1)}% (${formatBytes(data.bytes_sent || 0)})`;

    if (data.speed_kbps !== undefined) {
        document.getElementById("metric-speed").textContent = `${data.speed_kbps} KB/s`;
    }
    if (data.retries !== undefined) {
        document.getElementById("metric-retries").textContent = String(data.retries);
    }
}
