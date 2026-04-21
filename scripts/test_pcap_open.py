"""Probe which interface-name forms pcap-ct actually accepts on Windows."""
import pcap
import sys

print('=== findalldevs ===')
for d in pcap.findalldevs():
    print(' ', repr(d))

print()
print('=== ex_name("eth0") ... ex_name("eth10") ===')
for i in range(11):
    try:
        n = pcap.ex_name(f"eth{i}")
        print(f'  eth{i} -> {n!r}')
    except Exception as e:
        print(f'  eth{i} FAIL: {type(e).__name__}: {e}')

CANDIDATES = [
    'eth0', 'eth1', 'eth2', 'eth3', 'eth4', 'eth5',
    'Ethernet 14',
    r'\Device\NPF_{4C95DA23-E78D-4555-8861-C0F158E9F74E}',
]
print()
print('=== open attempts ===')
for name in CANDIDATES:
    print(f'pcap.pcap(name={name!r}) ...')
    try:
        s = pcap.pcap(name=name, snaplen=1600, promisc=True, immediate=True, timeout_ms=50)
        print(f'  OK -> {s}')
        try:
            s.close()
        except Exception:
            pass
    except Exception as e:
        print(f'  FAIL: {type(e).__name__}: {e}')
