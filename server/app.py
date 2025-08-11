import re
import base64
from pathlib import Path
from datetime import datetime
from flask import Flask, send_from_directory, Response, jsonify, abort

# === Rutas base ===
BASE_DIR   = Path(__file__).resolve().parents[1]   # raíz del repo
PUBLIC_DIR = BASE_DIR / "public"
PARTS_DIR  = BASE_DIR / "server" / "parts"         # 10 TXT con base64 dentro de comillas triples

# === Partes/Nombres ===
PARTS_COUNT     = 10
PART_PREFIX     = "payload_encoded_part_"
PART_SUFFIX     = ".txt"
FINAL_ZIP_NAME  = "anabel_y_marco_completo.zip"
XOR_KEY         = 123  # <- la misma clave de tu script de ejemplo

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
    Lee los 10 TXT y concatena SOLO lo que está entre comillas triples (''' ... ''' o \"\"\" ... \"\"\").
    Elimina todo whitespace para que Base64 quede limpio.
    """
    ensure_dirs()
    if not parts_exist():
        missing = [f"{PART_PREFIX}{i}{PART_SUFFIX}" for i in range(1, PARTS_COUNT + 1)
                   if not (PARTS_DIR / f"{PART_PREFIX}{i}{PART_SUFFIX}").exists()]
        raise FileNotFoundError(f"Faltan partes: {missing}")

    chunks = []
    triple_pat = re.compile(r"'''(.*?)'''|\"\"\"(.*?)\"\"\"", re.DOTALL)
    for i in range(1, PARTS_COUNT + 1):
        p = PARTS_DIR / f"{PART_PREFIX}{i}{PART_SUFFIX}"
        text = p.read_text(encoding="utf-8", errors="ignore")
        matches = triple_pat.findall(text)
        if matches:
            # matches es lista de tuplas (m1, m2). Tomamos el que no esté vacío.
            for m1, m2 in matches:
                seg = m1 if m1 else m2
                chunks.append(seg)
        else:
            # Si no hay comillas triples, usamos TODO el archivo
            chunks.append(text)
    joined = "".join(chunks)
    # quitar todo whitespace (espacios/nuevas líneas/tab)
    joined = re.sub(r"\s+", "", joined)
    return joined

def generator_zip_bytes():
    """
    Produce los bytes del ZIP:
      parts (triple quotes) -> base64 decode -> XOR inverso (clave=123) -> yield por bloques.
    """
    b64_str = read_all_parts_b64()

    # Decodificar TODO el Base64 de una vez (como tu script de ejemplo)
    try:
        raw = base64.b64decode(b64_str)  # tolerate whitespace; ya limpiamos
    except Exception as e:
        raise RuntimeError(f"Error al decodificar Base64: {e}")

    # XOR inverso (clave fija)
    # Lo hacemos por bloques para no duplicar memoria
    key = XOR_KEY
    view = memoryview(raw)
    CHUNK = 1 << 20  # 1 MiB
    for off in range(0, len(view), CHUNK):
        chunk = bytearray(view[off:off+CHUNK])
        for j in range(len(chunk)):
            chunk[j] ^= key
        yield bytes(chunk)

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
    }
    return Response(gen, headers=headers)

if __name__ == "__main__":
    # pruebas locales
    app.run(host="0.0.0.0", port=5000)
