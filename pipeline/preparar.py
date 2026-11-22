"""
Preparação (roda UMA vez, ou quando mudar o modelo ou os critérios). Gera:

  pipeline/estatico/estado.geojson       limite do Tocantins (simplificado)
  pipeline/estatico/config.json          campo de ID dos trechos e sub-bacias do Araguaia
  pipeline/estatico/telhas_agua.json     telhas do zoom 13 que têm água (só elas são calculadas)
  site/data/trechos.geojson              trechos de rio ≥ 60 m, com bacia, largura e ponto SEMARH próximo
  asset turbidez_v5_arvores              as 400 árvores do modelo, no seu projeto do Earth Engine
"""
import json
import math
import sys
import time
from pathlib import Path

import ee
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import config as C
import gee as G
import telhas as T


def bacias_araguaia(pontos):
    """IDs das sub-bacias HydroBASINS nível 8 que drenam para o Araguaia (topologia NEXT_DOWN).

    Sementes: os pontos SEMARH mais a jusante no Araguaia e no Tocantins. A primeira sub-bacia
    comum aos dois caminhos rio abaixo é a da confluência; tudo o que drena para o Araguaia antes dela
    é bacia do Araguaia.
    """
    hb = ee.FeatureCollection('WWF/HydroSHEDS/v1/Basins/hybas_8')

    def semente(rio):
        p = pontos[pontos['rio'].str.lower() == rio].sort_values('lat', ascending=False).iloc[0]
        f = hb.filterBounds(ee.Geometry.Point([p['lon'], p['lat']])).first().getInfo()['properties']
        return f, p

    s_a, p_a = semente('rio araguaia')
    s_t, p_t = semente('rio tocantins')
    tab = (hb.filter(ee.Filter.eq('MAIN_BAS', s_a['MAIN_BAS']))
             .reduceColumns(ee.Reducer.toList(2), ['HYBAS_ID', 'NEXT_DOWN']).get('list').getInfo())
    desce = {int(a): int(b) for a, b in tab}
    sobe = {}
    for a, b in desce.items():
        sobe.setdefault(b, []).append(a)

    def caminho(i):
        c = [i]
        while desce.get(c[-1]) in desce:
            c.append(desce[c[-1]])
        return c

    c_a, c_t = caminho(int(s_a['HYBAS_ID'])), caminho(int(s_t['HYBAS_ID']))
    comum = next(i for i in c_a if i in set(c_t))
    if c_a.index(comum) == 0:
        raise RuntimeError('A semente do Araguaia caiu na sub-bacia da confluência.')
    ids, pilha = set(), [c_a[c_a.index(comum) - 1]]
    while pilha:
        i = pilha.pop()
        if i not in ids:
            ids.add(i)
            pilha.extend(sobe.get(i, []))
    print(f'Sementes: Ponto {p_a.ponto} (Araguaia) e Ponto {p_t.ponto} (Tocantins) → {len(ids)} sub-bacias do Araguaia')
    return sorted(ids)


def validar_bacias(pontos, ids):
    fc = ee.FeatureCollection('WWF/HydroSHEDS/v1/Basins/hybas_8').filter(ee.Filter.inList('HYBAS_ID', ids))
    pts = ee.FeatureCollection([ee.Feature(ee.Geometry.Point([r.lon, r.lat]), {'p': int(r.ponto)})
                                for r in pontos.itertuples()])
    dentro = set(pts.filterBounds(fc.geometry()).aggregate_array('p').getInfo())
    calc = pontos['ponto'].map(lambda p: 'Rio Araguaia' if p in dentro else 'Rio Tocantins')
    erro = pontos[calc != pontos['bacia']]
    print(f'Validação da bacia: {len(pontos) - len(erro)}/{len(pontos)} pontos SEMARH conferem.')
    if len(erro):
        print(erro[['ponto', 'rio', 'municipio', 'bacia']].to_string(index=False))


def ponto_proximo(lon, lat, pontos, max_km=15):
    dx = (pontos['lon'] - lon) * 111.32 * math.cos(math.radians(lat))
    dy = (pontos['lat'] - lat) * 110.57
    km = np.hypot(dx, dy)
    i = km.idxmin()
    return int(pontos.loc[i, 'ponto']) if km[i] <= max_km else None


