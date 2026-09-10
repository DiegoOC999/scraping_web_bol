"""
panel.py — Panel de administración local (Fase 1.5 del roadmap).

Un panel web mínimo para no tener que abrir la terminal cada vez:
- Ves las cadenas Shopify configuradas (config_cadenas.json).
- Por cadena, un botón "Descubrir categorías" te trae TODAS las categorías
  reales del sitio (vía collections.json) con su conteo de productos, para
  que elijas cuáles scrapear sin copiar URLs a mano.
- Marcas las que quieres, apretás "Scrapear seleccionadas" y el panel
  corre scraping -> carga -> matchear -> export_json solo, en el orden
  correcto, y te muestra el log en vivo.
- Las categorías que elijas se guardan en config_cadenas.json, así la
  próxima corrida de run_all.py (o del cron) ya las incluye sin que
  vuelvas a tocar el panel.

Esto corre SOLO en tu máquina (no es para exponer a internet tal cual —
no tiene login). Es la base sobre la que se puede construir un dashboard
real más adelante si el negocio lo justifica.

Uso:
    pip install flask
    python panel.py
    abre http://localhost:5050
"""
import json
import subprocess
import sys
import threading
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request

import scraper_shopify

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config_cadenas.json"

app = Flask(__name__)

# Estado del último run, en memoria (simple a propósito: un solo usuario, un solo proceso)
estado = {"corriendo": False, "log": []}


def cargar_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def guardar_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def log(msg: str):
    print(msg, flush=True)
    estado["log"].append(msg)
    estado["log"] = estado["log"][-300:]  # no crecer sin límite


