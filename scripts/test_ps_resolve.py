"""Debug how subprocess invokes PowerShell to resolve NPF GUID -> NIC Name."""
import subprocess

npf_guid = "{4C95DA23-E78D-4555-8861-C0F158E9F74E}"

# V1 — what phase2_slac.py currently does
ps_cmd = (
    f"(Get-NetAdapter | Where-Object "
    f"{{ $_.InterfaceGuid -eq '{npf_guid.upper()}' }})."
    f"Name"
)
print("=== V1 (braces in-line) ===")
print(f"cmd: {ps_cmd!r}")
out = subprocess.run(
    ["powershell.exe", "-NoProfile", "-Command", ps_cmd],
    capture_output=True, text=True, timeout=5,
)
print(f"rc={out.returncode}")
print(f"stdout: {out.stdout!r}")
print(f"stderr: {out.stderr!r}")

# V2 — explicit wrapping that doesn't rely on hashbraces
ps_cmd2 = (
    f"$g='{npf_guid.upper()}'; "
    f"(Get-NetAdapter | Where-Object InterfaceGuid -eq $g).Name"
)
print()
print("=== V2 (no inline braces) ===")
print(f"cmd: {ps_cmd2!r}")
out = subprocess.run(
    ["powershell.exe", "-NoProfile", "-Command", ps_cmd2],
    capture_output=True, text=True, timeout=5,
)
print(f"rc={out.returncode}")
print(f"stdout: {out.stdout!r}")
print(f"stderr: {out.stderr!r}")
