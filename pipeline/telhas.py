"""Telhas do mapa (Web Mercator, esquema XYZ do Leaflet/OpenStreetMap): contas, cores e pirâmide de zooms.

Não depende do Earth Engine, então pode ser testado offline (pipeline/testar.py).
"""
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

ORIGEM = 20037508.342789244          # metade da circunferência da Terra no EPSG:3857 (m)
TAM = 256                            # pixels por telha


def resolucao(z):
    """Tamanho do pixel (m, no EPSG:3857) no zoom z."""
    return 2 * ORIGEM / (TAM * 2 ** z)


def lonlat_para_pixel(lon, lat, z):
    """Coordenada de pixel global (x, y) no zoom z."""
    n = TAM * 2 ** z
    x = (lon + 180) / 360 * n
    lat_r = math.radians(lat)
    y = (1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * n
    return x, y


def telha_bbox_lonlat(x, y, z):
    n = 2 ** z
    lon0, lon1 = x / n * 360 - 180, (x + 1) / n * 360 - 180
    lat = lambda yy: math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))
    return lon0, lat(y + 1), lon1, lat(y)


def grade_bloco(bx, by, z, bloco):
    """Grade (pixelGrid) do computePixels para o bloco (bx, by) de bloco x bloco telhas."""
    r = resolucao(z)
    lado = TAM * bloco
    return {'dimensions': {'width': lado, 'height': lado},
            'affineTransform': {'scaleX': r, 'shearX': 0, 'translateX': -ORIGEM + bx * lado * r,
                                'shearY': 0, 'scaleY': -r, 'translateY': ORIGEM - by * lado * r},
            'crsCode': 'EPSG:3857'}


def blocos_de_telhas(telhas, bloco):
    """Agrupa telhas (x, y) em blocos; devolve {(bx, by): [(x, y), ...]}."""
    out = {}
    for x, y in telhas:
        out.setdefault((x // bloco, y // bloco), []).append((x, y))
    return out


def cortar_bloco(arr, bx, by, bloco):
    """Divide o array de um bloco (bloco*256 x bloco*256) em telhas {(x, y): array 256 x 256}."""
    out = {}
    for j in range(bloco):
        for i in range(bloco):
            out[(bx * bloco + i, by * bloco + j)] = arr[j * TAM:(j + 1) * TAM, i * TAM:(i + 1) * TAM]
    return out


# ---------------- cores ----------------
# escala contínua (em log) da água clara à barrenta; as cores de 5 e 15 NTU são as das classes do site
PARADAS = [(1, '#1d6a86'), (3, '#3b8fa3'), (5, '#7fa98f'), (10, '#ad9a42'), (15, '#b47a33'),
           (30, '#8c4a22'), (60, '#62321a')]


def _rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def tabela_cores(max_x10=2000):
    """LUT: índice = turbidez x 10 (inteiro) → RGBA. Índice 0 = sem dado (transparente)."""
    v = np.arange(max_x10 + 1) / 10
    lv = np.log10(np.clip(v, PARADAS[0][0], PARADAS[-1][0]))
    xs = np.log10([p for p, _ in PARADAS])
    cores = np.array([_rgb(c) for _, c in PARADAS], float)
    lut = np.zeros((max_x10 + 1, 4), np.uint8)
    for k in range(3):
        lut[:, k] = np.interp(lv, xs, cores[:, k]).round()
    lut[:, 3] = 235
    lut[0] = 0
    return lut


LUT = tabela_cores()


def salvar_png(valores_x10, arquivo: Path):
    """valores_x10: array 256 x 256 uint16 (turbidez x 10; 0 = sem dado)."""
    idx = np.clip(valores_x10, 0, len(LUT) - 1)
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(LUT[idx], 'RGBA').save(arquivo, optimize=True)


def reduzir(filhos):
    """Junta 4 telhas filhas {(dx, dy): array} numa telha do zoom de cima.

    Cada pixel novo = média em log dos pixels válidos (até 4) que ele cobre.
    """
    grande = np.zeros((2 * TAM, 2 * TAM), np.float64)
    for (dx, dy), a in filhos.items():
        grande[dy * TAM:(dy + 1) * TAM, dx * TAM:(dx + 1) * TAM] = a
    blocos = grande.reshape(TAM, 2, TAM, 2).transpose(0, 2, 1, 3).reshape(TAM, TAM, 4)
    validos = blocos > 0
    n = validos.sum(-1)
    soma_log = np.where(validos, np.log(np.where(validos, blocos, 1)), 0).sum(-1)
    out = np.zeros((TAM, TAM), np.uint16)
    ok = n > 0
    out[ok] = np.exp(soma_log[ok] / n[ok]).round().clip(1, 65534)
    return out


def piramide(base, z_base, z_min, pasta: Path):
    """Grava as telhas PNG de z_base até z_min e o índice das telhas existentes (indice.json).

    base = {(x, y): array uint16} no zoom z_base. O site usa o índice para só pedir telhas que existem.
    """
    nivel, total, indice = base, 0, {}
    for z in range(z_base, z_min - 1, -1):
        for (x, y), a in nivel.items():
            if a.any():
                salvar_png(a, pasta / str(z) / str(x) / f'{y}.png')
                indice.setdefault(str(z), []).append(f'{x}/{y}')
                total += 1
        if z == z_min:
            break
        pais = {}
        for (x, y), a in nivel.items():
            pais.setdefault((x // 2, y // 2), {})[(x % 2, y % 2)] = a
        nivel = {k: reduzir(f) for k, f in pais.items()}
    (pasta / 'indice.json').write_text(json.dumps(indice))
    return total


def valor_no_ponto(base, z, lon, lat, raio=1):
    """Mediana dos pixels válidos (turbidez x 10) numa janela (2*raio+1)² ao redor do ponto."""
    px, py = lonlat_para_pixel(lon, lat, z)
    vals = []
    for gy in range(int(py) - raio, int(py) + raio + 1):
        for gx in range(int(px) - raio, int(px) + raio + 1):
            a = base.get((gx // TAM, gy // TAM))
            if a is not None:
                v = a[gy % TAM, gx % TAM]
                if v > 0:
                    vals.append(v)
    return float(np.median(vals)) if vals else None
