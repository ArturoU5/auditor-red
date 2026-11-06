"""Pruebas unitarias del auditor (no requieren conexion a internet)."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import auditor  # noqa: E402

MUESTRA = os.path.join(os.path.dirname(__file__), "..", "data", "muestra.json")


def respuesta(codigo, cuerpo=None, cabeceras=None):
    resp = mock.Mock()
    resp.status_code = codigo
    resp.headers = cabeceras or {}
    resp.json.return_value = cuerpo
    return resp


class PruebasProcesamiento(unittest.TestCase):
    def setUp(self):
        crudos = auditor.cargar_json_local(MUESTRA)
        self.disp = [auditor.normalizar_dispositivo(d) for d in crudos]

    def test_normalizar(self):
        self.assertEqual(self.disp[0]["fabricante"], "Cisco")
        self.assertEqual(self.disp[2]["ip"], "N/D")

    def test_analizar(self):
        a = auditor.analizar(self.disp)
        self.assertEqual(a["total"], 5)
        self.assertEqual(a["por_sitio"]["Lima-Sede"], 3)
        self.assertIn("rt-borde-01", a["no_activos"])
        self.assertEqual(len(a["sin_ip"]), 2)

    def test_exportar(self):
        a = auditor.analizar(self.disp)
        with tempfile.TemporaryDirectory() as carpeta:
            auditor.exportar_todo(self.disp, a, carpeta)
            for nombre in ("inventario.csv", "inventario.yaml",
                           "inventario.xml"):
                self.assertTrue(os.path.getsize(os.path.join(carpeta, nombre)))


class PruebasAPI(unittest.TestCase):
    URL = "https://netbox.ejemplo.com/api/dcim/devices/"

    def test_rechaza_http(self):
        with self.assertRaises(auditor.ErrorAPI):
            auditor.consultar_api("http://netbox.ejemplo.com/api/", "tk")

    def test_sin_token(self):
        with self.assertRaises(auditor.ErrorAPI):
            auditor.consultar_api(self.URL, None)

    @mock.patch("auditor.time.sleep")
    @mock.patch("auditor.requests.Session.get")
    def test_401(self, get, _sleep):
        get.return_value = respuesta(401)
        with self.assertRaises(auditor.ErrorAPI) as ctx:
            auditor.consultar_api(self.URL, "malo")
        self.assertIn("401", str(ctx.exception))

    @mock.patch("auditor.time.sleep")
    @mock.patch("auditor.requests.Session.get")
    def test_404(self, get, _sleep):
        get.return_value = respuesta(404)
        with self.assertRaises(auditor.ErrorAPI) as ctx:
            auditor.consultar_api(self.URL, "tk")
        self.assertIn("404", str(ctx.exception))

    @mock.patch("auditor.time.sleep")
    @mock.patch("auditor.requests.Session.get")
    def test_reintenta_503_y_funciona(self, get, _sleep):
        get.side_effect = [respuesta(503), respuesta(
            200, {"results": [{"id": 1}], "next": None})]
        datos = auditor.consultar_api(self.URL, "tk")
        self.assertEqual(len(datos), 1)
        self.assertEqual(get.call_count, 2)

    @mock.patch("auditor.time.sleep")
    @mock.patch("auditor.requests.Session.get")
    def test_json_invalido(self, get, _sleep):
        resp = respuesta(200)
        resp.json.side_effect = ValueError("no es json")
        get.return_value = resp
        with self.assertRaises(auditor.ErrorAPI):
            auditor.consultar_api(self.URL, "tk")

    @mock.patch("auditor.time.sleep")
    @mock.patch("auditor.requests.Session.get")
    def test_paginacion(self, get, _sleep):
        get.side_effect = [
            respuesta(200, {"results": [{"id": 1}],
                            "next": self.URL + "?offset=1"}),
            respuesta(200, {"results": [{"id": 2}], "next": None})]
        self.assertEqual(len(auditor.consultar_api(self.URL, "tk")), 2)


if __name__ == "__main__":
    unittest.main()
