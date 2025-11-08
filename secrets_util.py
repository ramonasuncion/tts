import os
import stat
import secrets
import yaml

ROLES = ["admin", "mod", "tts", "push", "pull", "overlay"]


def _chmod600(p):
    try:
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    except:
        pass


def _read_yaml(p):
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _write_yaml(p, data):
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=True)
    _chmod600(p)


def ensure_session_secret(path="./secrets.yaml"):
    data = _read_yaml(path)
    if "session_secret" not in data:
        data["session_secret"] = secrets.token_urlsafe(48)
        _write_yaml(path, data)
        print(f"[session] wrote {path}")
        print("[session] keep session_secret private")
    return data["session_secret"]


def ensure_keys(auth_cfg: dict):
    path = (auth_cfg or {}).get("file") or "./secrets.yaml"
    data = _read_yaml(path)
    ks = dict(data.get("keys", {}))
    created = []
    for r in ROLES:
        if not ks.get(r):
            ks[r] = secrets.token_urlsafe(32)
            created.append(r)
    if created or "keys" not in data:
        data["keys"] = ks
        _write_yaml(path, data)
        print(f"[auth] wrote {path}")
        for r in created:
            print(f"[auth] save this {r} key: {ks[r]}")
    return ks
