#!/bin/bash
# PrintFlow - prepara el paquete para el servidor (correr en la Mac).
# Uso:
#   cd "<carpeta de la version a empaquetar>"
#   bash ../preparar_paquete.sh
#
# Revisa todo lo que fallo en el despliegue anterior ANTES de que llegue al servidor:
#  - que sea la version correcta (app.py con load_dotenv)
#  - que requirements.txt tenga TODAS las librerias (prueba en un entorno limpio)
#  - que esten los scripts de migracion y que compilen
#  - que no viaje el .env, ni venv/.venv, ni archivos de prueba
# y genera PrintFlow_AAAAMMDD_HHMM.zip en Descargas con PAQUETE.txt, MIGRACIONES.txt y VARIABLES.txt.
# El zip se arma desde una carpeta temporal: VARIABLES.txt y MIGRACIONES.txt del proyecto no se tocan.

set -u
RAIZ="$(pwd)"
FECHA="$(date +%Y%m%d_%H%M)"
ZIP="$HOME/Downloads/PrintFlow_${FECHA}.zip"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

rojo()  { printf "\033[31m%s\033[0m\n" "$*"; }
verde() { printf "\033[32m%s\033[0m\n" "$*"; }
paso()  { printf "\n\033[36m== %s\033[0m\n" "$*"; }
falla() { rojo "ERROR: $*"; exit 1; }

paso "1) Revisando que sea la carpeta correcta"
[ -f app.py ] && [ -f servidor.py ] && [ -f requirements.txt ] || falla "Corre esto dentro de la carpeta del proyecto (donde estan app.py, servidor.py y requirements.txt)."
grep -q "load_dotenv" app.py || falla "app.py no usa load_dotenv: esta no es la version actual."
[ -d scripts ] || falla "No existe la carpeta scripts/ (ahi van las migraciones)."
echo "  Carpeta: $RAIZ"

paso "2) Revisando que requirements.txt este completo (entorno limpio)"
python3 -m venv "$TMP/venv" || falla "No se pudo crear el entorno de prueba."
"$TMP/venv/bin/pip" install -q --upgrade pip >/dev/null
AGREGADAS=""
for lib in waitress python-dotenv "psycopg[binary]"; do
  nombre="${lib%%[*}"
  if ! grep -qiE "^${nombre}([=<>~\[ ]|$)" requirements.txt; then
    echo "$lib" >> requirements.txt
    AGREGADAS="$AGREGADAS $lib"
  fi
done
[ -n "$AGREGADAS" ] && echo "  Agregue a requirements.txt:$AGREGADAS"
"$TMP/venv/bin/pip" install -q -r requirements.txt || falla "pip install -r requirements.txt fallo. Revisa el mensaje de arriba."

