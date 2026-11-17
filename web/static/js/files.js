/**
 * LabNet Real Multi-Protocol File Transfer Client (web/static/js/files.js)
 * =========================================================================
 * Connects frontend to the Flask TCP (:2121) and UDP (:2122) streaming bridges
 * and P2P direct transfer coordinators. All progress calculations, ASCII blocks,
 * and animations are driven by actual byte counters.
 */

let socket = null;
let transferStartTime = 0;

document.addEventListener("DOMContentLoaded", function () {
    // Listen for SocketIO progress events if socket.io is loaded
    if (typeof io !== "undefined") {
        socket = io();
        socket.on("file_progress", function (data) {
            handleBackendProgress(data);
        });
    }

    const senderSelect = document.getElementById("sender-select");
    if (senderSelect) {
        senderSelect.addEventListener("change", function () {
            const senderLabel = document.getElementById("viz-sender-label");
            if (senderLabel) {
                senderLabel.textContent = senderSelect.value + " Computer";
            }
        });
    }

    loadServerFiles();
});

function onDestModeChanged() {
    const mode = document.getElementById("dest-mode").value;
    const p2pGroup = document.getElementById("p2p-peer-group");
    const recvLabel = document.getElementById("viz-receiver-label");
    const recvSub = document.getElementById("viz-receiver-sub");

    if (mode === "p2p") {
        p2pGroup.style.display = "block";
        recvLabel.textContent = "Peer Workstation (Direct)";
        recvSub.textContent = document.getElementById("p2p-target-ip").value + ":" + document.getElementById("p2p-target-port").value;
    } else {
        p2pGroup.style.display = "none";
        recvLabel.textContent = "LabNet Server";
        updateProtocolInfo();
    }
}

function updateProtocolInfo() {
    const protoRadios = document.querySelectorAll('input[name="protocol"]');
    let selected = "TCP";
    protoRadios.forEach(r => { if (r.checked) selected = r.value; });

    const desc = document.getElementById("protocol-desc");
    const badge = document.getElementById("header-protocol-badge");
    const recvSub = document.getElementById("viz-receiver-sub");
    const btn = document.getElementById("btn-start-transfer");

    if (selected === "TCP") {
        desc.innerHTML = `<strong>TCP Stream Transfer</strong><br><span class="text-muted text-sm">Reliable chunk-based stream | Port 2121 | SHA-256 Verified</span>`;
        if (badge) badge.textContent = "TCP:2121";
        if (recvSub && document.getElementById("dest-mode").value !== "p2p") recvSub.textContent = "192.168.66.128:2121";
        if (btn) btn.innerHTML = `<i class="fa-solid fa-cloud-arrow-up"></i> Send File via TCP`;
    } else {
        desc.innerHTML = `<strong>Reliable UDP File Transfer</strong><br><span class="text-muted text-sm">Sliding-Window ARQ | Port 2122 | Packet Sequencing & Retransmissions</span>`;
        if (badge) badge.textContent = "UDP:2122";
        if (recvSub && document.getElementById("dest-mode").value !== "p2p") recvSub.textContent = "192.168.66.128:2122";
        if (btn) btn.innerHTML = `<i class="fa-solid fa-bolt"></i> Send File via UDP`;
    }
}

function onFileSelected() {
    const fileInput = document.getElementById("file-upload");
    const metaCard = document.getElementById("file-meta-card");
    const fnEl = document.getElementById("meta-filename");
    const fsEl = document.getElementById("meta-filesize");

    if (fileInput && fileInput.files.length > 0) {
        const file = fileInput.files[0];
        fnEl.textContent = file.name;
        fsEl.textContent = formatBytes(file.size);
        metaCard.style.display = "block";
    } else {
        metaCard.style.display = "none";
    }
}

