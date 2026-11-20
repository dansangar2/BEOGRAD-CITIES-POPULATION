"""Check a source-only copy without local DB, seeds, exports or diagnostic state."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def verify(python: str) -> None:
    temporary = tempfile.TemporaryDirectory(prefix="beograd-clean-")
    destination = Path(temporary.name).resolve()
    try:
        for name in ("manage.py", "requirements.txt"):
            shutil.copy2(ROOT / name, destination / name)

        def ignore(directory, names):
            excluded = {name for name in names if name == "__pycache__" or name.endswith((".pyc", ".pyo"))}
            if Path(directory).resolve() == (ROOT / "ciudades_del_mundo").resolve():
                excluded.update(set(names) & {"html", "subdivisions", "new_country_configs", "subdivision_groups",
                                             "historical_divisions_old", "new_subdivisions_old"})
            return excluded

        for name in ("ciudades_del_mundo", "backend-python", "tools", "tests", "locale"):
            shutil.copytree(ROOT / name, destination / name, ignore=ignore)
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.update(PYTHONDONTWRITEBYTECODE="1", DJANGO_SETTINGS_MODULE="ciudades_del_mundo.tests.isolated_settings")
        commands = [
            [python, "-m", "pip", "check"],
            [python, "-B", "-m", "tools.build_web_assets", "--check"],
            [python, "-B", "manage.py", "check", "--settings=ciudades_del_mundo.tests.isolated_settings"],
            [python, "-B", "manage.py", "test",
             "ciudades_del_mundo.tests.test_architecture_boundaries", "ciudades_del_mundo.tests.test_web_modularity",
             "ciudades_del_mundo.tests.test_http_queries", "ciudades_del_mundo.tests.test_task_lifecycle", "ciudades_del_mundo.tests.test_ai_provider",
             "ciudades_del_mundo.tests.test_group_references", "ciudades_del_mundo.tests.test_derived_config_mutations",
             "ciudades_del_mundo.tests.test_ai_text_enrichment", "ciudades_del_mundo.tests.test_nuevo_admin_excel_export",
             "--settings=ciudades_del_mundo.tests.isolated_settings", "--noinput"],
            [shutil.which("node") or "node", "--test", "tests/frontend/behavior.test.cjs"],
        ]
        for command in commands:
            subprocess.run(command, cwd=destination, env=env, check=True)
        if (destination / "db.sqlite3").exists():
            raise AssertionError("Verification created a local database.")
        print("Clean source copy verified without local database or seeds.")
    finally:
        # Check the exact temporary target before recursive cleanup on Windows.
        if destination.parent != Path(tempfile.gettempdir()).resolve() or not destination.name.startswith("beograd-clean-"):
            raise RuntimeError("Unexpected cleanup target")
        temporary.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable, help="Interpreter with requirements.txt installed.")
    args = parser.parse_args()
    verify(str(Path(args.python).resolve()))


if __name__ == "__main__":
    main()
