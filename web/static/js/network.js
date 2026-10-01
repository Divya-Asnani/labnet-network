
function runDiag(command) {
    const term = document.getElementById('diag-terminal');
    term.textContent = `student@labnet:~$ ${command.toUpperCase()}
Executing...

`;
    
    fetch('/api/diagnostics/' + command)
        .then(res => res.json())
        .then(data => {
            if(data.output) {
                term.textContent += data.output;
            } else if(data.error) {
                term.textContent += 'Error: ' + data.error;
            }
        })
        .catch(err => {
            term.textContent += 'Failed to execute diagnostic command.';
        });
}
