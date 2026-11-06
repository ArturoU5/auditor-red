#!/usr/bin/env python3
"""Auditor de inventario de red.

Consulta el inventario de dispositivos mediante una API REST sobre HTTPS
(NetBox), procesa los datos en JSON, genera un resumen de auditoria y
exporta el inventario a CSV, YAML y XML.

Uso:
    export NETBOX_URL="https://demo.netbox.dev/api/dcim/devices/"
    export NETBOX_TOKEN="<token>"
    python3 src/auditor.py --salida data

Modo sin conexion (pruebas):
    python3 src/auditor.py --archivo data/muestra.json --salida data
"""

import argparse
import csv
import json
import logging
import os
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter

import requests
import yaml

LOG = logging.getLogger("auditor")
CAMPOS = ["id", "nombre", "fabricante", "modelo", "rol", "sitio",
          "estado", "ip", "serie", "plataforma"]
SIN_DATO = "N/D"


class ErrorAPI(Exception):
    """Error controlado al consultar la API REST."""


# --------------------------------------------------------------------------
# Configuracion y logging
# --------------------------------------------------------------------------
def configurar_logging(ruta_log):
    """Registra los eventos en consola y en un archivo de log."""
    os.makedirs(os.path.dirname(ruta_log) or ".", exist_ok=True)
    formato = "%(asctime)s [%(levelname)s] %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=formato,
        handlers=[logging.FileHandler(ruta_log, encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)],
    )


def validar_https(url):
    """Rechaza cualquier URL que no use HTTPS."""
    if not url.lower().startswith("https://"):
        raise ErrorAPI("La URL debe usar HTTPS: " + url)
    return url


# --------------------------------------------------------------------------
# Consulta a la API REST
# --------------------------------------------------------------------------
def _get_con_reintentos(sesion, url, timeout, reintentos, espera=2):
    """Hace un GET y maneja los errores mas comunes de una API REST."""
    for intento in range(1, reintentos + 1):
        try:
            resp = sesion.get(url, timeout=timeout, verify=True)
        except requests.exceptions.SSLError as exc:
            # No se desactiva la verificacion del certificado.
            raise ErrorAPI("Error de certificado SSL: %s" % exc)
        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError) as exc:
            LOG.warning("Fallo de red (intento %d/%d): %s",
                        intento, reintentos, exc.__class__.__name__)
            if intento == reintentos:
                raise ErrorAPI("No se pudo conectar con el servidor.")
            time.sleep(espera * intento)
            continue

        codigo = resp.status_code
        if codigo == 200:
            return resp
        if codigo == 401:
            raise ErrorAPI("401 No autorizado: token ausente o invalido.")
        if codigo == 403:
            raise ErrorAPI("403 Prohibido: el token no tiene permisos.")
        if codigo == 404:
            raise ErrorAPI("404 No encontrado: revise la URL del endpoint.")
        if codigo == 429 or codigo >= 500:
            retraso = espera * intento
            if codigo == 429 and resp.headers.get("Retry-After", "").isdigit():
                retraso = int(resp.headers["Retry-After"])
            LOG.warning("HTTP %d (intento %d/%d). Reintentando en %ds.",
                        codigo, intento, reintentos, retraso)
            if intento == reintentos:
                raise ErrorAPI("HTTP %d tras %d intentos." % (codigo, reintentos))
            time.sleep(retraso)
            continue
        raise ErrorAPI("Respuesta inesperada: HTTP %d" % codigo)
    raise ErrorAPI("No se obtuvo respuesta.")


def consultar_api(url, token, timeout=10, reintentos=3):
    """Descarga todos los dispositivos recorriendo la paginacion."""
    validar_https(url)
    if not token:
        raise ErrorAPI("Falta el token (variable NETBOX_TOKEN).")

    sesion = requests.Session()
    sesion.headers.update({
        "Authorization": "Token " + token,
        "Accept": "application/json",
    })
    dispositivos = []
    siguiente = url
    while siguiente:
        LOG.info("Consultando: %s", siguiente)
        resp = _get_con_reintentos(sesion, siguiente, timeout, reintentos)
        try:
            datos = resp.json()
        except ValueError:
            raise ErrorAPI("La respuesta no es un JSON valido.")
        if "results" not in datos:
            raise ErrorAPI("El JSON no contiene la clave 'results'.")
        dispositivos.extend(datos["results"])
        siguiente = datos.get("next")
        if siguiente and siguiente.startswith("http://"):
            LOG.warning("La paginacion devolvio HTTP; se fuerza HTTPS.")
            siguiente = "https://" + siguiente[len("http://"):]
    LOG.info("Dispositivos recibidos: %d", len(dispositivos))
    return dispositivos


def cargar_json_local(ruta):
    """Lee un JSON local con el mismo formato de la API (modo pruebas)."""
    with open(ruta, encoding="utf-8") as archivo:
        datos = json.load(archivo)
    return datos["results"] if isinstance(datos, dict) else datos


# --------------------------------------------------------------------------
# Procesamiento y analisis
# --------------------------------------------------------------------------
def _anidado(dato, *claves, defecto=SIN_DATO):
    """Obtiene un valor dentro de diccionarios anidados sin fallar."""
    for clave in claves:
        if not isinstance(dato, dict):
            return defecto
        dato = dato.get(clave)
    return dato if dato not in (None, "") else defecto


