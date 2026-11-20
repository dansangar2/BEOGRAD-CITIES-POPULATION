#!/usr/bin/env python
"""Explicit entry point for the maintained Python HTTP backend."""
import os
from pathlib import Path
import sys


def main():
    # Share the current Django implementation, including models and CLI tasks.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ["BEOGRAD_BACKEND"] = "python"
    os.environ["BEOGRAD_JAVA_ENABLED"] = "0"
    from manage import main as django_main

    django_main()


if __name__ == "__main__":
    main()
