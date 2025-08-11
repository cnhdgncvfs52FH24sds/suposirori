import re
import base64
from pathlib import Path
from datetime import datetime
from flask import Flask, send_from_directory, Response, jsonify, abort, stream_with_context

# === Rutas base (relativas) ===
BASE_DIR   = Path(__file__).resolve().parents[1]
PUBLIC_DIR = BASE_DIR / "public"
PARTS_DIR  = BASE_DIR / "server" / "parts"

# === Config payload ===
PARTS_COUNT     = 10
PART_PREFIX     = "payload_encoded_part_"
PART_SUFFIX     = ".txt"
FINAL_ZIP_NAME  = "anabel_y_marco_completo.zip"
XOR_KEY         = 123  # clave XOR inversa

# Trozos: chars de Base64 a procesar por iteración (múltiplos de 4)
B64_CHUNK_CHARS = 256 * 1024  # 256 KiB de texto base64
# Nota: el binario emitido por iteración será ~192 KiB aprox.

app = Flask(__name__, static_folder=None)

# -------- Utilidades --------
def ensure_dirs():
    PARTS_DIR.mkdir(parents=True, exist_ok=True)

def parts_exist() -> bool:
    for i in range(1, PARTS_COUNT + 1):
        if not (PARTS_DIR / f"{PART_PREFIX}{i}{PART_SUFFIX}").exists():
            return False
    return True

def _extract_b64_block(text: str) -> str:
    """
    Extrae SOLO el contenido entre comillas triples:
      ''' ... '''  o  \"\"\" ... \"\"\"
    Si no hay comillas, devuelve el texto completo.
    Elimina whitespace para dejar base64 limpio.
    """
    triple_pat = re.compile(r"'''(.*?)'''|\"\"\"(.*?)\"\"\"", re.DOTALL)
    m = triple_pat.search(text)
    payload = (m.group(1) or m.group(2)) if m else text
    return re.sub(r"\s+", "", payload)

def iter_b64_decoded_bytes():
    """
    Lee los 10 TXT secuencialmente y decodifica Base64 en streaming,
    manejando el 'carry' para que cada decode sea múltiplo de 4.
    Rinde bytes ya decodificados (AÚN sin XOR).
    """
    ensure_dirs()
    if not parts_exist():
        missing = [f"{PART_PREFIX}{i}{PART_SUFFIX}" for i in range(1, PARTS_COUNT + 1)
                   if not (PARTS_DIR / f"{PART_PREFIX}{i}{PART_SUFFIX}").exists()]
        raise FileNotFoundError(f"Faltan partes: {missing}")

    carry = ""  # restos de chars base64 que no forman múltiplo de 4
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
            usable_len = (len(buf) // 4) * 4   # mayor múltiplo de 4
            if usable_len:
                to_decode = buf[:usable_len]
                carry     = buf[usable_len:]
                try:
                    yield base64.b64decode(to_decode, validate=False)
                except Exception as e:
                    raise RuntimeError(f"Error Base64 en parte {i}: {e}")
            else:
                carry = buf

    # decodificar lo que quedó pendiente (con padding si hace falta)
    if carry:
        pad = "=" * (-len(carry) % 4)
        try:
            yield base64.b64decode(carry + pad, validate=False)
        except Exception as e:
            raise RuntimeError(f"Error Base64 (final): {e}")

def generator_zip_bytes():
    """
    Aplica XOR por bloques y hace yield del ZIP final en streaming (RAM baja).
    """
    key = XOR_KEY
    for decoded in iter_b64_decoded_bytes():
        block = bytearray(decoded)
        for j in range(len(block)):
            block[j] ^= key
        yield bytes(block)

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
    return jsonify({
        "server_time": datetime.utcnow().isoformat() + "Z",
        "parts_present": parts_exist(),
        "zip_name": FINAL_ZIP_NAME
    })

# -------- Descarga (streaming) --------
@app.get("/api/download/fullzip")
@app.get("/api/descargar/fullzip")  # alias en español
def download_fullzip():
    try:
        gen = generator_zip_bytes()
    except FileNotFoundError as e:
        abort(404, description=str(e))
    except Exception as e:
        abort(500, description=f"Error preparando descarga: {e}")

    headers = {
        "Content-Type": "application/zip",
        "Content-Disposition": f'attachment; filename="{FINAL_ZIP_NAME}"',
        "Cache-Control": "no-store, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
        "X-Accel-Buffering": "no",  # sugiere no bufferizar en proxies
    }
    return Response(stream_with_context(gen), headers=headers)

if __name__ == "__main__":
    # pruebas locales
    app.run(host="0.0.0.0", port=5000)
