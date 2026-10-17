from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import os
import shutil

APP_ROOT = Path(__file__).resolve().parents[1]
CLI_ENV_OVERRIDES = {"PYTHONUTF8": "1", "AGY_CLI_DISABLE_AUTO_UPDATE": "true"}
TERMINAL = {"completed", "cancelled", "timed_out", "failed"}

def resolve_cli(root):
    local = Path(root) / 'webui.local.json'
    if local.exists():
        try:
            value = json.loads(local.read_text(encoding='utf-8-sig'))
            if set(value) != {'cli_path'} or not isinstance(value['cli_path'], str) or not value['cli_path'].strip():
                raise ValueError()
            path = Path(value['cli_path'])
            if not path.is_absolute():raise ValueError()
            return path
        except (OSError, ValueError, TypeError, KeyError):
            raise ValueError('webui.local.json が無効です。Setup.ps1 -CliPath で絶対パスを指定してください。') from None
    standard = Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'AppData/Local'))) / 'agy/bin/agy.exe'
    if standard.is_file():return standard
    found = shutil.which('agy.exe')
    return Path(found) if found else standard

@dataclass
class Config:
    root: Path
    token: str
    port: int
    mock: bool = False
    browser_timeout: float = 15
    server_timeout: float = 5
    cleanup_timeout: float = 5
    cli: Path | None = None
    mock_scenario: str = 'normal'
    startup_id: str | None = None

    def __post_init__(self):
        self.root = Path(self.root)
        self.cli = Path(self.cli) if self.cli is not None else resolve_cli(self.root)

    @property
    def data(self):
        return self.root / "data"

    @property
    def mutex_prefix(self):
        name = hashlib.sha256(str(self.root.resolve()).casefold().encode()).hexdigest()[:24]
        return "Local\\AGWeb_" + name

    @property
    def origin(self):
        return f"http://127.0.0.1:{self.port}"

def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()
