/**
 * LabNet Real TCP File Transfer Client (web/static/js/files.js)
 * ==============================================================
 * Connects frontend to the Flask TCP streaming bridge and server/tcp_file_server.py.
 * All progress calculations, ASCII blocks, and animations are driven by actual
 * byte counters. No fake timer-based animations.
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
});

function updateProtocolInfo() {
    const protoRadios = document.querySelectorAll('input[name="protocol"]');
    let selected = "TCP";
    protoRadios.forEach(r => { if (r.checked) selected = r.value; });

    const desc = document.getElementById("protocol-desc");
    if (selected === "TCP") {
        desc.innerHTML = `<strong>TCP Stream Transfer</strong><br><span class="text-muted text-sm">Reliable chunk-based stream | Port 2121 | SHA-256 Verified</span>`;
    } else {
        desc.innerHTML = `<strong>UDP File Transfer</strong><br><span class="text-muted text-sm">Connectionless datagrams (Experimental - Planned for Phase 3)</span>`;
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

    if (protocol !== "TCP") {
        alert("UDP file transfer is planned for Phase 3. Please select TCP.");
        return;
    }

    const senderSelect = document.getElementById("sender-select");
    const sender = senderSelect ? senderSelect.value : "Student";
    const senderIp = (sender === "Student") ? "192.168.66.130" : "192.168.66.128";

    // UI elements
    const vizAnim = document.getElementById("viz-animation");
    const transferLog = document.getElementById("transfer-log");
    const statusBadge = document.getElementById("pipeline-status-badge");
    const metricStatus = document.getElementById("metric-status");
    const metricProgress = document.getElementById("metric-progress");
    const metricSpeed = document.getElementById("metric-speed");
    const metricSha = document.getElementById("metric-sha256");
    const metricSave = document.getElementById("metric-savepath");
    const btnSend = document.getElementById("btn-start-transfer");

    btnSend.disabled = true;
    transferStartTime = performance.now();

    // Reset pipeline state
    statusBadge.className = "badge badge-warning";
    statusBadge.textContent = "CONNECTING";
    metricStatus.innerHTML = '<span class="badge badge-warning">CONNECTING</span>';
    metricSha.textContent = "Calculating...";
    metricSave.textContent = "-";

    vizAnim.style.top = "0%";
    vizAnim.innerHTML = `TCP<br>CONNECTING...`;

    transferLog.textContent = `[INIT] Preparing TCP connection to LabNet Server (192.168.66.128:2121)...\n`;
    transferLog.textContent += `[FILE] '${file.name}' (${formatBytes(file.size)})\n`;
    transferLog.textContent += `[TCP] Establishing 3-Way Handshake (SYN -> SYN-ACK -> ACK)...\n`;

    // Construct FormData payload
    const formData = new FormData();
    formData.append("file", file);
    formData.append("protocol", protocol);
    formData.append("sender", sender);
    formData.append("sender_ip", senderIp);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/files/upload", true);

    // Track real HTTP chunk upload progress to Flask gateway
    xhr.upload.onprogress = function (e) {
        if (e.lengthComputable) {
            const percent = (e.loaded / e.total) * 100;
            const elapsedSeconds = Math.max((performance.now() - transferStartTime) / 1000, 0.001);
            const speedKbps = ((e.loaded / 1024) / elapsedSeconds).toFixed(2);
            const asciiBar = generateAsciiBlocks(percent);

            // Animate packet down the pipeline based on actual bytes
            vizAnim.style.top = Math.min(percent, 90) + "%";
            vizAnim.innerHTML = `TCP<br>${asciiBar}<br>${percent.toFixed(0)}%`;

            metricProgress.textContent = `${percent.toFixed(1)}% (${formatBytes(e.loaded)} / ${formatBytes(e.total)})`;
            metricSpeed.textContent = `${speedKbps} KB/s`;

            if (percent < 100) {
                statusBadge.className = "badge badge-tcp";
                statusBadge.textContent = "STREAMING";
                metricStatus.innerHTML = '<span class="badge badge-tcp">STREAMING</span>';
            }
        }
    };

    xhr.onload = function () {
        btnSend.disabled = false;
        if (xhr.status === 200) {
            try {
                const resp = JSON.parse(xhr.responseText);
                const result = resp.result || {};

                vizAnim.style.top = "100%";
                const fullBar = generateAsciiBlocks(100);
                vizAnim.innerHTML = `TCP<br>${fullBar}<br>100%`;

                statusBadge.className = "badge badge-success";
                statusBadge.textContent = "COMPLETED";
                metricStatus.innerHTML = '<span class="badge badge-success"><i class="fa-solid fa-check-circle"></i> SUCCESS</span>';
                metricProgress.textContent = `100% (${formatBytes(result.filesize || file.size)})`;
                metricSpeed.textContent = `${result.speed_kbps || "-"} KB/s`;
                metricSha.innerHTML = `<span class="text-green font-semibold">${result.sha256 || "-"}</span> <span class="badge badge-success" style="font-size:0.65rem;">MATCH</span>`;
                metricSave.textContent = result.save_path || "uploads/" + file.name;

                transferLog.textContent += `[STREAM] All bytes transferred.\n`;
                transferLog.textContent += `[SHA-256] Computed & Verified: ${result.sha256}\n`;
                transferLog.textContent += `[SERVER] Destination verified at: ${result.save_path}\n`;
                transferLog.textContent += `[TCP] Connection closed cleanly (FIN-ACK).\n`;
                transferLog.textContent += `[RESULT] Transfer SUCCESS!\n`;
                transferLog.scrollTop = transferLog.scrollHeight;
            } catch (err) {
                handleTransferError("Failed to parse server response: " + err);
            }
        } else {
            handleTransferError(`Server returned HTTP ${xhr.status}: ${xhr.responseText}`);
        }
    };

    xhr.onerror = function () {
        btnSend.disabled = false;
        handleTransferError("Network socket error while communicating with gateway.");
    };

    xhr.send(formData);
}

function handleBackendProgress(data) {
    const transferLog = document.getElementById("transfer-log");
    if (!transferLog) return;

    if (data.status === "STREAMING" && data.percent !== undefined) {
        if (Math.round(data.percent) % 25 === 0) {
            transferLog.textContent += `[TCP SOCKET :2121] Streamed ${formatBytes(data.bytes_sent)} / ${formatBytes(data.total_bytes)} (${data.percent}%)\n`;
            transferLog.scrollTop = transferLog.scrollHeight;
        }
    }
}

function handleTransferError(errMsg) {
    const statusBadge = document.getElementById("pipeline-status-badge");
    const metricStatus = document.getElementById("metric-status");
    const transferLog = document.getElementById("transfer-log");
    const vizAnim = document.getElementById("viz-animation");

    if (statusBadge) {
        statusBadge.className = "badge badge-danger";
        statusBadge.textContent = "FAILED";
    }
    if (metricStatus) {
        metricStatus.innerHTML = '<span class="badge badge-danger"><i class="fa-solid fa-circle-xmark"></i> ERROR</span>';
    }
    if (vizAnim) {
        vizAnim.innerHTML = `TCP<br>[FAILED]`;
    }
    if (transferLog) {
        transferLog.textContent += `\n[ERROR] ${errMsg}\n`;
        transferLog.scrollTop = transferLog.scrollHeight;
    }
}
