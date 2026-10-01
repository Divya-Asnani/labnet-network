
function updateProtocolInfo() {
    const proto = document.querySelector('input[name="protocol"]:checked').value;
    const desc = document.getElementById('protocol-desc');
    if(proto === 'TCP') {
        desc.innerHTML = `<strong>TCP File Transfer</strong><br>Reliable connection<br>Port 2121`;
    } else {
        desc.innerHTML = `<strong>UDP File Transfer</strong><br>Connectionless<br>Port 6000`;
    }
}

function startFileTransfer() {
    const fileInput = document.getElementById('file-upload');
    const file = fileInput.files[0];
    if(!file) {
        alert("Please select a file first");
        return;
    }
    
    const protocol = document.querySelector('input[name="protocol"]:checked').value;
    const vizAnim = document.getElementById('viz-animation');
    const transferLog = document.getElementById('transfer-log');
    
    // Reset viz
    vizAnim.style.top = '0%';
    vizAnim.innerHTML = 'TCP\n[ ]';
    transferLog.innerHTML = 'Connecting...\nTCP handshake initiated...\n';
    
    let progress = 0;
    
    // Visual Animation Loop (simulate network delay)
    const animInterval = setInterval(() => {
        progress += 5;
        vizAnim.style.top = progress + '%';
        
        let blockCount = Math.floor(progress / 10);
        let blocks = '█'.repeat(blockCount) + '░'.repeat(10 - blockCount);
        
        vizAnim.innerHTML = `${protocol}\n${blocks}`;
        
        if (progress % 20 === 0 && progress < 100) {
            transferLog.innerHTML += `Uploading... ${blocks} ${progress}%\n`;
            transferLog.scrollTop = transferLog.scrollHeight;
        }
        
        if (progress >= 100) {
            clearInterval(animInterval);
            vizAnim.style.top = '100%';
            
            // Execute real backend request
            const formData = new FormData();
            formData.append('file', file);
            formData.append('protocol', protocol);
            formData.append('sender', 'Student');
            
            fetch('/api/files/upload', {
                method: 'POST',
                body: formData
            })
            .then(res => res.json())
            .then(data => {
                if(data.status === 'completed') {
                    transferLog.innerHTML += 'Transfer complete.\n200 OK from Flask.\nSocket transfer logged.';
                } else {
                    transferLog.innerHTML += 'Transfer failed.\n' + data.error;
                }
                transferLog.scrollTop = transferLog.scrollHeight;
            });
        }
    }, 100);
}