def preparar_trechos(estado, araguaia_ids, pontos):
    rios = ee.FeatureCollection('WWF/HydroSHEDS/v1/FreeFlowingRivers').filterBounds(estado)
    props = rios.first().propertyNames().getInfo()
    cid = next(c for c in ['REACH_ID', 'GOID', 'HYRIV_ID'] if c in props)
    rios = rios.filter(ee.Filter.gte('DIS_AV_CMS', C.VAZAO_MIN))
    araguaia = ee.FeatureCollection('WWF/HydroSHEDS/v1/Basins/hybas_8').filter(ee.Filter.inList('HYBAS_ID', araguaia_ids))
    area_agua = (ee.Image('JRC/GSW1_4/GlobalSurfaceWater').select('occurrence').gte(C.OCORRENCIA_MIN)
                 .selfMask().multiply(ee.Image.pixelArea()).rename('a'))

    def atributos(f):
        g = f.geometry()
        comp = g.length(10)
        area = area_agua.reduceRegion(ee.Reducer.sum(), g.buffer(1000), 30, maxPixels=1e9, tileScale=4).get('a')
        return ee.Feature(g.simplify(30), {
            'id': f.get(cid), 'largura_m': ee.Number(area).divide(comp), 'comprimento_km': comp.divide(1000),
            'vazao_media': f.get('DIS_AV_CMS'),
            'bacia': ee.Algorithms.If(araguaia.filterBounds(g.centroid(10)).size().gt(0), 'Rio Araguaia', 'Rio Tocantins')})

    n = rios.size().getInfo()
    print(f'{n} trechos candidatos (vazão ≥ {C.VAZAO_MIN} m³/s)')
    feats, passo = [], 150
    for i in range(0, n, passo):
        lote = (ee.FeatureCollection(rios.toList(passo, i)).map(atributos)
                .filter(ee.Filter.gte('largura_m', C.LARGURA_MIN_M)))
        for tent in range(3):
            try:
                feats += lote.getInfo()['features']
                break
            except ee.EEException as e:
                print('  nova tentativa:', str(e)[:80])
                time.sleep(10)
        print(f'  {min(i + passo, n)}/{n} avaliados, {len(feats)} com largura ≥ {C.LARGURA_MIN_M} m')

    for k, f in enumerate(feats, 1):
        p = f['properties']
        cs = f['geometry']['coordinates']
        if f['geometry']['type'] == 'MultiLineString':
            cs = [c for parte in cs for c in parte]
        lon, lat = cs[len(cs) // 2]
        p.update(idx=k, largura_m=round(p['largura_m']), comprimento_km=round(p['comprimento_km'], 1),
                 vazao_media=round(p['vazao_media'] or 0, 1), ponto_proximo=ponto_proximo(lon, lat, pontos))
        f['geometry']['coordinates'] = json.loads(json.dumps(f['geometry']['coordinates']),
                                                  parse_float=lambda x: round(float(x), 5))
    return cid, feats


def telhas_com_agua(agua, estado, z_grade=10):
    """Telhas do zoom 13 com água: lê a máscara reduzida (máximo) na grade do zoom 10 (~150 m),
    em pedidos pequenos e paralelos (pedidos grandes estouram a memória das consultas interativas)."""
    from concurrent.futures import ThreadPoolExecutor
    cantos = ee.Geometry(estado).bounds(1).coordinates().get(0).getInfo()
    lons, lats = [c[0] for c in cantos], [c[1] for c in cantos]
    x0, y1 = [int(v) // T.TAM for v in T.lonlat_para_pixel(min(lons) - 0.2, min(lats) - 0.2, z_grade)]
    x1, y0 = [int(v) // T.TAM for v in T.lonlat_para_pixel(max(lons) + 0.2, max(lats) + 0.2, z_grade)]
    reduzida = (agua.clip(ee.Geometry(estado).buffer(15000))
                .reduceResolution(ee.Reducer.max(), maxPixels=4096).unmask(0))
    fator = 2 ** (C.ZOOM_BASE - z_grade)       # telhas do z13 por telha da grade, em cada direção
    px = T.TAM // fator                        # pixels da grade por telha do z13

    def uma(tx, ty):
        for tent in range(4):
            try:
                a = G.baixar_bloco(reduzida, T.grade_bloco(tx, ty, z_grade, 1))['agua']
                return {(tx * fator + i // px, ty * fator + j // px) for j, i in zip(*np.nonzero(a))}
            except Exception as e:
                erro = e
                time.sleep(5 * (tent + 1))
        raise erro

    pedidos = [(tx, ty) for ty in range(y0, y1 + 1) for tx in range(x0, x1 + 1)]
    lista = set()
    with ThreadPoolExecutor(C.PEDIDOS_PARALELOS) as ex:
        for k, r in enumerate(ex.map(lambda t: uma(*t), pedidos), 1):
            lista |= r
            if k % 50 == 0 or k == len(pedidos):
                print(f'  {k}/{len(pedidos)} pedidos, {len(lista)} telhas z13 com água')
    return sorted(lista)


def main():
    G.iniciar()
    C.ESTATICO.mkdir(parents=True, exist_ok=True)
    pontos = pd.read_csv(C.DADOS / 'pontos_semarh.csv')

    print('1/5 Árvores do modelo → asset')
    tarefa = G.salvar_arvores_asset()

    print('2/5 Limite do estado')
    est = G.limite_estado_remoto().simplify(500)
    (C.ESTATICO / 'estado.geojson').write_text(json.dumps(est.getInfo()))
    estado = G.estado()

    print('3/5 Bacia do Araguaia')
    ids = bacias_araguaia(pontos)
    validar_bacias(pontos, ids)

    print('4/5 Trechos de rio')
    cid, feats = preparar_trechos(estado, ids, pontos)
    C.SITE_DADOS.mkdir(parents=True, exist_ok=True)
    (C.SITE_DADOS / 'trechos.geojson').write_text(
        json.dumps({'type': 'FeatureCollection', 'features': feats}, ensure_ascii=False), encoding='utf-8')
    (C.ESTATICO / 'config.json').write_text(json.dumps({'campo_id': cid, 'araguaia_hybas8': ids}))

    print('5/5 Telhas com água')
    lista = telhas_com_agua(G.mascara_agua(estado), estado)
    (C.ESTATICO / 'telhas_agua.json').write_text(json.dumps(lista))
    print(f'{len(lista)} telhas do zoom {C.ZOOM_BASE} com água')

    while tarefa.active():
        time.sleep(10)
    st = tarefa.status()
    print('Asset das árvores:', st['state'], st.get('error_message', ''))
    if st['state'] != 'COMPLETED':
        sys.exit(1)
    print('✅ Preparação concluída')


if __name__ == '__main__':
    main()
