"""Explicit maintainer bridge: emit pinned requirements from downloaded wheels.

Download reviewed versions into backend-java/.bootstrap/python-wheels first.
This helper performs no network requests and is never used at startup.
"""
from pathlib import Path
import email
import hashlib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    records = {}
    for path in sorted((ROOT / "backend-java/.bootstrap/python-wheels").glob("*.whl")):
        with zipfile.ZipFile(path) as wheel:
            member = next(name for name in wheel.namelist() if name.endswith(".dist-info/METADATA"))
            metadata = email.message_from_bytes(wheel.read(member))
        key = metadata["Name"].lower().replace("_", "-") + "==" + metadata["Version"]
        records.setdefault(key, []).append(hashlib.sha256(path.read_bytes()).hexdigest())
    if not records:
        raise SystemExit("No wheels to lock")
    target = ROOT / "backend-java/requirements-local.lock"
    content = "# Windows local deployment. Direct dependencies match ../requirements.txt.\n# Universal wheels plus the verified Windows CPython wheel; require-hashes is mandatory.\n"
    for name, hashes in sorted(records.items()):
        content += name + " \\\n" + " \\\n".join("    --hash=sha256:" + h for h in sorted(hashes)) + "\n"
    target.write_text(content, encoding="ascii")
    print(f"Locked {len(records)} Python distributions")


if __name__ == "__main__":
    main()