def correr_pipeline(cadenas_filtro: list[str] | None):
    """Corre run_all.py como subproceso y va agregando su output al log
    en vivo. Subproceso (no import directo) para que un error de un sitio
    no tumbe el panel entero."""
    estado["corriendo"] = True
    estado["log"] = []
    try:
        cmd = [sys.executable, "run_all.py"]
        if cadenas_filtro and len(cadenas_filtro) == 1:
            cmd += ["--solo", cadenas_filtro[0]]
        log(f"Ejecutando: {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd, cwd=str(BASE_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        for line in proc.stdout:
            log(line.rstrip())
        proc.wait()
        log(f"--- Terminado (código {proc.returncode}) ---")
    except Exception as e:
        log(f"ERROR fatal: {e}")
    finally:
        estado["corriendo"] = False


@app.route("/")
def home():
    return render_template_string(HTML, cfg=cargar_config())


@app.route("/api/descubrir")
def api_descubrir():
    sitio_base = request.args.get("sitio_base")
    if not sitio_base:
        return jsonify({"error": "falta sitio_base"}), 400
    try:
        cols = scraper_shopify.listar_colecciones(sitio_base)
        cols.sort(key=lambda c: -c["productos"])
        return jsonify(cols)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/guardar_colecciones", methods=["POST"])
def api_guardar_colecciones():
    data = request.get_json()
    cadena = data["cadena"]
    handles = data["handles"]
    cfg = cargar_config()
    for chain_cfg in cfg["shopify"]:
        if chain_cfg["cadena"] == cadena:
            chain_cfg["colecciones"] = handles
    guardar_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/correr", methods=["POST"])
def api_correr():
    if estado["corriendo"]:
        return jsonify({"error": "ya hay una corrida en curso"}), 409
    data = request.get_json(silent=True) or {}
    cadenas = data.get("cadenas")  # None = todas
    threading.Thread(target=correr_pipeline, args=(cadenas,), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/estado")
def api_estado():
    return jsonify(estado)


HTML = """
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Price Intelligence — Panel</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; background: #0f1117; color: #e8e8e8; }
  h1 { font-size: 1.4rem; }
  .cadena { border: 1px solid #2a2d3a; border-radius: 10px; padding: 1rem; margin-bottom: 1rem; background: #161923; }
  .cadena h2 { margin: 0 0 .5rem; font-size: 1.1rem; }
  .colecciones { max-height: 220px; overflow-y: auto; margin: .5rem 0; }
  label { display: block; padding: .15rem 0; font-size: .9rem; }
  button { background: #4f7cff; color: white; border: none; padding: .5rem 1rem; border-radius: 6px; cursor: pointer; font-size: .9rem; }
  button:hover { background: #3d63d9; }
  button.secundario { background: #2a2d3a; }
  button.secundario:hover { background: #363a4a; }
  #log { background: #000; color: #7fff7f; font-family: monospace; font-size: .8rem; padding: 1rem; border-radius: 8px; height: 280px; overflow-y: auto; white-space: pre-wrap; margin-top: 1.5rem; }
  .badge { color: #9aa0b0; font-size: .8rem; }
</style>
</head>
<body>
  <h1>Price Intelligence — Panel de scraping</h1>
  <p class="badge">Cadenas configuradas: {{ cfg.shopify|length }}. Marca categorías nuevas y corre el pipeline completo (scraping → carga → match → export) con un click.</p>

  {% for chain in cfg.shopify %}
  <div class="cadena" data-cadena="{{ chain.cadena }}" data-sitio="{{ chain.sitio_base }}">
    <h2>{{ chain.cadena }} <span class="badge">({{ chain.sitio_base }})</span></h2>
    <div class="badge">Categorías activas hoy: {{ chain.colecciones|join(', ') }}</div>
    <div class="colecciones" data-loaded="false"><span class="badge">Sin descubrir aún — click "Descubrir categorías"</span></div>
    <button class="secundario" onclick="descubrir('{{ chain.cadena }}', '{{ chain.sitio_base }}')">Descubrir categorías</button>
    <button class="secundario" onclick="guardarSeleccion('{{ chain.cadena }}')">Guardar selección</button>
    <button onclick="correr(['{{ chain.cadena }}'])">Scrapear esta cadena ahora</button>
  </div>
  {% endfor %}

  <h2 style="font-size:1.1rem;">Cadenas con navegador (más lentas, sin descubrimiento automático)</h2>
  {% for chain in cfg.navegador %}
  <div class="cadena">
    <h2>{{ chain.cadena }}</h2>
    <div class="badge">URLs configuradas: {{ chain.urls|join(', ') }}</div>
    <div class="badge">Para agregar una categoría nueva, edita config_cadenas.json a mano (sección "navegador") y agrega la URL de la categoría al array "urls".</div>
    <button onclick="correr(['{{ chain.cadena }}'])">Scrapear esta cadena ahora</button>
  </div>
  {% endfor %}

  <button onclick="correr(null)" style="font-size:1rem;padding:.7rem 1.2rem;">▶ Correr TODO el pipeline (las 5 cadenas)</button>

  <div id="log">Esperando...</div>

<script>
async function descubrir(cadena, sitio) {
  const div = document.querySelector(`.cadena[data-cadena="${cadena}"] .colecciones`);
  div.innerHTML = '<span class="badge">Cargando...</span>';
  const r = await fetch(`/api/descubrir?sitio_base=${encodeURIComponent(sitio)}`);
  const cols = await r.json();
  if (cols.error) { div.innerHTML = `<span class="badge">Error: ${cols.error}</span>`; return; }
  const activas = new Set(
    Array.from(document.querySelectorAll(`.cadena[data-cadena="${cadena}"]`))[0]
      .querySelector('.badge').textContent.split(': ')[1]?.split(', ') || []
  );
  div.innerHTML = cols.map(c => `
    <label><input type="checkbox" value="${c.handle}" ${activas.has(c.handle) ? 'checked' : ''}> ${c.titulo} <span class="badge">(${c.productos} productos, /${c.handle})</span></label>
  `).join('');
  div.dataset.loaded = 'true';
}

async function guardarSeleccion(cadena) {
  const div = document.querySelector(`.cadena[data-cadena="${cadena}"] .colecciones`);
  const handles = Array.from(div.querySelectorAll('input:checked')).map(i => i.value);
  await fetch('/api/guardar_colecciones', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({cadena, handles}),
  });
  alert(`Guardado: ${cadena} ahora scrapea [${handles.join(', ')}]`);
  location.reload();
}

async function correr(cadenas) {
  const r = await fetch('/api/correr', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({cadenas}),
  });
  if (!r.ok) { alert((await r.json()).error); return; }
  poll();
}

async function poll() {
  const r = await fetch('/api/estado');
  const e = await r.json();
  document.getElementById('log').textContent = e.log.join('\\n') || 'Esperando...';
  document.getElementById('log').scrollTop = 999999;
  if (e.corriendo) setTimeout(poll, 1000);
}
poll();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(port=5050, debug=False)
