"""Optional multicast smoke: python3 tests/lab_traffic_smoke.py (no VM needed).

Sends synthetic Ethernet frames to an isolated, random loopback multicast group.
Two listeners must receive the same frame, proving the observer does not steal it.
"""
import socket
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vmctl import lab_traffic, qemu


def main():
    endpoint = qemu.segment_endpoint("telemetry-test-" + uuid.uuid4().hex)
    address, port = endpoint.split(":")
    macs = ["52:54:00:00:00:01", "52:54:00:00:00:02"]
    monitor = lab_traffic.TrafficMonitor()
    peer = lab_traffic.SegmentListener(endpoint)
    try:
        first = monitor.segment(endpoint, macs)
        peer.sample(macs)
        packet = bytes.fromhex("5254000000025254000000010800") + bytes(84)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(qemu.MCAST_LOCALADDR))
            sender.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            sender.sendto(packet, (address, int(port)))
        deadline = time.monotonic() + 2
        while True:
            observed, received = monitor.segment(endpoint, macs), peer.sample(macs)
            if observed["counters"][macs[0]]["tx"] and received["counters"][macs[1]]["rx"]:
                break
            if time.monotonic() > deadline:
                raise AssertionError("Loopback multicast frame was not received by both observers")
            time.sleep(.02)
        expected = {macs[0]: {"rx": 0, "tx": 98}, macs[1]: {"rx": 98, "tx": 0}}
        assert observed["counters"] == received["counters"] == expected
        assert observed["epoch"] == first["epoch"]
        # Closing the map releases the socket after its idle lease even with no requests.
        listener = monitor.listeners[endpoint]
        with listener.lock:
            listener.last_used = time.monotonic() - lab_traffic.IDLE_SECONDS - 1
        listener.thread.join(timeout=2)
        assert listener.stop.is_set() and listener.sock.fileno() == -1
        restarted = monitor.segment(endpoint, macs)
        assert restarted["epoch"] != first["epoch"]
        assert all(values == {"rx": 0, "tx": 0} for values in restarted["counters"].values())
        print("PASS: real multicast TX/RX, independent peer delivery, idle cleanup and fresh capture")
    finally:
        peer.close()
        monitor.close()


if __name__ == "__main__":
    main()
