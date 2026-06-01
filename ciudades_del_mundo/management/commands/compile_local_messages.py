"""Compile project gettext PO catalogs without external gettext binaries."""

from __future__ import annotations

import ast
from pathlib import Path
import struct

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Compiles locale/*/LC_MESSAGES/django.po files to django.mo without GNU gettext."

    def add_arguments(self, parser):
        parser.add_argument(
            "languages",
            nargs="*",
            help="Optional language codes. If omitted, every locale directory is compiled.",
        )

    def handle(self, *args, **options):
        locale_root = Path(settings.BASE_DIR) / "locale"
        if not locale_root.exists():
            raise CommandError(f"Locale directory not found: {locale_root}")

        languages = options["languages"] or sorted(path.name for path in locale_root.iterdir() if path.is_dir())
        compiled = 0
        for language in languages:
            po_path = locale_root / language / "LC_MESSAGES" / "django.po"
            if not po_path.is_file():
                raise CommandError(f"PO catalog not found: {po_path}")

            entries = _parse_po(po_path)
            mo_path = po_path.with_suffix(".mo")
            _write_mo(entries, mo_path)
            compiled += 1
            self.stdout.write(self.style.SUCCESS(f"OK {language}: {len(entries)} entries -> {mo_path}"))

        if compiled == 0:
            raise CommandError("No catalogs compiled.")


def _parse_po(path: Path) -> dict[str, str]:
    """Parse the PO subset used by this project."""
    entries: dict[str, str] = {}
    current_key = None
    msgid = None
    msgstr = None
    fuzzy = False

    def flush():
        nonlocal msgid, msgstr, fuzzy
        if msgid is not None and msgstr is not None and not fuzzy:
            entries[msgid] = msgstr
        msgid = None
        msgstr = None
        fuzzy = False

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            current_key = None
            continue
        if line.startswith("#,") and "fuzzy" in line:
            fuzzy = True
            continue
        if line.startswith("#"):
            continue
        if line.startswith("msgid "):
            flush()
            current_key = "msgid"
            msgid = ast.literal_eval(line[6:].strip())
            msgstr = None
            continue
        if line.startswith("msgstr "):
            current_key = "msgstr"
            msgstr = ast.literal_eval(line[7:].strip())
            continue
        if line.startswith('"') and current_key:
            value = ast.literal_eval(line)
            if current_key == "msgid":
                msgid = (msgid or "") + value
            else:
                msgstr = (msgstr or "") + value

    flush()
    return entries


def _write_mo(entries: dict[str, str], output_path: Path) -> None:
    ids = sorted(entries)
    strs = [entries[msgid] for msgid in ids]
    ids_blob = b"\x00".join(msgid.encode("utf-8") for msgid in ids) + b"\x00"
    strs_blob = b"\x00".join(msgstr.encode("utf-8") for msgstr in strs) + b"\x00"
    count = len(ids)
    key_start = 7 * 4 + count * 16
    value_start = key_start + len(ids_blob)

    key_offsets = []
    offset = key_start
    for msgid in ids:
        data = msgid.encode("utf-8")
        key_offsets.append((len(data), offset))
        offset += len(data) + 1

    value_offsets = []
    offset = value_start
    for msgstr in strs:
        data = msgstr.encode("utf-8")
        value_offsets.append((len(data), offset))
        offset += len(data) + 1

    output = [struct.pack("Iiiiiii", 0x950412DE, 0, count, 7 * 4, 7 * 4 + count * 8, 0, 0)]
    output.extend(struct.pack("ii", length, offset) for length, offset in key_offsets)
    output.extend(struct.pack("ii", length, offset) for length, offset in value_offsets)
    output.append(ids_blob)
    output.append(strs_blob)
    output_path.write_bytes(b"".join(output))
