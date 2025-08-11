import re
import base64
from pathlib import Path
from datetime import datetime
from flask import Flask, send_from_directory, Response, jsonify, abort, stream_with_context

# === Rutas base (relativas, sin absolutos) ===
BASE_DIR   = Path(__file__).resolve().parents[1]   # raíz del repo
PUBLIC_DIR = BASE_DIR / "public"
PARTS_DIR  = BASE_DIR / "server" / "parts"         # 10 TXT con base64 dentro de comillas triples

# === Partes/Nombres ===
PARTS_COUNT     = 10
PART_PREFIX     = "payload_encoded_part_"
PART_SUFFIX     = ".txt"
FINAL_ZIP_NAME  = "anabel_y_marco_completo.zip"
XOR_KEY         = 123  # clave XOR inversa

app = Flask(__name__, static_folder=None)

def ensure_dirs():
    PARTS_DIR.mkdir(parents=True, exist_ok=True)

def parts_exist() -> bool:
    for i in range(1, PARTS_COUNT + 1):
        if not (PARTS_DIR / f"{PART_PREFIX}{i}{PART_SUFFIX}").exists():
            return False
    return True

def read_all_parts_b64() -> str:
    """
    Lee los 10 TXT y concatena SOLO lo entre comillas triples:
      ''' ... '''  o  \"\"\" ... \"\"\"
    Luego elimina todo whitespace para dejar Base64 limpio.
    """
    ensure_dirs()
    if not parts_exist():
        missing = [f"{PART_PREFIX}{i}{PART_SUFFIX}" for i in range(1, PARTS_COUNT + 1)
                   if not (PARTS_DIR / f"{PART_PREFIX}{i}{PART_SUFFIX}").exists()]
        raise FileNotFoundError(f"Faltan partes: {missing}")

    triple_pat = re.compile(r"'''(.*?)'''|\"\"\"(.*?)\"\"\"", re.DOTALL)
    chunks = []
    for i in range(1, PARTS_COUNT + 1):
        p = PARTS_DIR / f"{PART_PREFIX}{i}{PART_SUFFIX}"
        text = p.read_text(encoding="utf-8", errors="ignore")
        matches = triple_pat.findall(text)
        if matches:
            # matches = lista de tuplas (m1, m2); tomamos el no vacío
            for m1, m2 in matches:
                chunks.append(m1 if m1 else m2)
        else:
            # si no vienen con comillas triples, usamos todo
            chunks.append(text)
    joined = "".join(chunks)
    joined = re.sub(r"\s+", "", joined)  # quitar espacios/saltos
    return joined

def generator_zip_bytes():
    """
    Produce los bytes del ZIP como streaming:
      partes (triple quotes) -> base64 decode -> XOR inverso (clave=123)
    """
    b64_str = read_all_parts_b64()
    try:
        raw = base64.b64decode(b64_str)
    except Exception as e:
        raise RuntimeError(f"Error al decodificar Base64: {e}")

    key = XOR_KEY
    view = memoryview(raw)
    CHUNK = 256 * 1024  # 256 KiB por chunk para emitir seguido
    for off in range(0, len(view), CHUNK):
        block = bytearray(view[off:off+CHUNK])
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
        "X-Accel-Buffering": "no",   # sugiere no bufferizar en proxies
    }
    return Response(stream_with_context(gen), headers=headers)

if __name__ == "__main__":
    # Solo para pruebas locales
    app.run(host="0.0.0.0", port=5000)
