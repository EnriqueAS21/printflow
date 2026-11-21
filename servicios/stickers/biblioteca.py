"""Biblioteca de diseños de sticker compartida por todos los usuarios, guardada en disco."""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .disenos import InfoDiseno, cargar_diseno


ORDENES = ("reciente", "nombre", "uso")
MAX_NOMBRE = 80

_ID = re.compile(r"[0-9a-f]{32}")
_CAMPOS_INFO = (
    "id", "nombre", "ruta", "ancho_px", "alto_px", "tiene_alfa", "dpi", "contenido_px", "radio_px",
)


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalizar(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in descompuesto if not unicodedata.combining(c)).casefold()


def _escribir_json(ruta: Path, datos: dict) -> None:
    temporal = ruta.with_name(ruta.name + ".tmp")
    temporal.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporal, ruta)


def _ruta_json(raiz: Path, id_diseno: str) -> Path:
    if not isinstance(id_diseno, str) or not _ID.fullmatch(id_diseno):
        raise LookupError("El diseño no existe.")
    ruta = raiz / f"{id_diseno}.json"
    if not ruta.is_file():
        raise LookupError("El diseño no existe.")
    return ruta


def _leer(ruta: Path) -> dict:
    return json.loads(ruta.read_text(encoding="utf-8"))


def _todos(raiz: Path) -> list[dict]:
    if not raiz.is_dir():
        return []
    registros = []
    for ruta in raiz.glob("*.json"):
        if not _ID.fullmatch(ruta.stem):
            continue
        try:
            registros.append(_leer(ruta))
        except (OSError, ValueError):
            continue
    return registros


def _validar_nombre(nombre) -> str:
    if not isinstance(nombre, str):
        raise ValueError("El nombre debe ser texto.")
    nombre = nombre.strip()
    if not 1 <= len(nombre) <= MAX_NOMBRE:
        raise ValueError(f"El nombre debe tener entre 1 y {MAX_NOMBRE} caracteres.")
    return nombre


def agregar(raiz, datos: bytes, nombre: str, id_usuario, nombre_usuario) -> dict:
    """Guarda un diseño; si el mismo archivo ya existe devuelve ese con "duplicado": True."""
    raiz = Path(raiz)
    sha256 = hashlib.sha256(datos).hexdigest()
    for registro in _todos(raiz):
        if registro.get("sha256") == sha256:
            return {**registro, "duplicado": True}

    nombre = (nombre or "").strip()[:MAX_NOMBRE] or "diseño"
    info = cargar_diseno(datos, nombre, raiz)
    registro = {
        **asdict(info),
        "contenido_px": list(info.contenido_px),
        "ruta": Path(info.ruta).name,
        "sha256": sha256,
        "subido_por": id_usuario,
        "subido_por_nombre": nombre_usuario,
        "fecha": _ahora(),
        "usos": 0,
        "ultimo_uso": None,
    }
    _escribir_json(raiz / f"{info.id}.json", registro)
    return {**registro, "duplicado": False}


def listar(raiz, buscar: str = "", orden: str = "reciente") -> list[dict]:
    """Diseños de la biblioteca filtrados por nombre (sin acentos ni mayúsculas)."""
    if orden not in ORDENES:
        raise ValueError(f"orden debe ser uno de: {', '.join(ORDENES)}.")
    registros = _todos(Path(raiz))
    termino = _normalizar(buscar or "").strip()
    if termino:
        registros = [r for r in registros if termino in _normalizar(r.get("nombre", ""))]
    if orden == "nombre":
        registros.sort(key=lambda r: _normalizar(r.get("nombre", "")))
    elif orden == "uso":
        registros.sort(key=lambda r: (r.get("usos", 0), r.get("ultimo_uso") or ""), reverse=True)
    else:
        registros.sort(key=lambda r: r.get("fecha", ""), reverse=True)
    return registros


def leer(raiz, id_diseno: str) -> dict:
    """Registro completo (json) de un diseño."""
    return _leer(_ruta_json(Path(raiz), id_diseno))


def obtener(raiz, id_diseno: str) -> InfoDiseno:
    raiz = Path(raiz)
    registro = _leer(_ruta_json(raiz, id_diseno))
    datos = {campo: registro[campo] for campo in _CAMPOS_INFO}
    datos["contenido_px"] = tuple(datos["contenido_px"])
    datos["ruta"] = str(raiz / Path(datos["ruta"]).name)
    return InfoDiseno(**datos)


def ruta_miniatura(raiz, id_diseno: str) -> Path:
    raiz = Path(raiz)
    _ruta_json(raiz, id_diseno)
    ruta = raiz / f"{id_diseno}_mini.png"
    if not ruta.is_file():
        raise LookupError("El diseño no tiene miniatura.")
    return ruta


def renombrar(raiz, id_diseno: str, nombre: str) -> dict:
    ruta = _ruta_json(Path(raiz), id_diseno)
    registro = _leer(ruta)
    registro["nombre"] = _validar_nombre(nombre)
    _escribir_json(ruta, registro)
    return registro


def borrar(raiz, id_diseno: str) -> None:
    raiz = Path(raiz)
    ruta = _ruta_json(raiz, id_diseno)
    registro = _leer(ruta)
    for archivo in (raiz / Path(registro.get("ruta", "")).name, raiz / f"{id_diseno}_mini.png", ruta):
        if archivo.is_file() and archivo.parent == raiz:
            archivo.unlink()


def marcar_uso(raiz, ids) -> None:
    raiz = Path(raiz)
    ahora = _ahora()
    for id_diseno in set(ids):
        try:
            ruta = _ruta_json(raiz, id_diseno)
        except LookupError:
            continue
        registro = _leer(ruta)
        registro["usos"] = int(registro.get("usos", 0)) + 1
        registro["ultimo_uso"] = ahora
        _escribir_json(ruta, registro)
