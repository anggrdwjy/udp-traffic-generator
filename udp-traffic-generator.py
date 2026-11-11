#!/usr/bin/env python3

import argparse
import csv
import socket
import struct
import sys
import time

# ANSI colors
GREEN = '\033[0;32m'
RED = '\033[0;31m'
YELLOW = '\033[1;33m'
BLUE = '\033[0;34m'
CYAN = '\033[0;36m'
BOLD = '\033[1m'
NC = '\033[0m'

BANNER = f"""

██╗   ██╗██████╗ ██████╗     ████████╗██████╗  █████╗ ███████╗███████╗██╗ ██████╗
██║   ██║██╔══██╗██╔══██╗    ╚══██╔══╝██╔══██╗██╔══██╗██╔════╝██╔════╝██║██╔════╝
██║   ██║██║  ██║██████╔╝       ██║   ██████╔╝███████║█████╗  █████╗  ██║██║     
██║   ██║██║  ██║██╔═══╝        ██║   ██╔══██╗██╔══██║██╔══╝  ██╔══╝  ██║██║     
╚██████╔╝██████╔╝██║            ██║   ██║  ██║██║  ██║██║     ██║     ██║╚██████╗
 ╚═════╝ ╚═════╝ ╚═╝            ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝     ╚═╝     ╚═╝ ╚═════╝
                                                                                 
 ██████╗ ███████╗███╗   ██╗███████╗██████╗  █████╗ ████████╗ ██████╗ ██████╗     
██╔════╝ ██╔════╝████╗  ██║██╔════╝██╔══██╗██╔══██╗╚══██╔══╝██╔═══██╗██╔══██╗    
██║  ███╗█████╗  ██╔██╗ ██║█████╗  ██████╔╝███████║   ██║   ██║   ██║██████╔╝    
██║   ██║██╔══╝  ██║╚██╗██║██╔══╝  ██╔══██╗██╔══██║   ██║   ██║   ██║██╔══██╗    
╚██████╔╝███████╗██║ ╚████║███████╗██║  ██║██║  ██║   ██║   ╚██████╔╝██║  ██║    
 ╚═════╝ ╚══════╝╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝    ╚═════╝ ╚═╝  ╚═╝    
                                                                                 

** The author is not responsible for any consequences resulting from this script, 
as it was created solely for testing purposes in a lab or on a local network.
beyond the scope of applicable international law. **

author by anggrdwjy
source : https://github.com/anggrdwjy/udp-traffic-generator

"""


def print_banner():
    """Print startup banner. Colors only when output is a terminal."""
    if sys.stdout.isatty():
        # Cyan banner, reset at the end
        print("\033[96m" + BANNER + "\033[0m")
    else:
        print(BANNER)


def parse_args():
    ap = argparse.ArgumentParser(description="UDP one-way traffic generator with per-second graph")
    ap.add_argument("-t", "--target", required=True, help="Target IP")
    ap.add_argument("-p", "--port", type=int, default=5001, help="Target UDP port (default 5001)")
    ap.add_argument("-s", "--size", type=int, default=1400, help="Payload size in bytes (default 1400, max 65507)")
    ap.add_argument("-d", "--duration", type=float, default=10.0, help="Duration in seconds (default 10)")
    ap.add_argument("-r", "--rate", type=float, default=0.0, help="Target rate in Mbps (default 0 = flood)")
    ap.add_argument("--pps", type=float, default=0.0, help="Target packets per second (alternative to rate)")
    ap.add_argument("--flood", action="store_true", help="Send as fast as possible without pacing")
    ap.add_argument("--ttl", type=int, default=64, help="Packet TTL (default 64)")
    ap.add_argument("--tos", type=int, default=0, help="TOS/DSCP, e.g. 184 = EF (default 0)")
    ap.add_argument("--stat-interval", type=float, default=1.0, help="Stats interval in seconds (default 1.0)")
    ap.add_argument("--bar-width", type=int, default=40, help="Width of the ASCII bar chart (default 40)")
    ap.add_argument("--csv-file", default="", help="Optional CSV file for per-second stats (e.g. traffic.csv)")
    ap.add_argument("--no-banner", action="store_true", help="Hide the startup banner")
    return ap.parse_args()


def make_payload(size, seq):
    # 8-byte header: magic(4) + sequence number(4), rest is padding.
    pkt = struct.pack("!II", 0x55445047, seq)  # "UDPG"
    if size > len(pkt):
        pkt += b"\x00" * (size - len(pkt))
    return pkt[:size]


def draw_bar(mbps, max_mbps, width=40):
    """Return an ASCII bar scaled to max_mbps."""
    if max_mbps <= 0:
        return ""
    ratio = min(mbps / max_mbps, 1.0)
    filled = int(ratio * width)
    return "█" * filled + "░" * (width - filled)


def save_csv(history, csv_file):
    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["second", "mbps", "pps", "bytes", "packets"])
        writer.writeheader()
        for h in history:
            writer.writerow(h)
    print(f"[*] CSV saved to: {csv_file}")


