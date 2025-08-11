import re, base64, os, tempfile
from pathlib import Path
from datetime import datetime
from flask import Flask, send_from_directory, Response, jsonify, abort, stream_with_context, send_file, request

# === Rutas base (relativas) ===
BASE_DIR   = Path(__file__).resolve().parents[1]
PUBLIC_DIR = BASE_DIR / "public"
PARTS_DIR  = BASE_DIR / "server" / "parts"

# === Config payload ===
PARTS_COUNT     = 10
PART_PREFIX     = "payload_encoded_part_"
PART_SUFFIX     = ".txt"
FINAL_ZIP_NAME  = "anabel_y_marco_completo.zip"
XOR_KEY         = 123

# Rendimiento
B64_CHUNK_CHARS = 512 * 1024      # ↑ chunks más grandes => menos overhead
CACHE_TO_TMP    = True            # ✅ cachear en /tmp tras la primera vez
TMP_DIR         = Path(os.getenv("TMPDIR", "/tmp"))

app = Flask(__name__, static_folder=None)

# -------- Utilidades --------
def ensure_dirs():
    PARTS_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)

def parts_exist() -> bool:
    for i in range(1, PARTS_COUNT + 1):
        if not (PARTS_DIR / f"{PART_PREFIX}{i}{PART_SUFFIX}").exists():
            return False
    return True

def _extract_b64_block(text: str) -> str:
    triple_pat = re.compile(r"'''(.*?)'''|\"\"\"(.*?)\"\"\"", re.DOTALL)
    m = triple_pat.search(text)
    payload = (m.group(1) or m.group(2)) if m else text
    return re.sub(r"\s+", "", payload)

def iter_b64_decoded_bytes():
    ensure_dirs()
    if not parts_exist():
        missing = [f"{PART_PREFIX}{i}{PART_SUFFIX}" for i in range(1, PARTS_COUNT + 1)
                   if not (PARTS_DIR / f"{PART_PREFIX}{i}{PART_SUFFIX}").exists()]
        raise FileNotFoundError(f"Faltan partes: {missing}")

    carry = ""
    for i in range(1, PARTS_COUNT + 1):
        p = PARTS_DIR / f"{PART_PREFIX}{i}{PART_SUFFIX}"
        text = p.read_text(encoding="utf-8", errors="ignore")
        payload = _extract_b64_block(text)

        pos = 0
        L = len(payload)
        while pos < L:
            chunk = payload[pos:pos + B64_CHUNK_CHARS]
            pos += B64_CHUNK_CHARS

            buf = carry + chunk
            usable_len = (len(buf) // 4) * 4
            if usable_len:
                to_decode = buf[:usable_len]
                carry     = buf[usable_len:]
                try:
                    yield base64.b64decode(to_decode, validate=False)
                except Exception as e:
                    raise RuntimeError(f"Error Base64 en parte {i}: {e}")
            else:
                carry = buf

    if carry:
        pad = "=" * (-len(carry) % 4)
        try:
            yield base64.b64decode(carry + pad, validate=False)
        except Exception as e:
            raise RuntimeError(f"Error Base64 (final): {e}")

def generator_zip_bytes():
    key = XOR_KEY
    for decoded in iter_b64_decoded_bytes():
        block = bytearray(decoded)
        for j in range(len(block)):
            block[j] ^= key
        yield bytes(block)

def cache_key_from_parts() -> str:
    """Crea una firma simple por fechas+tamaños de las partes (para invalidar caché si cambian)."""
    meta = []
    for i in range(1, PARTS_COUNT + 1):
        p = PARTS_DIR / f"{PART_PREFIX}{i}{PART_SUFFIX}"
        st = p.stat()
        meta.append(f"{p.name}:{st.st_size}:{int(st.st_mtime)}")
    return str(abs(hash("|".join(meta))))

def get_cached_zip_path() -> Path:
    return TMP_DIR / f"{cache_key_from_parts()}_{FINAL_ZIP_NAME}"

def build_cache_if_missing() -> Path:
    """Construye el ZIP en /tmp si no existe. Escritura atómica."""
    out_path = get_cached_zip_path()
    if out_path.exists():
        return out_path

    # construir a archivo temporal y luego renombrar
    with tempfile.NamedTemporaryFile(dir=TMP_DIR, delete=False) as tmp:
        tmp_path = Path(tmp.name)
        try:
            for decoded in iter_b64_decoded_bytes():
                block = bytearray(decoded)
                for j in range(len(block)):
                    block[j] ^= XOR_KEY
                tmp.write(block)
            tmp.flush()
            os.fsync(tmp.fileno())
        except Exception:
            try: tmp.close()
            except: pass
            try: tmp_path.unlink(missing_ok=True)
            except: pass
            raise
    tmp_path.replace(out_path)
    return out_path

# -------- Frontend estático --------
@app.get("/")
def home():
    return send_from_directory(PUBLIC_DIR, "index.html")

@app.get("/<path:path>")
def assets(path: str):
    return send_from_directory(PUBLIC_DIR, path)

# -------- Estado --------
@app.get("/api/status")
def status():
    ensure_dirs()
    have_cache = get_cached_zip_path().exists() if CACHE_TO_TMP else False
    return jsonify({
        "server_time": datetime.utcnow().isoformat() + "Z",
        "parts_present": parts_exist(),
        "zip_name": FINAL_ZIP_NAME,
        "cached": have_cache
    })

# -------- Descarga --------
@app.get("/api/download/fullzip")
@app.get("/api/descargar/fullzip")
def download_fullzip():
    ensure_dirs()
    headers = {
        "Content-Type": "application/zip",
        "Content-Disposition": f'attachment; filename="{FINAL_ZIP_NAME}"',
        "Cache-Control": "no-store, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
        "Content-Encoding": "identity",
        "X-Accel-Buffering": "no",
    }

    if CACHE_TO_TMP:
        try:
            path = build_cache_if_missing()  # primera vez construye; luego sirve archivo
        except FileNotFoundError as e:
            abort(404, description=str(e))
        except Exception as e:
            abort(500, description=f"Error preparando caché: {e}")

        # send_file con conditional=True permite Range/206 (reanudable) y usa file wrapper eficiente
        resp = send_file(path, as_attachment=True, download_name=FINAL_ZIP_NAME, conditional=True)
        for k, v in headers.items(): resp.headers.setdefault(k, v)
        # ETag/Last-Modified los pone Flask automáticamente
        return resp

    # Fallback: streaming puro (sin cache)
    try:
        gen = generator_zip_bytes()
    except FileNotFoundError as e:
        abort(404, description=str(e))
    except Exception as e:
        abort(500, description=f"Error preparando descarga: {e}")

    return Response(stream_with_context(gen), headers=headers)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
