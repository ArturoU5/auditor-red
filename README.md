# Auditor de inventario de red

Script en Python que consulta un inventario de dispositivos mediante una API REST
sobre HTTPS (NetBox), analiza los datos y exporta el resultado a CSV, YAML y XML.

## Requisitos
- DEVASC VM (Python 3, pip3, Git)
- `pip3 install -r requirements.txt`

## Configuración
```bash
export NETBOX_URL="https://<servidor>/api/dcim/devices/"
export NETBOX_TOKEN="<su_token>"      # nunca subir el token a Git
```

## Uso
```bash
python3 src/auditor.py --salida data                          # consulta la API
python3 src/auditor.py --archivo data/muestra.json --salida data   # modo sin conexión
python3 -m unittest discover -s tests -v                      # pruebas
```

## Archivos generados
`data/inventario.csv`, `data/inventario.yaml`, `data/inventario.xml` y `logs/auditor.log`.

## Integrantes
- Guillermo Arturo Ugaz Montesinos
- Angello Andre Ramírez Del Aguila
- Omar Alexander Córdova Pintado
- ⁠Josmel Montoya Paiva
