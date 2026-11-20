"""Validate and render explicitly editable legacy recipes."""

from __future__ import annotations


import ast
import json
from pathlib import Path
from pprint import pformat
from django.http import Http404
from django.utils.translation import gettext as _
from ciudades_del_mundo.models import NuevoAdminArea
from ciudades_del_mundo.web.tasks import task_manager
from ciudades_del_mundo.web import view_support as _view_support


def _recipe_summaries() -> list[dict]:
    rows = []
    for group, root, editable in (
        ("new", _view_support.NEW_RECIPES_ROOT, True),
        ("historical", _view_support.HISTORICAL_RECIPES_ROOT, False),
    ):
        for path in sorted(root.glob("*.py")):
            if path.name.startswith("_"):
                continue
            slug = path.stem
            built_count = NuevoAdminArea.objects.filter(country_code=slug).count()
            rows.append(
                {
                    "slug": slug,
                    "group": group,
                    "editable": editable,
                    "path": path,
                    "root_name": _literal_assignment(path, ("ROOT_NAME", "COUNTRY_NAME")) or slug,
                    "built_count": built_count,
                    "active_task": task_manager.latest_for_key(f"build:{slug}"),
                }
            )
    return rows


def _normalize_recipe_slug(value: str) -> str:
    slug = (value or "").strip().lower()
    if not _view_support.RECIPE_SLUG_RE.fullmatch(slug):
        raise ValueError(_("El codigo debe empezar por una letra y usar letras sin acentos, numeros o guion bajo, sin espacios."))
    return slug


def _recipe_path(slug: str, *, group: str, must_exist: bool = True) -> Path:
    root = _view_support.NEW_RECIPES_ROOT if group == "new" else _view_support.HISTORICAL_RECIPES_ROOT
    path = root / f"{slug}.py"
    if must_exist and not path.is_file():
        raise Http404(_("No existe la receta '%(slug)s'.") % {"slug": slug})
    return path


def _any_recipe_path(slug: str) -> Path:
    for group in ("new", "historical"):
        path = _recipe_path(slug, group=group, must_exist=False)
        if path.is_file():
            return path
    raise Http404(_("No existe la receta '%(slug)s'.") % {"slug": slug})


def _render_recipe_from_form(form: dict) -> str:
    try:
        divisions = json.loads(form["divisions_json"])
    except json.JSONDecodeError as exc:
        raise ValueError(_("DIVISIONS no es JSON valido: %(error)s") % {"error": exc}) from exc
    if not isinstance(divisions, (list, dict)):
        raise ValueError(_("DIVISIONS debe ser una lista o un objeto JSON."))
    divisions = _numeric_keys_to_int(divisions)

    lines = [
        '"""Derived hierarchy recipe created from the web interface."""',
        "",
    ]
    if form.get("source_country"):
        lines.append(f"SOURCE_COUNTRY = {form['source_country']!r}")
    if form.get("root_name"):
        lines.append(f"ROOT_NAME = {form['root_name']!r}")
    if form.get("municipal_level"):
        lines.append(f"MUNICIPAL_LEVEL = {int(form['municipal_level'])}")

    representation = {}
    if form.get("representation_level"):
        representation["level"] = int(form["representation_level"])
    if form.get("representation_total"):
        representation["total"] = int(form["representation_total"])
    if form.get("representation_min"):
        representation["min"] = int(form["representation_min"])
    if representation:
        representation["system"] = "dhondt"
        lines.append(f"REPRESENTATION = {pformat(representation, sort_dicts=False)}")

    lines.append(f"DIVISIONS = {pformat(divisions, width=100, sort_dicts=False)}")
    lines.append("")
    return "\n".join(lines)


def _numeric_keys_to_int(value):
    if isinstance(value, list):
        return [_numeric_keys_to_int(item) for item in value]
    if isinstance(value, dict):
        converted = {}
        for key, item in value.items():
            if isinstance(key, str) and key.isdigit():
                key = int(key)
            converted[key] = _numeric_keys_to_int(item)
        return converted
    return value


def _validate_recipe_text(content: str, *, filename: str) -> None:
    try:
        tree = ast.parse(content, filename=filename)
        compile(tree, filename, "exec")
    except SyntaxError as exc:
        raise ValueError(_("Python invalido: %(error)s") % {"error": exc}) from exc

    assignment_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignment_names.add(target.id)
    if "DIVISIONS" not in assignment_names:
        raise ValueError(_("La receta debe definir DIVISIONS."))


def _literal_assignment(path: Path, names: tuple[str, ...]) -> str | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in names:
                try:
                    value = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    continue
                return str(value)
    return None
