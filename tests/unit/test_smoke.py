from __future__ import annotations

import research_assistant


def test_package_imports_and_exposes_version() -> None:
    assert research_assistant.__version__ == "0.1.0"
