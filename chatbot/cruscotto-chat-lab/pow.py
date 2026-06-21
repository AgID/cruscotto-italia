"""Proof-of-Work challenge firmata HMAC, stateless. Anti-flood per /api/chat."""
import hmac, hashlib, time, secrets

def genera_challenge(secret: bytes, difficulty: int, ts: int = None) -> dict:
    ts = int(time.time()) if ts is None else ts
    rnd = secrets.token_hex(16)
    sig = hmac.new(secret, f"{ts}:{rnd}:{difficulty}".encode(), hashlib.sha256).hexdigest()
    return {"ts": ts, "rnd": rnd, "difficulty": difficulty, "sig": sig}

def _sig_ok(secret, ts, rnd, difficulty, sig) -> bool:
    atteso = hmac.new(secret, f"{ts}:{rnd}:{difficulty}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(atteso, str(sig))

def verifica_proof(secret, ts, rnd, difficulty, sig, counter, ttl=120, now=None):
    now = int(time.time()) if now is None else now
    if not _sig_ok(secret, ts, rnd, difficulty, sig):
        return False, "firma non valida"
    if now - int(ts) > ttl:
        return False, "challenge scaduta"
    if int(ts) - now > 5:
        return False, "timestamp nel futuro"
    h = hashlib.sha256(f"{ts}:{rnd}:{counter}".encode()).hexdigest()
    if not h.startswith("0" * int(difficulty)):
        return False, "proof non valida"
    return True, "ok"

def risolvi(ts, rnd, difficulty):  # riferimento client-side, per il test
    target = "0" * int(difficulty); c = 0
    while True:
        if hashlib.sha256(f"{ts}:{rnd}:{c}".encode()).hexdigest().startswith(target):
            return c
        c += 1

if __name__ == "__main__":
    sec = b"secret-di-test-non-produzione"
    for diff in (3, 4, 5):
        ch = genera_challenge(sec, diff)
        t0 = time.time(); c = risolvi(ch["ts"], ch["rnd"], diff); dt = (time.time()-t0)*1000
        ok, msg = verifica_proof(sec, ch["ts"], ch["rnd"], diff, ch["sig"], c)
        print(f"diff={diff}: risolto in {dt:6.0f}ms (counter={c})  valido={ok} [{msg}]")
    ch = genera_challenge(sec, 3); c = risolvi(ch["ts"], ch["rnd"], 3)
    print("sig manomessa ->", verifica_proof(sec, ch["ts"], ch["rnd"], 3, "ab"*32, c)[0], "(atteso False)")
    print("counter errato ->", verifica_proof(sec, ch["ts"], ch["rnd"], 3, ch["sig"], c+1)[0], "(atteso False)")
    print("scaduta        ->", verifica_proof(sec, ch["ts"], ch["rnd"], 3, ch["sig"], c, now=ch["ts"]+999)[0], "(atteso False)")