def normalizar_dispositivo(crudo):
    """Convierte un dispositivo de la API en un diccionario plano."""
    rol = crudo.get("role") or crudo.get("device_role")  # NetBox 4.x / 3.x
    ip = crudo.get("primary_ip4") or crudo.get("primary_ip")
    return {
        "id": crudo.get("id", SIN_DATO),
        "nombre": crudo.get("name") or SIN_DATO,
        "fabricante": _anidado(crudo, "device_type", "manufacturer", "name"),
        "modelo": _anidado(crudo, "device_type", "model"),
        "rol": _anidado(rol, "name"),
        "sitio": _anidado(crudo, "site", "name"),
        "estado": _anidado(crudo, "status", "value"),
        "ip": _anidado(ip, "address"),
        "serie": crudo.get("serial") or SIN_DATO,
        "plataforma": _anidado(crudo, "platform", "name"),
    }


def analizar(dispositivos):
    """Genera estadisticas y hallazgos de auditoria."""
    return {
        "total": len(dispositivos),
        "por_sitio": dict(Counter(d["sitio"] for d in dispositivos)),
        "por_rol": dict(Counter(d["rol"] for d in dispositivos)),
        "por_fabricante": dict(Counter(d["fabricante"] for d in dispositivos)),
        "por_estado": dict(Counter(d["estado"] for d in dispositivos)),
        "no_activos": [d["nombre"] for d in dispositivos
                       if d["estado"] != "active"],
        "sin_ip": [d["nombre"] for d in dispositivos if d["ip"] == SIN_DATO],
        "sin_serie": [d["nombre"] for d in dispositivos
                      if d["serie"] == SIN_DATO],
    }


def mostrar_resumen(analisis):
    """Imprime el resumen de auditoria en consola."""
    print("\n===== RESUMEN DE AUDITORIA DE INVENTARIO =====")
    print("Total de dispositivos:", analisis["total"])
    for titulo, clave in (("Por sitio", "por_sitio"), ("Por rol", "por_rol"),
                          ("Por fabricante", "por_fabricante"),
                          ("Por estado", "por_estado")):
        print("\n%s:" % titulo)
        for nombre, cantidad in sorted(analisis[clave].items()):
            print("  - %-25s %d" % (nombre, cantidad))
    for titulo, clave in (("Dispositivos NO activos", "no_activos"),
                          ("Dispositivos sin IP primaria", "sin_ip"),
                          ("Dispositivos sin numero de serie", "sin_serie")):
        lista = analisis[clave]
        print("\n%s (%d): %s" % (titulo, len(lista),
                                 ", ".join(lista) if lista else "ninguno"))
    print("==============================================\n")


# --------------------------------------------------------------------------
# Exportacion (CSV, YAML, XML)
# --------------------------------------------------------------------------
def exportar_csv(dispositivos, ruta):
    with open(ruta, "w", newline="", encoding="utf-8") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=CAMPOS)
        escritor.writeheader()
        escritor.writerows(dispositivos)


def exportar_yaml(dispositivos, analisis, ruta):
    contenido = {"resumen": analisis, "dispositivos": dispositivos}
    with open(ruta, "w", encoding="utf-8") as archivo:
        yaml.safe_dump(contenido, archivo, allow_unicode=True,
                       sort_keys=False, default_flow_style=False)


def exportar_xml(dispositivos, ruta):
    raiz = ET.Element("inventario", total=str(len(dispositivos)))
    for disp in dispositivos:
        nodo = ET.SubElement(raiz, "dispositivo", id=str(disp["id"]))
        for campo in CAMPOS[1:]:
            ET.SubElement(nodo, campo).text = str(disp[campo])
    arbol = ET.ElementTree(raiz)
    if hasattr(ET, "indent"):          # disponible desde Python 3.9
        ET.indent(arbol)
    arbol.write(ruta, encoding="utf-8", xml_declaration=True)


def exportar_todo(dispositivos, analisis, carpeta):
    os.makedirs(carpeta, exist_ok=True)
    exportar_csv(dispositivos, os.path.join(carpeta, "inventario.csv"))
    exportar_yaml(dispositivos, analisis,
                  os.path.join(carpeta, "inventario.yaml"))
    exportar_xml(dispositivos, os.path.join(carpeta, "inventario.xml"))
    LOG.info("Archivos generados en '%s' (CSV, YAML, XML).", carpeta)


# --------------------------------------------------------------------------
# Programa principal
# --------------------------------------------------------------------------
def leer_argumentos(argv=None):
    parser = argparse.ArgumentParser(description="Auditor de inventario de red")
    parser.add_argument("--url", default=os.environ.get("NETBOX_URL"),
                        help="Endpoint HTTPS de dispositivos (o NETBOX_URL)")
    parser.add_argument("--archivo", help="JSON local (modo sin conexion)")
    parser.add_argument("--salida", default="data", help="Carpeta de salida")
    parser.add_argument("--timeout", type=int, default=10,
                        help="Segundos de espera por respuesta")
    parser.add_argument("--log", default="logs/auditor.log")
    return parser.parse_args(argv)


def main(argv=None):
    args = leer_argumentos(argv)
    configurar_logging(args.log)
    try:
        if args.archivo:
            crudos = cargar_json_local(args.archivo)
        else:
            if not args.url:
                raise ErrorAPI("Indique --url o la variable NETBOX_URL.")
            crudos = consultar_api(args.url, os.environ.get("NETBOX_TOKEN"),
                                   timeout=args.timeout)
        dispositivos = [normalizar_dispositivo(d) for d in crudos]
        analisis = analizar(dispositivos)
        mostrar_resumen(analisis)
        exportar_todo(dispositivos, analisis, args.salida)
    except ErrorAPI as exc:
        LOG.error("%s", exc)
        return 1
    except (OSError, ValueError, KeyError) as exc:
        LOG.error("Error al procesar los datos: %s", exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
