#!/usr/bin/env python3
import argparse, base64, textwrap
from pathlib import Path

def main():
    ap = argparse.ArgumentParser(description="Divide un ZIP en N partes Base64 dentro de comillas triples ('''...''').")
    ap.add_argument("zip_path", help="Ruta al archivo .zip origen")
    ap.add_argument("--out-dir", default="server/parts", help="Carpeta salida (default: server/parts)")
    ap.add_argument("--parts", type=int, default=10, help="Cantidad de partes (default: 10)")
    args = ap.parse_args()

    src = Path(args.zip_path).resolve()
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)

    b64 = base64.b64encode(src.read_bytes())  # bytes base64
    total = len(b64)

    # Reparto alineado a múltiplos de 4 (para no cortar quadlets)
    base = (total // args.parts) // 4 * 4
    sizes = [base] * args.parts
    used = base * args.parts
    rem = total - used
    i = 0
    while rem >= 4 and i < args.parts:
        sizes[i] += 4; rem -= 4; i += 1
    if rem:
        sizes[-1] += rem

    idx = 0
    for i, sz in enumerate(sizes, start=1):
        chunk = b64[idx:idx+sz]; idx += sz
        # Envolver en líneas de 76 para legibilidad
        lines = [chunk[j:j+76].decode("ascii") for j in range(0, len(chunk), 76)]
        body = "\n".join(lines)
        out = out_dir / f"payload_encoded_part_{i}.txt"
        out.write_text(f"'''{body}'''\n", encoding="utf-8")
        print(f"parte {i}: {out} ({len(chunk)} bytes b64)")
    print("Listo ✔")

if __name__ == "__main__":
    main()
