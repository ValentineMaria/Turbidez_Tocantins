"""
Testes que rodam sem o Earth Engine (antes de cada atualização no GitHub Actions):

1. As árvores convertidas reproduzem as previsões do modelo original (scikit-learn).
2. As contas das telhas batem (coordenada → telha → grade do Earth Engine).
3. O processamento de um bloco fictício gera telhas PNG, trechos e pontos sem erro.
"""
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[1] / 'modelo'))
import config as C
import telhas as T
from converter_arvores import avaliar_texto


def teste_arvores():
    A = json.loads((C.MODELO / 'arvores_v5.json').read_text())
    ref = pd.read_csv(C.MODELO / 'referencia_features_v5.csv').head(40)
    X = ref[A['variaveis']].astype('float32').astype('float64')
    pred = np.array([np.mean([avaliar_texto(t, r) for t in A['arvores']]) for _, r in X.iterrows()])
    erro = np.abs(pred - ref['pred_log10_sklearn']).max()
    assert erro < 1e-6, erro
    print(f'1. Árvores: OK (erro máx. {erro:.1e} em log10)')


def teste_telhas():
    lon, lat = -48.36, -10.18          # Palmas
    px, py = T.lonlat_para_pixel(lon, lat, 13)
    x, y = int(px // 256), int(py // 256)
    lon0, lat0, lon1, lat1 = T.telha_bbox_lonlat(x, y, 13)
    assert lon0 <= lon <= lon1 and lat0 <= lat <= lat1
    g = T.grade_bloco(x, y, 13, 1)['affineTransform']
    import math
    xm = math.radians(lon) * 6378137.0
    assert g['translateX'] <= xm <= g['translateX'] + 256 * g['scaleX']
    print(f'2. Telhas: OK (Palmas está na telha z13 {x},{y}; pixel de {T.resolucao(13):.1f} m)')
    return x, y


def teste_processamento(x, y):
    import atualizar as A
    rng = np.random.default_rng(0)
    bloco = {}
    for dx in range(2):
        for dy in range(2):
            t = np.zeros((256, 256), np.uint16)
            t[:, 100:160] = (rng.lognormal(np.log(50), 0.3, (256, 60))).astype(np.uint16)   # ~5 NTU
            tr = np.zeros_like(t); tr[:, 90:170] = 1
            bloco[(x + dx, y + dy)] = {'turb_x10': t, 'dias': np.where(t > 0, 4, 0).astype(np.uint16),
                                       'sensor': np.where(t > 0, 1, 0).astype(np.uint16), 'trecho': tr}
    ac = A.Acumulador()
    ac.adicionar(bloco)
    info = json.loads((C.MODELO / 'modelo_v5_info.json').read_text())
    with tempfile.TemporaryDirectory() as d:
        n = T.piramide(ac.base, 13, 9, Path(d))
        assert n >= 5, n
    tr = A.resumir_trechos(ac.por_trecho(), date(2026, 11, 15), info)
    assert tr['1']['classe'] in C.NOMES_CLASSE and tr['1']['data_imagem'] == '2026-11-12', tr
    lon0, lat0, lon1, lat1 = T.telha_bbox_lonlat(x, y, 13)
    px0 = lon0 + (lon1 - lon0) * 130 / 256
    pts = pd.DataFrame([{'ponto': 99, 'lon': px0, 'lat': (lat0 + lat1) / 2, 'no_dominio': True, 'ajuste_log10': 0.1}])
    p = A.estimar_pontos(ac.base, ac.dias, pts, date(2026, 11, 15), info)['99']
    assert p['status'] == 'ok' and abs(p['turb_ntu'] / p['turb_geral_ntu'] - 10 ** 0.1) < 0.05, p
    print(f"3. Processamento: OK ({n} telhas PNG; trecho {tr['1']['turb_ntu']} NTU; ponto {p['turb_ntu']} NTU com ajuste)")


if __name__ == '__main__':
    teste_arvores()
    teste_processamento(*teste_telhas())
    print('✅ Todos os testes passaram')
