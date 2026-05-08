import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.database import Base
from utils.import_util import ImportUtil


def test_find_models_skips_dot_venv_site_packages(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project_root = tmp_path
    (project_root / 'requirements.txt').write_text('sqlalchemy\n')
    package_dir = project_root / '.venv/lib/python3.12/site-packages/cohere/v2/types'
    package_dir.mkdir(parents=True)
    (package_dir / 'v2chat_stream_response.py').write_text('class Ignored: pass\n')

    imported_modules = []
    real_import_module = importlib.import_module

    def capture_import_module(module_name: str) -> ModuleType:
        imported_modules.append(module_name)
        return real_import_module(module_name)

    monkeypatch.setattr(ImportUtil, 'find_project_root', classmethod(lambda cls: project_root))
    monkeypatch.setattr(importlib, 'import_module', capture_import_module)
    ImportUtil.find_models.cache_clear()

    assert ImportUtil.find_models(Base) == []
    assert not any(module_name.startswith('.venv') for module_name in imported_modules)