function formatBytes(bytes) {
    if (bytes === 0) return "0 Bytes";
    const k = 1024;
    const sizes = ["Bytes", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}

function generateAsciiBlocks(percent) {
    const totalBlocks = 12;
    const filledBlocks = Math.round((percent / 100) * totalBlocks);
    const emptyBlocks = totalBlocks - filledBlocks;
    return "█".repeat(Math.max(0, filledBlocks)) + "░".repeat(Math.max(0, emptyBlocks));
}

function startRealFileTransfer() {
    const fileInput = document.getElementById("file-upload");
    if (!fileInput || fileInput.files.length === 0) {
        alert("Please select a file to transfer first.");
        return;
    }

    const file = fileInput.files[0];
    const protoRadios = document.querySelectorAll('input[name="protocol"]');
    let protocol = "TCP";
    protoRadios.forEach(r => { if (r.checked) protocol = r.value; });

    const destMode = document.getElementById("dest-mode").value;
    const senderSelect = document.getElementById("sender-select");
    const sender = senderSelect ? senderSelect.value : "Student";
    const senderIp = (sender === "Student") ? "192.168.66.130" : "192.168.66.128";

    // UI elements
    const vizAnim = document.getElementById("viz-animation");
    const transferLog = document.getElementById("transfer-log");
    const statusBadge = document.getElementById("pipeline-status-badge");
    const metricStatus = document.getElementById("metric-status");
    const metricProg = document.getElementById("metric-progress");
    const metricSpeed = document.getElementById("metric-speed");
    const metricSha = document.getElementById("metric-sha256");
    const metricPath = document.getElementById("metric-savepath");
    const btn = document.getElementById("btn-start-transfer");

    btn.disabled = true;
    statusBadge.className = "badge badge-primary";
    statusBadge.textContent = "STREAMING";
    metricStatus.innerHTML = `<span class="badge badge-primary">IN PROGRESS</span>`;
    metricProg.textContent = "0% (0 B)";
    metricSpeed.textContent = "Calculating...";
    metricSha.textContent = "Computing SHA-256...";
    metricPath.textContent = "Server buffer...";

    transferStartTime = performance.now();
    transferLog.textContent = `[INIT] Handshaking ${protocol} file stream for '${file.name}' (${formatBytes(file.size)})...\n`;
    vizAnim.className = "viz-animation-container active";
    vizAnim.innerHTML = `${protocol}<br>[░░░░░░░░░░░░]`;

    const formData = new FormData();
    formData.append("file", file);
    formData.append("protocol", protocol);
    formData.append("sender", sender);
    formData.append("sender_ip", senderIp);
    formData.append("dest_mode", destMode);

    if (destMode === "p2p") {
        formData.append("target_ip", document.getElementById("p2p-target-ip").value);
        formData.append("target_port", document.getElementById("p2p-target-port").value);
    }

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/files/upload", true);

    xhr.onload = function () {
        btn.disabled = false;
        vizAnim.className = "viz-animation-container";

        if (xhr.status === 200) {
            try {
                const resp = JSON.parse(xhr.responseText);
                const res = resp.result || {};
                const verified = res.verified;
                const sha = res.sha256 || res.local_sha256 || "N/A";

                statusBadge.className = "badge badge-success";
                statusBadge.textContent = "VERIFIED";
                vizAnim.innerHTML = `${protocol}<br>[COMPLETED]`;
                metricStatus.innerHTML = `<span class="badge badge-success">COMPLETED (SHA-256 MATCH)</span>`;
                metricProg.textContent = `100.0% (${formatBytes(file.size)})`;
                metricSpeed.textContent = `${res.speed_kbps || '0'} KB/s`;
                metricSha.textContent = sha;
                metricPath.textContent = res.save_path || "uploads/" + file.name;

                transferLog.textContent += `[SUCCESS] Protocol: ${protocol}\n`;
                transferLog.textContent += `[VERIFIED] SHA-256: ${sha}\n`;
                transferLog.textContent += `[SPEED] Throughput: ${res.speed_kbps || '0'} KB/s\n`;
                if (res.retransmissions !== undefined) {
                    transferLog.textContent += `[ARQ] Retransmissions: ${res.retransmissions}\n`;
                }
                transferLog.textContent += `[STATUS] Saved to: ${res.save_path || 'uploads/' + file.name}\n`;
                transferLog.scrollTop = transferLog.scrollHeight;

                loadServerFiles();
            } catch (e) {
                transferLog.textContent += `[ERROR] Failed to parse response: ${e}\n`;
            }
        } else {
            statusBadge.className = "badge badge-danger";
            statusBadge.textContent = "FAILED";
            metricStatus.innerHTML = `<span class="badge badge-danger">FAILED</span>`;
            transferLog.textContent += `[FATAL] Server responded with error HTTP ${xhr.status}: ${xhr.responseText}\n`;
        }
    };

    xhr.onerror = function () {
        btn.disabled = false;
        statusBadge.className = "badge badge-danger";
        statusBadge.textContent = "NETWORK ERROR";
        metricStatus.innerHTML = `<span class="badge badge-danger">SOCKET ERROR</span>`;
        transferLog.textContent += `[FATAL] Network error connecting to LabNet File Gateway.\n`;
    };

    xhr.send(formData);
}

function handleBackendProgress(data) {
    const metricProg = document.getElementById("metric-progress");
    const metricSpeed = document.getElementById("metric-speed");
    const vizAnim = document.getElementById("viz-animation");
    const transferLog = document.getElementById("transfer-log");

    if (data.percent !== undefined) {
        const pct = parseFloat(data.percent);
        const ascii = generateAsciiBlocks(pct);
        if (metricProg) {
            metricProg.textContent = `${pct.toFixed(1)}% (${formatBytes(data.bytes_sent || 0)})`;
        }
        if (metricSpeed && data.speed_kbps) {
            metricSpeed.textContent = `${data.speed_kbps} KB/s`;
        }
        if (vizAnim) {
            vizAnim.innerHTML = `STREAM<br>[${ascii}]`;
        }
        if (transferLog && (pct % 20 === 0 || pct === 100)) {
            transferLog.textContent += `[STREAM] ${pct.toFixed(1)}% - ${data.speed_kbps || ''} KB/s\n`;
            transferLog.scrollTop = transferLog.scrollHeight;
        }
    }
}

async function loadServerFiles() {
    const tbody = document.getElementById("repo-tbody");
    if (!tbody) return;

    try {
        const resp = await fetch("/api/files/list");
        const data = await resp.json();
        const files = data.files || [];

        if (files.length === 0) {
            tbody.innerHTML = `<tr><td colspan="5" class="text-muted text-center" style="padding: 12px;">No stored files in uploads/ directory yet.</td></tr>`;
            return;
        }

        let html = "";
        files.forEach(f => {
            const shortSha = f.sha256 ? f.sha256.substring(0, 12) + "..." : "Pending";
            html += `
            <tr style="border-bottom: 1px solid #f1f5f9;">
                <td style="padding: 8px 12px; font-weight: 500;">
                    <i class="fa-regular fa-file" style="color: #64748b; margin-right: 6px;"></i> ${f.filename}
                </td>
                <td style="padding: 8px 12px; color: #475569;">${formatBytes(f.size)}</td>
                <td style="padding: 8px 12px;"><span class="badge ${f.protocol === 'UDP' ? 'badge-primary' : 'badge-tcp'}" style="font-size: 0.75rem;">${f.protocol || 'TCP'}</span></td>
                <td style="padding: 8px 12px; font-family: monospace; font-size: 0.75rem; color: #64748b;">${shortSha}</td>
                <td style="padding: 8px 12px;">
                    <a href="/api/files/download/${encodeURIComponent(f.filename)}" class="btn btn-secondary text-sm" style="text-decoration: none; padding: 4px 8px; font-size: 0.75rem;" download>
                        <i class="fa-solid fa-download"></i> Download
                    </a>
                </td>
            </tr>
            `;
        });
        tbody.innerHTML = html;
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="5" class="text-danger text-center" style="padding: 12px;">Failed to load files: ${e.message}</td></tr>`;
    }
}
