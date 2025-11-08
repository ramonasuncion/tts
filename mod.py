import os
import re
import unicodedata
import time

_url_re = re.compile(r"(https?://\S+|www\.\S+)", re.IGNORECASE)
_emoji_re = re.compile(
    "["
    + "\U0001f600-\U0001f64f"
    + "\U0001f300-\U0001f5ff"
    + "\U0001f680-\U0001f6ff"
    + "\U0001f1e6-\U0001f1ff"
    + "\U00002700-\U000027bf"
    + "\U0001f900-\U0001f9ff"
    + "\U00002600-\U000026ff"
    + "\U00002b00-\U00002bff"
    + "]",
    flags=re.UNICODE,
)

_leet = {
    "a": "[a@4]",
    "b": "[b8]",
    "e": "[e3]",
    "i": "[i1!|]",
    "l": "[l1|]",
    "o": "[o0]",
    "s": "[s5$]",
    "t": "[t7]",
    "g": "[g9]",
    "z": "[z2]",
}


def _normalize(s):
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def _obfus_rx(term):
    t = _normalize(term.lower())
    parts = []
    for ch in t:
        parts.append(_leet.get(ch, re.escape(ch)) if ch.isalnum() else re.escape(ch))
    glue = r"[^a-zA-Z0-9]{0,2}"
    return re.compile(glue.join(parts), re.IGNORECASE)


class SlurCensor:
    def __init__(self, path):
        self.path = path
        self.rxs = []
        self.raw = []
        self.mtime = None
        if path:
            self._reload()

    def _read_terms(self):
        if not self.path or not os.path.exists(self.path):
            return []
        with open(self.path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f]
        return [t for t in lines if t and not t.startswith("#")]

    def _reload(self):
        terms = self._read_terms()
        self.raw = terms
        self.rxs = [_obfus_rx(t) for t in terms]
        try:
            self.mtime = os.path.getmtime(self.path) if self.path else None
        except OSError:
            self.mtime = None

    def ensure_fresh(self):
        if not self.path:
            return
        try:
            mt = os.path.getmtime(self.path)
        except OSError:
            mt = None
        if mt and mt != self.mtime:
            self._reload()

    def reload(self):
        self._reload()

    def list(self):
        self.ensure_fresh()
        return list(self.raw)

    def _mask(self, s):
        n = 0

        def repl(m):
            nonlocal n
            n += 1
            src = m.group(0)
            return (
                "*" * len(src)
                if len(src) <= 2
                else src[0] + "*" * (len(src) - 2) + src[-1]
            )

        for rx in self.rxs:
            s = rx.sub(repl, s)
        return s, n

    def _drop(self, s):
        n = 0

        def repl(m):
            nonlocal n
            n += 1
            return ""  # remove the word entirely

        for rx in self.rxs:
            s = rx.sub(repl, s)
        # collapse double spaces left by drops
        return " ".join(s.split()), n

    def censor(self, s, mode="drop"):
        self.ensure_fresh()
        if not self.rxs:
            return s, 0
        return self._drop(s) if mode == "drop" else self._mask(s)

    def _mask_token(src):
        return (
            "*" * len(src)
            if len(src) <= 2
            else (src[0] + "*" * (len(src) - 2) + src[-1])
        )


class Moderator:
    def __init__(self, cfg=None):
        cfg = cfg or {}
        self.strip_urls = bool(cfg.get("strip_urls", True))
        self.strip_emojis = bool(cfg.get("strip_emojis", True))
        self.censor_slurs = bool(cfg.get("censor_slurs", True))
        bl_path = cfg.get("blocklist_path")
        self.censor = SlurCensor(bl_path) if bl_path else SlurCensor(None)

    def filter(self, s, mode="mask"):
        out = s or ""
        flags = {"urls": 0, "emojis": 0, "slurs": 0}

        if self.strip_urls:
            before = out
            out = _url_re.sub("[link]", out)
            if out != before:
                flags["urls"] = 1

        if self.strip_emojis:
            before = out
            out = _emoji_re.sub("", out)
            if out != before:
                flags["emojis"] = 1

        if self.censor_slurs and self.censor:
            n = 0

            def repl(m):
                nonlocal n
                n += 1
                return _mask_token(m.group(0)) if mode == "mask" else ""

            for rx in self.censor.rxs:
                out = rx.sub(repl, out)
            flags["slurs"] = n

        return out.strip(), flags
