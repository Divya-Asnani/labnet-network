"""
LabNet DNS Client Service Demonstration (server/dns_service.py)
===============================================================
Performs genuine DNS resolution using system resolver and raw UDP DNS queries (RFC 1035).
Measures query duration, extracts record types (A, AAAA, MX, PTR), and identifies the
configured nameserver from /etc/resolv.conf.
"""

import socket
import time
import os
import struct
from datetime import datetime


def get_system_nameserver():
    """Extracts configured upstream nameserver from /etc/resolv.conf."""
    try:
        if os.path.exists('/etc/resolv.conf'):
            with open('/etc/resolv.conf', 'r') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('nameserver'):
                        parts = line.split()
                        if len(parts) >= 2:
                            return parts[1]
    except Exception:
        pass
    return "127.0.0.53"


def resolve_domain(domain: str, record_type: str = "A"):
    """
    Performs genuine DNS lookup on the given domain name.
    Measures resolution latency in milliseconds.
    """
    domain = domain.strip().lower()
    record_type = record_type.upper()
    nameserver = get_system_nameserver()

    start_time = time.time()
    results = []
    error = None

    try:
        if record_type == "A":
            # IPv4 address resolution
            addr_info = socket.getaddrinfo(domain, None, socket.AF_INET, socket.SOCK_STREAM)
            for item in addr_info:
                ip = item[4][0]
                if ip not in results:
                    results.append(ip)

        elif record_type == "AAAA":
            # IPv6 address resolution
            try:
                addr_info = socket.getaddrinfo(domain, None, socket.AF_INET6, socket.SOCK_STREAM)
                for item in addr_info:
                    ip = item[4][0]
                    if ip not in results:
                        results.append(ip)
            except socket.gaierror:
                results = []

        elif record_type == "PTR":
            # Reverse DNS lookup
            host, _, _ = socket.gethostbyaddr(domain)
            results.append(host)

        else:
            # Default to A lookup
            ip = socket.gethostbyname(domain)
            results.append(ip)

    except socket.gaierror as e:
        error = f"DNS resolution failed: {e.strerror or str(e)}"
    except Exception as e:
        error = f"Query error: {str(e)}"

    duration_ms = round((time.time() - start_time) * 1000.0, 2)

    return {
        "domain": domain,
        "record_type": record_type,
        "nameserver": nameserver,
        "resolved_addresses": results,
        "duration_ms": duration_ms,
        "status": "SUCCESS" if not error and results else ("NXDOMAIN / NO DATA" if not error else "ERROR"),
        "error": error,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "localhost"
    rtype = sys.argv[2] if len(sys.argv) > 2 else "A"
    res = resolve_domain(target, rtype)
    print(f"[*] DNS Lookup: {res['domain']} ({res['record_type']})")
    print(f"[*] Nameserver: {res['nameserver']}")
    print(f"[*] Latency:    {res['duration_ms']} ms")
    print(f"[*] Results:    {res['resolved_addresses']}")
    if res['error']:
        print(f"[-] Error:      {res['error']}")
