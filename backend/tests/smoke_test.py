"""End-to-end smoke test against a *running* CureWise backend.

Not a unit test — it drives the real API over HTTP, so it needs the server up
and Postgres reachable. It deliberately needs **no provider API keys**: the AI
routes are asserted to degrade cleanly (calm message, no leaked internals)
rather than to succeed.

    # terminal 1
    uvicorn src.main:app --port 8000
    # terminal 2
    python tests/smoke_test.py

Exits non-zero if anything fails, so it works as a CI gate.
"""
import io, random, sys
import httpx

API = "http://127.0.0.1:8000"
c = httpx.Client(base_url=API, timeout=90.0)
ok = fails = 0

def chk(name, cond, detail=""):
    global ok, fails
    if cond:
        ok += 1; print(f"  \033[32m✓\033[0m {name}")
    else:
        fails += 1; print(f"  \033[31m✗\033[0m {name} — {detail}")

def png(size_bytes=None):
    """A tiny valid PNG, optionally padded to an arbitrary size."""
    import struct, zlib
    raw = b'\x00\xff\x00\x00'
    idat = zlib.compress(raw)
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    data = (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', idat) + chunk(b'IEND', b''))
    if size_bytes and size_bytes > len(data):
        data += b'\x00' * (size_bytes - len(data))
    return data

print("── health ──")
r = c.get("/health")
chk("GET /health -> 200", r.status_code == 200, r.text)

print("── auth ──")
U = f"smoke{random.randint(10000,99999)}"
r = c.post("/api/auth/signup", json={"username": U, "email": f"{U}@example.com", "password": "hunter2hunter2"})
chk("signup -> 200 + JWT", r.status_code == 200 and r.json().get("token"), f"{r.status_code} {r.text[:120]}")
TOKEN = r.json().get("token", "")
H = {"Authorization": f"Bearer {TOKEN}"}

r = c.post("/api/auth/signup", json={"username": U, "email": f"{U}@example.com", "password": "another12345"})
chk("duplicate signup -> 400", r.status_code == 400, f"{r.status_code} {r.text[:120]}")

r = c.post("/api/auth/login", json={"username": U, "password": "hunter2hunter2"})
chk("login correct -> 200", r.status_code == 200, f"{r.status_code} {r.text[:120]}")

r = c.post("/api/auth/login", json={"username": U, "password": "WRONG"})
chk("login wrong -> 400", r.status_code == 400, f"{r.status_code} {r.text[:120]}")

chk("GET /me authed -> 200", c.get("/api/auth/me", headers=H).status_code == 200)
chk("GET /me unauthed -> 401", c.get("/api/auth/me").status_code == 401)
chk("GET /me bad token -> 401", c.get("/api/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401)

print("── profile (profile_picture regression) ──")
c.put("/api/profile", headers=H, json={"first_name": "Ada", "profile_picture": "https://example.com/ada.png"})
c.put("/api/profile", headers=H, json={"first_name": "Ada", "last_name": "Lovelace", "phone": "555",
                                       "date_of_birth": None, "gender": "Female", "address": "London"})
p = c.get("/api/profile", headers=H).json()
chk("profile_picture survives a PUT that omits it", p.get("profile_picture") == "https://example.com/ada.png", p.get("profile_picture"))
chk("other fields still write", p.get("last_name") == "Lovelace", p.get("last_name"))
c.put("/api/profile", headers=H, json={"phone": None})
chk("explicit null still clears", c.get("/api/profile", headers=H).json().get("phone") is None)

print("── medical history ──")
r = c.post("/api/medical-history", headers=H, json={"conditions": "asthma", "allergies": "pollen", "notes": "mild"})
chk("POST history -> 200", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
chk("GET history returns it", len(c.get("/api/medical-history", headers=H).json()) == 1)

print("── screening registry ──")
models = c.get("/api/disease-detection", headers=H).json()
chk("registry lists 10 models", len(models) == 10, f"got {len(models)}")
chk("all models expose a chat prompt", all(m["has_chat"] for m in models))
keys = {m["key"] for m in models}
chk("includes the 3 undocumented models", {"fracture", "brain-tumor", "artery"} <= keys, keys)

print("── screening without weights (graceful 503, no path leak) ──")
r = c.post("/api/disease-detection/kidney/classify", headers=H,
           files={"image": ("t.png", png(), "image/png")})
chk("classify -> 503", r.status_code == 503, f"{r.status_code} {r.text[:120]}")
d = r.json().get("detail", "")
chk("503 does NOT leak the weights path", "/" not in d and ".h5" not in d, d)

print("── upload guards ──")
r = c.post("/api/disease-detection/kidney/classify", headers=H,
           files={"image": ("t.gif", b"GIF89a", "image/gif")})
chk("wrong content type -> 400", r.status_code == 400, f"{r.status_code} {r.text[:100]}")
r = c.post("/api/skin/acne-analysis", headers=H,
           files={"image": ("big.png", png(9 * 1024 * 1024), "image/png")})
chk("9MB image -> 413 (cap is 8MB)", r.status_code == 413, f"{r.status_code} {r.text[:120]}")
r = c.post("/api/medical-report/query", headers=H,
           files={"file": ("big.pdf", b"%PDF-1.4" + b"\x00" * (11 * 1024 * 1024), "application/pdf")})
chk("11MB PDF -> 413 (cap is 10MB)", r.status_code == 413, f"{r.status_code} {r.text[:120]}")
r = c.post("/api/skin/acne-analysis", headers=H, files={"image": ("empty.png", b"", "image/png")})
chk("empty file -> 400", r.status_code == 400, f"{r.status_code} {r.text[:100]}")

print("── AI routes degrade cleanly without keys ──")
r = c.post("/api/chat", headers=H, json={"query": "what is anemia?"})
d = r.json().get("detail", "")
chk("chat -> 500 with a calm message", r.status_code == 500, f"{r.status_code}")
chk("chat error does NOT leak internals", "api_key" not in d.lower() and "traceback" not in d.lower(), d)
r = c.post("/api/chat", headers=H, json={"query": "   "})
chk("empty query -> 400", r.status_code == 400, f"{r.status_code}")

print("── emergency (live OpenStreetMap, no key needed) ──")
# Overpass is a free public service that is regularly saturated. We control how
# we handle that, not its uptime — so accept either a real result or a clean
# 502, and fail only on anything else (a crash, a 500, a leaked traceback).
try:
    r = c.get("/api/emergency/hospitals", headers=H, params={"lat": 51.5074, "lng": -0.1278})
    chk("hospitals -> 200 or a clean 502", r.status_code in (200, 502), f"{r.status_code} {r.text[:120]}")
    if r.status_code == 200:
        hs = r.json().get("hospitals", [])
        chk("returns real hospitals near London", len(hs) > 0, f"got {len(hs)}")
        if hs:
            print(f"      e.g. {hs[0]['name']} — {hs[0]['address']}")
    else:
        print("      \033[33m!\033[0m Overpass upstream unavailable; graceful 502 path verified instead")
except httpx.ReadTimeout:
    print("      \033[33m!\033[0m Overpass upstream timed out; skipping (not our bug)")

print("── rate limiting ──")
codes = [c.post("/api/auth/login", json={"username": U, "password": "WRONG"}).status_code for _ in range(14)]
chk("login limiter eventually returns 429", 429 in codes, f"codes={codes}")
r = c.post("/api/auth/login", json={"username": U, "password": "hunter2hunter2"})
chk("429 carries Retry-After", r.status_code != 429 or r.headers.get("Retry-After"), r.headers.get("Retry-After"))

print(f"\n{ok} passed, {fails} failed")
sys.exit(1 if fails else 0)
