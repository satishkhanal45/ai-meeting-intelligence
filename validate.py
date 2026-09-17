#!/usr/bin/env python3
"""Project validation script.

Checks that the project structure is complete, required modules import
correctly, and the database initialises without errors.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

REQUIRED_MODULES = [
    "config",
    "logger",
    "models",
    "utils",
    "database",
    "prompts",
    "graph",
    "pipeline",
    "providers.base_provider",
    "providers.gemini_provider",
    "providers.groq_provider",
    "providers.openrouter_provider",
]

REQUIRED_FILES = [
    "app.py",
    "config.py",
    "logger.py",
    "models.py",
    "utils.py",
    "database.py",
    "prompts.py",
    "graph.py",
    "pipeline.py",
    "pyproject.toml",
    ".env.example",
    "README.md",
    "providers/__init__.py",
    "providers/base_provider.py",
    "providers/gemini_provider.py",
    "providers/groq_provider.py",
    "providers/openrouter_provider.py",
    "meetings/sample_transcript.txt",
    "tests/__init__.py",
    "tests/conftest.py",
    "tests/test_models.py",
    "tests/test_utils.py",
    "tests/test_database.py",
    "tests/test_graph.py",
    "tests/test_pipeline.py",
]


def check_files() -> list[str]:
    errors = []
    for path in REQUIRED_FILES:
        full_path = ROOT / path
        if not full_path.exists():
            errors.append(f"MISSING: {path}")
    return errors


def check_imports() -> list[str]:
    errors = []
    sys.path.insert(0, str(ROOT))
    for mod_name in REQUIRED_MODULES:
        try:
            importlib.import_module(mod_name)
        except Exception as exc:
            errors.append(f"IMPORT FAIL: {mod_name} — {exc}")
    return errors


def check_database() -> list[str]:
    errors = []
    try:
        from database import get_meeting_count, init_db

        init_db()
        count = get_meeting_count()
        print(f"  DB initialised OK. Meeting count: {count}")
    except Exception as exc:
        errors.append(f"DB ERROR: {exc}")
    return errors


def check_env() -> list[str]:
    errors = []
    env_path = ROOT / ".env"
    if not env_path.exists():
        errors.append("MISSING: .env (copy from .env.example)")
    else:
        try:
            from config import settings

            configured = settings.get_configured_providers()
            print(f"  Configured providers: {configured}")
            if not configured:
                errors.append("No providers configured — add API keys to .env")
        except Exception as exc:
            errors.append(f"ENV ERROR: {exc}")
    return errors


def main() -> int:
    print(f"\n{'='*60}")
    print("  AI Meeting Intelligence — Validation")
    print(f"{'='*60}\n")

    all_errors: list[str] = []

    print("[1/5] Checking required files...")
    errors = check_files()
    if errors:
        for e in errors:
            print(f"  ✗ {e}")
        all_errors.extend(errors)
    else:
        print("  ✓ All files present")

    print("[2/5] Checking module imports...")
    errors = check_imports()
    if errors:
        for e in errors:
            print(f"  ✗ {e}")
        all_errors.extend(errors)
    else:
        print("  ✓ All modules import correctly")

    print("[3/5] Checking database...")
    errors = check_database()
    if errors:
        for e in errors:
            print(f"  ✗ {e}")
        all_errors.extend(errors)
    else:
        print("  ✓ Database OK")

    print("[4/5] Checking .env configuration...")
    errors = check_env()
    if errors:
        for e in errors:
            print(f"  ✗ {e}")
        all_errors.extend(errors)
    else:
        print("  ✓ Environment OK")

    print("[5/5] Checking provider registration...")
    try:
        from pipeline import PROVIDER_REGISTRY, register_provider
        from providers.gemini_provider import GeminiProvider
        from providers.groq_provider import GroqProvider
        from providers.openrouter_provider import OpenRouterProvider

        register_provider("gemini", GeminiProvider)
        register_provider("groq", GroqProvider)
        register_provider("openrouter", OpenRouterProvider)

        print(f"  Registered providers: {list(PROVIDER_REGISTRY.keys())}")
        if not PROVIDER_REGISTRY:
            all_errors.append("No providers registered")
        else:
            print("  ✓ Provider registry OK")
    except Exception as exc:
        all_errors.append(f"PROVIDER ERROR: {exc}")

    print(f"\n{'='*60}")
    if all_errors:
        print(f"  VALIDATION FAILED — {len(all_errors)} issue(s) found\n")
        for e in all_errors:
            print(f"  ✗ {e}")
        print("\n  Fix the issues above before running the application.")
        return 1
    else:
        print("  ✓ VALIDATION PASSED — All checks OK")
        print("\n  Run:  uv run streamlit run app.py")
        return 0


if __name__ == "__main__":
    sys.exit(main())