"$TMP/venv/bin/python" - <<'PY' || falla "Faltan librerias en requirements.txt (lista arriba). Agregalas y vuelve a correr."
import ast, sys, pathlib, importlib.util
raiz = pathlib.Path(".")
omitir = {"venv", ".venv", "pyphomemo", "__pycache__", ".git", "instance", "static", "templates", "node_modules", "tests", "test"}
locales = {p.stem for p in raiz.glob("*.py")} | {p.name for p in raiz.iterdir() if p.is_dir()}
mods = set()
for f in raiz.rglob("*.py"):
    if any(parte in omitir for parte in f.parts) or f.name.startswith("test_") or f.name == "conftest.py":
        continue
    try:
        arbol = ast.parse(f.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  No se pudo leer {f}: {e}")
        sys.exit(1)
    for n in ast.walk(arbol):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            mods.add(n.module.split(".")[0])
externos = sorted(mods - set(sys.stdlib_module_names) - locales)
SOLO_WINDOWS = {"win32print", "win32api", "win32con", "win32ui", "win32com", "pywintypes", "winreg", "msvcrt", "wmi"}
faltan = [m for m in externos if m not in SOLO_WINDOWS and importlib.util.find_spec(m) is None]
win = [m for m in externos if m in SOLO_WINDOWS]
print("  Librerias externas usadas:", ", ".join(externos))
if win:
    print("  Solo Windows (no se prueban en Mac):", ", ".join(win))
    req = pathlib.Path("requirements.txt")
    if "pywin32" not in req.read_text(encoding="utf-8").lower():
        with req.open("a", encoding="utf-8") as fh:
            fh.write('\npywin32; sys_platform == "win32"\n')
        print('  Agregue a requirements.txt: pywin32; sys_platform == "win32" (solo se instala en Windows)')
if faltan:
    print("  FALTAN en requirements.txt:", ", ".join(faltan))
    sys.exit(1)
print("  ok: todas se instalan desde requirements.txt")
PY

paso "3) Compilando el codigo y revisando migraciones"
"$TMP/venv/bin/python" -m compileall -q -x '(venv|\.venv|pyphomemo)' . >/dev/null || falla "Hay errores de sintaxis (corre: python -m compileall .)."
[ -s MIGRACIONES.txt ] || falla "Falta MIGRACIONES.txt en el proyecto (o esta vacio). Crealo con el orden de las migraciones."
while read -r m || [ -n "$m" ]; do
  [ -z "$m" ] || [ -f "$m" ] || falla "MIGRACIONES.txt menciona $m, pero no existe."
done < MIGRACIONES.txt
sed 's/^/  /' MIGRACIONES.txt

paso "4) Variables de entorno que usa el codigo"
grep -rhoE "(environ\.get|environ\[|getenv)\(?[[:space:]]*['\"][A-Z][A-Z0-9_]+" --include='*.py' . \
  --exclude-dir=venv --exclude-dir=.venv --exclude-dir=pyphomemo 2>/dev/null \
  | grep -oE "[A-Z][A-Z0-9_]+$" | sort -u > "$TMP/VARIABLES.txt"
tr '\n' ' ' < "$TMP/VARIABLES.txt"; echo

paso "5) Pruebas"
if [ -n "${VIRTUAL_ENV:-}" ] && "$VIRTUAL_ENV/bin/python" -c "import pytest" 2>/dev/null; then
  "$VIRTUAL_ENV/bin/python" -m pytest -q 2>&1 | tail -n 4
  read -r -p "  Solo deben fallar las 2 conocidas de stickers. Continuar? (s/n) " r
  [[ "$r" =~ ^[sS] ]] || falla "Cancelado."
else
  echo "  (Sin entorno activo: corre antes 'python -m pytest -q' en tu entorno de siempre.)"
fi

paso "6) Armando el zip"
STAGING="$TMP/staging"
mkdir -p "$STAGING"
rsync -a --exclude='/venv/' --exclude='/.venv/' --exclude='__pycache__/' --exclude='.env' --exclude='.DS_Store' \
  --exclude='/.git/' --exclude='/instance/' --exclude='/static/uploads/' --exclude='pyphomemo/.venv/' \
  --exclude='*.log' --exclude='*.dump' --exclude='/.pytest_cache/' --exclude='*.pyc' ./ "$STAGING/" \
  || falla "No se pudo copiar el proyecto a la carpeta temporal."
cp "$TMP/VARIABLES.txt" "$STAGING/VARIABLES.txt"
{
  echo "Paquete PrintFlow"
  echo "Generado: $(date '+%d/%m/%Y %H:%M')"
  echo "Origen:   $RAIZ"
  echo "app.py:   $(shasum app.py | cut -c1-12)"
  echo "Migraciones: $(grep -c . MIGRACIONES.txt | tr -d ' ')"
} > "$STAGING/PAQUETE.txt"
rm -f "$ZIP"
(cd "$STAGING" && zip -rq "$ZIP" .) || falla "No se pudo armar el zip."

SUCIO="$(unzip -l "$ZIP" | awk '{print $4}' | grep -E '(^|/)\.env$|(^|/)\.?venv/' || true)"
[ -z "$SUCIO" ] || { rm -f "$ZIP"; falla "Se colaron archivos que no deben viajar: $SUCIO"; }
for f in PAQUETE.txt MIGRACIONES.txt VARIABLES.txt app.py servidor.py requirements.txt \
  static/vendor/bootstrap/css/bootstrap.min.css static/vendor/bootstrap/js/bootstrap.bundle.min.js \
  static/vendor/bootstrap-icons/bootstrap-icons.css static/vendor/bootstrap-icons/fonts/bootstrap-icons.woff2 \
  static/vendor/fonts/fonts.css static/vendor/fonts/inter-latin.woff2; do
  unzip -l "$ZIP" | awk '{print $4}' | grep -qx "$f" || { rm -f "$ZIP"; falla "El zip no trae $f"; }
done

verde ""
verde "LISTO: $ZIP ($(du -h "$ZIP" | cut -f1))"
echo "Pasa ese .zip y desplegar.ps1 a Descargas del servidor. NO lo descomprimas."
