"""Rewrite group references without persistence or HTTP dependencies."""
from __future__ import annotations

def replace_group_references(data: dict, *, old_key: str, new_key: str, normalize_key) -> bool:
    changed = False
    for table_key in ("include", "subtract"):
        changed = _replace_group_key_in_source_blocks(data.get(table_key), old_key=old_key, new_key=new_key, normalize_key=normalize_key) or changed
    changed = _replace_group_key_in_capital_blocks(data.get("capital_groups"), old_key=old_key, new_key=new_key, normalize_key=normalize_key) or changed
    for child in data.get("children") if isinstance(data.get("children"), list) else []:
        if not isinstance(child, dict):
            continue
        for table_key in ("include", "subtract"):
            changed = _replace_group_key_in_source_blocks(child.get(table_key), old_key=old_key, new_key=new_key, normalize_key=normalize_key) or changed
        changed = _replace_group_key_in_capital_blocks(child.get("capital_groups"), old_key=old_key, new_key=new_key, normalize_key=normalize_key) or changed
    return changed


def _replace_group_key_in_source_blocks(blocks, *, old_key: str, new_key: str, normalize_key) -> bool:
    changed = False
    if not isinstance(blocks, list):
        return False
    for block in blocks:
        if not isinstance(block, dict):
            continue
        groups, block_changed = _renamed_group_key_list(block.get("groups"), old_key=old_key, new_key=new_key, normalize_key=normalize_key)
        if block_changed:
            block["groups"] = groups
            changed = True
    return changed


def _replace_group_key_in_capital_blocks(blocks, *, old_key: str, new_key: str, normalize_key) -> bool:
    changed = False
    if not isinstance(blocks, list):
        return False
    for block in blocks:
        if not isinstance(block, dict):
            continue
        for field in ("group", "group_key"):
            value = str(block.get(field) or "").strip()
            if value and normalize_key(value) == old_key:
                block[field] = new_key
                changed = True
    return changed


def _renamed_group_key_list(values, *, old_key: str, new_key: str, normalize_key) -> tuple[list[str], bool]:
    if not isinstance(values, list):
        return [], False
    changed = False
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        raw = str(value or "").strip()
        if not raw:
            continue
        normalized = normalize_key(raw)
        if normalized == old_key:
            candidate = new_key
            changed = True
        else:
            candidate = raw
        candidate_key = normalize_key(candidate)
        if candidate_key in seen:
            changed = True
            continue
        seen.add(candidate_key)
        result.append(candidate)
    return result, changed