def main():
    args = parse_args()
    if not args.no_banner:
        print_banner()

    size = min(max(args.size, 8), 65507)
    stat_interval = max(args.stat_interval, 0.1)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, args.ttl)
    if args.tos:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, args.tos)

    target = (args.target, args.port)

    # Determine inter-packet interval
    if args.flood or (args.rate == 0 and args.pps == 0):
        interval = 0.0
    elif args.pps > 0:
        interval = 1.0 / args.pps
    else:
        # Mbps -> interval per packet
        interval = (size * 8) / (args.rate * 1_000_000)

    seq = 0
    sent_bytes = 0
    sent_pkts = 0
    t0 = time.perf_counter()
    next_send = t0

    # Per-second tracking
    history = []
    sec_bytes = 0
    sec_pkts = 0
    sec_start = t0
    sec_num = 1
    next_stat = t0 + stat_interval
    peak_mbps = args.rate if args.rate > 0 else 0.0  # for bar scaling

    print(f"[+] Target {args.target}:{args.port} | size={size}B | "
          f"mode={'flood' if interval == 0 else f'interval={interval*1e6:.1f}us'} | duration={args.duration}s")
    print(f"[+] Live per-second graph (interval={stat_interval}s, bar scaled to peak/target)")
    print(f"{'Time':>6} | {'Mbps':>8} | {'pps':>8} | Graph")
    print("-" * (28 + args.bar_width))

    try:
        while True:
            elapsed = time.perf_counter() - t0
            if elapsed >= args.duration:
                break

            sock.sendto(make_payload(size, seq), target)
            sent_pkts += 1
            sent_bytes += size
            sec_pkts += 1
            sec_bytes += size
            seq = (seq + 1) & 0xFFFFFFFF

            # --- Per-second report ---
            now = time.perf_counter()
            if now >= next_stat:
                window = now - sec_start
                if window > 0 and sec_pkts > 0:
                    mbps_sec = (sec_bytes * 8) / window / 1e6
                    pps_sec = sec_pkts / window
                    peak_mbps = max(peak_mbps, mbps_sec)
                    bar = draw_bar(mbps_sec, peak_mbps, args.bar_width)
                    print(f"{sec_num*stat_interval:5.1f}s | {mbps_sec:8.2f} | {pps_sec:8.0f} | {bar} {mbps_sec:.1f} Mbps")
                    history.append({
                        "second": round(sec_num * stat_interval, 2),
                        "mbps": round(mbps_sec, 2),
                        "pps": round(pps_sec, 2),
                        "bytes": sec_bytes,
                        "packets": sec_pkts,
                    })
                # Reset for next interval
                sec_bytes = 0
                sec_pkts = 0
                sec_start = now
                sec_num += 1
                next_stat += stat_interval
                if next_stat < now:  # fell behind, resync
                    next_stat = now + stat_interval

            if interval > 0:
                next_send += interval
                delay = next_send - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                else:
                    # Falling behind: reschedule to avoid backlog buildup
                    next_send = time.perf_counter()
    except KeyboardInterrupt:
        print("\n[!] Interrupted by user (Ctrl+C)")
    finally:
        total = time.perf_counter() - t0
        sock.close()

        # Flush remaining partial interval (if test duration is not a multiple of stat_interval)
        if sec_pkts > 0:
            window = time.perf_counter() - sec_start
            if window > 0.05:
                mbps_sec = (sec_bytes * 8) / window / 1e6
                pps_sec = sec_pkts / window
                peak_mbps = max(peak_mbps, mbps_sec)
                bar = draw_bar(mbps_sec, peak_mbps, args.bar_width)
                print(f"{total:5.1f}s | {mbps_sec:8.2f} | {pps_sec:8.0f} | {bar} {mbps_sec:.1f} Mbps (partial)")
                history.append({
                    "second": round(total, 2),
                    "mbps": round(mbps_sec, 2),
                    "pps": round(pps_sec, 2),
                    "bytes": sec_bytes,
                    "packets": sec_pkts,
                })

        mbps_avg = (sent_bytes * 8) / total / 1e6 if total > 0 else 0
        pps_avg = sent_pkts / total if total > 0 else 0
        max_mbps = max([h["mbps"] for h in history], default=0)
        min_mbps = min([h["mbps"] for h in history], default=0)

        print("-" * (28 + args.bar_width))
        print(f"[*] Done: {sent_pkts} packets, {sent_bytes/1e6:.2f} MB in {total:.2f}s")
        print(f"[*] Avg: {mbps_avg:.2f} Mbps, {pps_avg:.0f} pps | "
              f"Per-second Min: {min_mbps:.2f} Mbps | Max: {max_mbps:.2f} Mbps")

        # Save CSV if requested
        if args.csv_file:
            try:
                save_csv(history, args.csv_file)
            except Exception as e:
                print(f"[!] Failed to save CSV: {e}")


if __name__ == "__main__":
    main()
