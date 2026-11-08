"""Where PersonalTube keeps its files: ~/.personaltube, or $PERSONALTUBE_HOME."""
import json
import os
from pathlib import Path

HOME = Path(os.environ.get("PERSONALTUBE_HOME") or Path.home() / ".personaltube")
SITE = Path(__file__).resolve().parent / "site"

DEFAULT_CONFIG = {
    "language": "English",
    "ai": {"provider": "openrouter", "model": "", "profile_model": "", "api_key": "", "base_url": ""},
    "youtube": {"browser": "chrome"},
    "port": 8777,
}


def read(name, default=None):
    path = HOME / name
    return json.loads(path.read_text()) if path.exists() else default


def write(name, data, pretty=False):
    """Write JSON atomically and readable only by the current user."""
    HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = HOME / name
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2 if pretty else None,
                              separators=None if pretty else (",", ":")))
    tmp.chmod(0o600)
    tmp.replace(path)


def config():
    saved = read("config.json", {})
    cfg = {**DEFAULT_CONFIG, **saved}
    for section in ("ai", "youtube"):
        cfg[section] = {**DEFAULT_CONFIG[section], **saved.get(section, {})}
    return cfg


def save_config(cfg):
    write("config.json", cfg, pretty=True)
