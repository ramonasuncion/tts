import os
from collections import deque
from fastapi import FastAPI, APIRouter, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

import secrets_util as sec
import tts as eng
import uuid

MAX_SOUNDS = 10


ROLE_TREE = {
    "admin": {"admin", "mod", "tts", "push", "pull"},
    "mod": {"mod", "tts"},
    "tts": {"tts"},
    "push": {"push"},
    "pull": {"pull"},
    "overlay": {"tts", "pull"},
}


def _eff_from_session(req):
    eff = set()
    for r, grants in ROLE_TREE.items():
        if req.session.get(r):
            eff |= grants
    return eff


def _eff_from_key(k):
    if not k:
        return set()
    import tts as eng

    eff = set()
    for r, grants in ROLE_TREE.items():
        if eng.auth_ok(r, k):
            eff |= grants
    return eff


def need(role):
    async def dep(req: Request):
        import tts as eng

        if not eng.auth_enabled():
            return
        if role in _eff_from_session(req):
            return
        k = req.headers.get("x-api-key") or req.headers.get("authorization") or ""
        if k.lower().startswith("bearer "):
            k = k[7:]
        if role in _eff_from_key(k):
            return
        raise HTTPException(401, "unauthorized")

    return Depends(dep)


def make_app(cfg):
    global app, Q
    Q = deque(maxlen=256)

    eng.init(cfg)
    app = FastAPI(title="tts")

    sd = cfg.get("sounds_dir", "./sounds")
    if os.path.isdir(sd):
        app.mount("/sounds", StaticFiles(directory=sd), name="sounds")

    s = cfg.get("session") or {}
    app.state.cfg = cfg
    secrets_file = (
        s.get("file")
        or (cfg.get("auth") or {}).get("file")
        or cfg.get("secrets_file")
        or "./secrets.yaml"
    )
    secret = s.get("secret") or sec.ensure_session_secret(secrets_file)
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        session_cookie=s.get("cookie_name", "sid"),
        same_site=s.get("same_site", "lax"),
        https_only=bool(s.get("secure", False)),
    )

    ori = cfg.get("cors_allow_origins", "*")
    if ori == "*":
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
    else:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[s.strip() for s in ori.split(",") if s.strip()],
            allow_methods=["*"],
            allow_headers=["*"],
        )
    r = APIRouter(prefix="/api")

    @r.get("/sounds", dependencies=[need("tts")])
    def list_sounds():
        return {"index": eng.get_sfx_index(), "aliases": eng.get_sfx_aliases()}

    @r.post("/sfx_aliases", dependencies=[need("admin")])
    async def add_sfx_alias(req: Request):
        j = await req.json()
        eng.set_sfx_alias(
            (j.get("name") or "").strip().lower(), (j.get("target") or "").strip()
        )
        return {"aliases": eng.get_sfx_aliases()}

    @r.delete("/sfx_aliases/{name}", dependencies=[need("admin")])
    def del_sfx_alias(name: str):
        eng.del_sfx_alias(name)
        return {"aliases": eng.get_sfx_aliases()}

    @r.post("/tts_batch", dependencies=[need("tts")])
    async def tts_batch(req: Request):
        j = await req.json()
        parts = j.get("parts") or []
        fmt = (j.get("format") or "mp3").lower()
        norm = bool(
            j.get("normalize")
            if j.get("normalize") is not None
            else eng.cfg.get("normalize", False)
        )

        ls = j.get("length_scale")
        ns = j.get("noise_scale")
        nw = j.get("noise_w")
        ss = j.get("sentence_silence")
        spk = j.get("speaker_id")

        segs, rm, sfx_count = [], [], 0
        try:
            for p in parts:
                if "sfx" in p:
                    if sfx_count > MAX_SOUNDS:
                        continue
                    _, ap = eng._resolve_sfx(p.get("sfx"))
                    if not ap:
                        continue
                    wav48 = eng._to_48k_mono_wav(ap)
                    segs.append(wav48)
                    if wav48 != ap:
                        rm.append(wav48)
                    sfx_count += 1
                else:
                    txt = (p.get("text") or "").strip()
                    if not txt:
                        continue
                    reqv = (p.get("voice") or "").strip()
                    vid, _ = eng._resolve_voice_id(reqv)
                    wav, tmp = eng._render_tts_wav(txt, vid, ls, ns, nw, ss, spk, norm)
                    rm += tmp
                    wav48 = eng._to_48k_mono_wav(wav)
                    segs.append(wav48)
                    if wav48 != wav:
                        rm.append(wav48)

            if not segs:
                raise HTTPException(400, "empty parts")

            b, m = eng._concat_wavs(segs, fmt=fmt, bitrate=j.get("bitrate"))
            rid = uuid.uuid4().hex[:8]
            h = {
                "Content-Disposition": f'inline; filename="batch-{rid}.{"mp3" if m=="audio/mpeg" else "wav"}"',
                "Cache-Control": "no-store",
            }
            return Response(content=b, media_type=m, headers=h)
        finally:
            for pth in rm:
                try:
                    os.remove(pth)
                except:
                    pass

    @r.get("/peek", dependencies=[need("mod")])
    def peek():
        if not Q:
            return Response(status_code=204)
        it = Q.popleft()
        if not it.get("id"):
            it["id"] = uuid.uuid4().hex[:8]
        Q.appendleft(it)
        return dict(it)

    @r.delete("/queue/{qid}", dependencies=[need("mod")])
    def queue_delete(qid: str):
        if not qid:
            raise HTTPException(400, "bad id")
        n = 0
        tmp = []
        while Q:
            it = Q.popleft()
            if str(it.get("id")) == str(qid):
                n += 1
            else:
                tmp.append(it)
        for it in tmp:
            Q.append(it)
        return {"deleted": n}

    @r.post("/panel/login")
    async def panel_login(req: Request):
        import tts as eng

        if not eng.auth_enabled():
            raise HTTPException(400, "auth disabled")
        j = await req.json()
        role = (j.get("role") or "").strip()
        key = (j.get("key") or "").strip()
        if role not in ("admin", "mod"):
            raise HTTPException(400, "bad role")
        if not eng.auth_ok(role, key):
            raise HTTPException(401, "invalid key")
        if role == "admin":
            req.session["admin"] = True
            req.session["mod"] = True
            req.session["tts"] = True
            req.session["push"] = True
            req.session["pull"] = True
        else:
            req.session["mod"] = True
            req.session["tts"] = True
        return {"ok": True, "role": role}

    @r.post("/panel/logout")
    async def panel_logout(req: Request):
        req.session.clear()
        return {"ok": True}

    @r.get("/panel/status")
    def panel_status(req: Request):
        return {
            "admin": bool(req.session.get("admin")),
            "mod": bool(req.session.get("mod")),
            "tts": bool(req.session.get("tts")),
            "push": bool(req.session.get("push")),
            "pull": bool(req.session.get("pull")),
        }

    @r.post("/mod/mask", dependencies=[need("tts")])
    async def mod_mask(req: Request):
        j = await req.json()
        text = (j.get("text") or "").strip()
        if not text:
            return {"masked": "", "flags": {"urls": 0, "emojis": 0, "slurs": 0}}
        if not eng.mod_enabled():
            return {"masked": text, "flags": {"urls": 0, "emojis": 0, "slurs": 0}}
        masked, flags = eng.moder.filter(text, mode="mask")
        return {"masked": masked, "flags": flags}

    @r.get("/healthz")
    def healthz():
        return eng.health()

    @r.get("/voices", dependencies=[need("tts")])
    def voices():
        return eng.voices()

    @r.post("/reload", dependencies=[need("admin")])
    def reload_voices():
        return {"reloaded": eng.reload()}

    @r.get("/aliases", dependencies=[need("admin")])
    def get_aliases():
        return eng.get_aliases()

    @r.post("/aliases", dependencies=[need("admin")])
    async def set_alias(req: Request):
        j = await req.json()
        n = (j.get("name") or "").strip().lower()
        v = (j.get("voice") or "").strip()
        if not n or not v:
            raise HTTPException(400, "bad alias")
        eng.set_alias(n, v)
        return eng.get_aliases()

    @r.delete("/aliases/{name}", dependencies=[need("admin")])
    def del_alias(name):
        eng.del_alias((name or "").strip().lower())
        return eng.get_aliases()

    @r.post("/tts_batch", dependencies=[need("tts")])
    async def tts_batch(req: Request):
        j = await req.json()
        b, m, h = eng.tts_batch(j)
        return Response(content=b, media_type=m, headers=h)

    @r.post("/tts", dependencies=[need("tts")])
    async def tts_post(req: Request):
        j = await req.json()
        b, m, h = eng.tts(j)
        return Response(content=b, media_type=m, headers=h)

    @r.get("/tts", dependencies=[need("tts")])
    def tts_get(
        text,
        voice=None,
        format=None,
        length_scale=None,
        noise_scale=None,
        noise_w=None,
        sentence_silence=None,
        normalize=None,
        bitrate=None,
        speaker_id=None,
        preset=None,
    ):
        q = {k: v for k, v in locals().items()}
        b, m, h = eng.tts(q)
        return Response(content=b, media_type=m, headers=h)

    @r.get("/metrics")
    def metrics():
        return eng.metrics()

    @r.post("/push", dependencies=[need("push")])
    async def push(req: Request):
        try:
            j = await req.json()
        except Exception:
            raise HTTPException(400, "invalid json")
        t = (j.get("text") or "").strip()
        if not t:
            raise HTTPException(400, "text required")
        mx = eng.cfg.get("max_text_chars", 500)
        if len(t) > mx:
            t = t[:mx]
        j["text"] = t
        # assign id for deletion
        import uuid

        j["id"] = j.get("id") or uuid.uuid4().hex[:8]
        Q.append(j)
        return {"ok": True, "id": j["id"], "queued": len(Q)}

    @r.delete("/queue/{qid}", dependencies=[need("mod")])
    def queue_delete(qid: str):
        if not qid:
            raise HTTPException(400, "bad id")
        if not Q:
            return {"deleted": 0}
        n = 0
        tmp = []
        while Q:
            item = Q.popleft()
            if str(item.get("id")) == str(qid):
                n += 1
                continue
            tmp.append(item)
        for it in tmp:
            Q.append(it)
        return {"deleted": n}

    @r.get("/pull", dependencies=[need("pull")])
    def pull():
        if not Q:
            return Response(status_code=204)
        it = Q.popleft()
        if not it.get("id"):
            it["id"] = uuid.uuid4().hex[:8]
        return it

    @r.get("/overlay")
    def overlay():
        # TODO: To add later...
        return HTMLResponse(html)

    @r.get("/mod/list", dependencies=[need("mod")])
    def mod_list():
        if not eng.mod_enabled():
            raise HTTPException(400, "moderation disabled")
        return {"terms": eng.mod_list()}

    @r.post("/mod/add", dependencies=[need("mod")])
    async def mod_add(req: Request):
        if not eng.mod_enabled():
            raise HTTPException(400, "moderation disabled")
        j = await req.json()
        term = (j.get("term") or "").strip()
        if not term:
            raise HTTPException(400, "term required")
        return eng.mod_add(term)

    @r.post("/mod/remove", dependencies=[need("mod")])
    async def mod_remove(req: Request):
        if not eng.mod_enabled():
            raise HTTPException(400, "moderation disabled")
        j = await req.json()
        term = (j.get("term") or "").strip()
        if not term:
            raise HTTPException(400, "term required")
        return eng.mod_remove(term)

    @r.post("/mod/reload", dependencies=[need("mod")])
    def mod_reload():
        if not eng.mod_enabled():
            raise HTTPException(400, "moderation disabled")
        return eng.mod_reload()

    @r.get("/mod/test", dependencies=[need("mod")])
    def modtest(text: str):
        tx = text
        if eng.mod_enabled():
            tx2, flags = eng.moder.filter(tx)
        else:
            tx2, flags = tx, {"urls": 0, "emojis": 0, "slurs": 0}
        return {"in": tx, "out": tx2, "flags": flags}

    app.include_router(r)
    if os.path.isdir("public"):
        app.mount("/", StaticFiles(directory="public", html=True), name="ui")
    return app
