"""
LabNet UDP Heartbeat Client (clients/heartbeat_client.py)
=========================================================
Sends periodic UDP heartbeat datagrams to the LabNet server on port 6000.
Supports configurable intervals, client ID, target server IP, and
controlled missed-heartbeat simulation to demonstrate offline detection.
"""

import socket
import time
import argparse
import sys
from datetime import datetime


def main():
    parser = argparse.ArgumentParser(description="LabNet UDP Heartbeat Client")
    parser.add_argument("--server", default="192.168.66.128", help="Target server IP (default: 192.168.66.128)")
    parser.add_argument("--port", type=int, default=6000, help="Target UDP port (default: 6000)")
    parser.add_argument("--id", default="Student-VM2", help="Device/Client ID (default: Student-VM2)")
    parser.add_argument("--interval", type=float, default=2.0, help="Heartbeat interval in seconds (default: 2.0)")
    parser.add_argument("--simulate-missed", type=int, default=0, help="Number of heartbeats to skip for testing offline detection")

    args = parser.parse_args()

    client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client_socket.settimeout(2.0)

    print("==================================================")
    print("        LABNET UDP HEARTBEAT CLIENT               ")
    print("==================================================")
    print(f"[*] Client ID:       {args.id}")
    print(f"[*] Target Server:   {args.server}:{args.port}")
    print(f"[*] Interval:        {args.interval}s")
    if args.simulate_missed > 0:
        print(f"[*] Simulated Drops: Will pause after 3 heartbeats for {args.simulate_missed * args.interval}s")
    print("--------------------------------------------------")
    print("[*] Press Ctrl+C to stop sending heartbeats.\n")

    seq = 0
    try:
        while True:
            seq += 1
            now_ts = time.time()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Check for simulated missed heartbeats
            if args.simulate_missed > 0 and seq == 4:
                skip_sec = args.simulate_missed * args.interval + 2.0
                print(f"\n[SIMULATION] Pausing heartbeats for {skip_sec:.1f}s to trigger server timeout...")
                time.sleep(skip_sec)
                print("[SIMULATION] Resuming heartbeats to demonstrate recovery.\n")
                args.simulate_missed = 0  # Only trigger once

            message = f"HEARTBEAT|{args.id}|{now_ts}"
            client_socket.sendto(message.encode(), (args.server, args.port))

            try:
                data, _ = client_socket.recvfrom(1024)
                ack_str = data.decode()
                rtt_ms = (time.time() - now_ts) * 1000.0
                print(f"[{now_str}] Packet #{seq} -> ACK received ({ack_str}) [RTT: {rtt_ms:.2f}ms]")
            except socket.timeout:
                print(f"[{now_str}] Packet #{seq} -> TIMEOUT: No ACK received from {args.server}:{args.port}")

            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\n[*] Heartbeat client stopped cleanly by user.")
    finally:
        client_socket.close()


if __name__ == "__main__":
    main()
