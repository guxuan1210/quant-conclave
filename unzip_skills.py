"""Unzip 妙想 skill packages."""
import zipfile, shutil, os
from pathlib import Path

HOME = Path.home()
SKILLS = HOME / "skills"
names = ["mx-data","mx-search","mx-xuangu","mx-zixuan","mx-moni","mx-poster"]

for n in names:
    zp = SKILLS / f"{n}.zip"
    tg = SKILLS / n
    if not zp.exists():
        print(f"MISSING ZIP: {zp}")
        continue
    if tg.exists():
        shutil.rmtree(tg)
    tg.mkdir(exist_ok=True)
    with zipfile.ZipFile(zp) as zf:
        zf.extractall(tg)
    md = tg / "SKILL.md"
    print(f"{'OK' if md.exists() else 'NO SKILL.md'}: {n} ({md.stat().st_size if md.exists() else 0} bytes)")

# Also set MX_APIKEY env var (permanent, Windows)
# Replace YOUR_KEY with the actual key from https://dl.dfcfs.com/m/itc4
import subprocess

_MX_KEY = os.environ.get("MX_APIKEY", "")
if _MX_KEY:
    try:
        subprocess.run(
            ["setx", "MX_APIKEY", _MX_KEY],
            capture_output=True, check=True,
        )
        print("\n✅ MX_APIKEY set via setx (restart terminal to take effect)")
    except subprocess.CalledProcessError as e:
        print(f"\n⚠️ Failed to set MX_APIKEY: {e.stderr.decode()}")
else:
    print("\n⚠️ MX_APIKEY not found in environment. Set it manually:")
    print("  PowerShell (Admin): [System.Environment]::SetEnvironmentVariable('MX_APIKEY','YOUR_KEY','User')")
    print("  Or get a key at https://dl.dfcfs.com/m/itc4")
