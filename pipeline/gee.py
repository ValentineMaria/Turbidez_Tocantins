"""Tudo o que roda no Google Earth Engine: autenticação, máscaras e o mapa de turbidez pixel a pixel.

O cálculo de cada pixel reproduz o treino do modelo v5:
  imagem limpa mais recente (Sentinel-2 ou Landsat 8/9) → mediana 3 x 3 dos pixels de água →
  filtros de qualidade → variáveis (bandas, bacia, mês, sensor, chuva de 7 dias IMERG na sub-bacia) →
  as 400 árvores do Extra Trees, convertidas para o Earth Engine.
"""
import json
import math
import os
from datetime import date, timedelta

import ee

import config as C

FUSO = 'America/Sao_Paulo'
VARS = ['lnB4B3', 'lnB4', 'lnB3B2', 'lnB8AB4', 'Araguaia', 'sin_mes', 'cos_mes', 'Landsat', 'log_chuva_7d']


# ---------------------------------------------------------------- conexão
def iniciar():
    chave = os.environ.get('GEE_SERVICE_ACCOUNT_KEY')
    if chave:
        info = json.loads(chave)
        ee.Initialize(ee.ServiceAccountCredentials(info['client_email'], key_data=chave), project=C.PROJETO)
    else:
        try:
            ee.Initialize(project=C.PROJETO)
        except Exception:
            ee.Authenticate()
            ee.Initialize(project=C.PROJETO)
    print(f'Earth Engine conectado (projeto {C.PROJETO})')


# ---------------------------------------------------------------- bases fixas
def limite_estado_remoto():
    """Limite do Tocantins (FAO GAUL 2025; se os nomes de campo mudarem, cai para a versão 2015)."""
    for colecao, campo in [('FAO/GAUL/2025/level1', 'gaul1_name'), ('FAO/GAUL/2025/level1', 'ADM1_NAME'),
                           ('FAO/GAUL/2015/level1', 'ADM1_NAME')]:
        fc = ee.FeatureCollection(colecao).filter(ee.Filter.eq(campo, 'Tocantins'))
        try:
            if fc.size().getInfo() > 0:
                print(f'Limite do estado: {colecao} ({campo})')
                return fc.geometry()
        except ee.EEException:
            continue
    raise RuntimeError('Limite do Tocantins não encontrado no GAUL')


def estado():
    """Limite salvo pelo preparar.py (leve, não depende do GAUL a cada execução)."""
    g = json.loads((C.ESTATICO / 'estado.geojson').read_text())
    return ee.Geometry(g)


def rios(regiao):
    return (ee.FeatureCollection('WWF/HydroSHEDS/v1/FreeFlowingRivers')
            .filterBounds(regiao).filter(ee.Filter.gte('DIS_AV_CMS', C.VAZAO_MIN)))


def mascara_agua(regiao):
    """Água permanente perto dos rios, sem açudes e lagoas isolados."""
    jrc = ee.Image('JRC/GSW1_4/GlobalSurfaceWater').select('occurrence')
    agua = jrc.gte(C.OCORRENCIA_MIN)
    perto = ee.Image(0).paint(rios(regiao).map(lambda f: f.buffer(C.BUFFER_RIO_M)), 1).eq(1)
    grande = agua.selfMask().connectedPixelCount(C.MIN_CORPO_PIX, True).gte(C.MIN_CORPO_PIX).unmask(0)
    return perto.And(agua).And(grande).reproject(jrc.projection()).rename('agua')


def imagem_trechos(campo_id, trechos):
    """Índice do trecho (1..N) em cada pixel até 1,5 km da linha do rio. Rios maiores por cima."""
    ordem = sorted(trechos, key=lambda t: t.get('vazao_media') or 0)
    ids = [t['id'] for t in ordem]
    idx = ee.Dictionary.fromLists([str(i) for i in ids], [t['idx'] for t in ordem])
    fc = (ee.FeatureCollection('WWF/HydroSHEDS/v1/FreeFlowingRivers').filter(ee.Filter.inList(campo_id, ids))
          .map(lambda f: f.buffer(C.BUFFER_TRECHO_M).set(
              'idx', idx.get(ee.Number(f.get(campo_id)).toLong().format('%d')),
              'q', f.get('DIS_AV_CMS')))
          .sort('q'))
    return ee.Image(0).int().paint(fc, 'idx').rename('trecho')


def imagem_araguaia(ids_hybas8):
    fc = ee.FeatureCollection('WWF/HydroSHEDS/v1/Basins/hybas_8').filter(ee.Filter.inList('HYBAS_ID', ids_hybas8))
    return ee.Image(0).paint(fc, 1).rename('Araguaia')


def classificador():
    arvores = ee.FeatureCollection(C.ASSET_ARVORES).sort('i').aggregate_array('arvore')
    return ee.Classifier.decisionTreeEnsemble(arvores).setOutputMode('REGRESSION')


def salvar_arvores_asset():
    """Guarda as árvores como asset (uma vez). Cada linha precisa de geometria: usamos um ponto qualquer."""
    try:
        ee.data.deleteAsset(C.ASSET_ARVORES)
        print('Asset antigo das árvores apagado')
    except ee.EEException:
        pass
    A = json.loads((C.MODELO / 'arvores_v5.json').read_text())
    assert A['variaveis'] == VARS, A['variaveis']
    pt = ee.Geometry.Point([-48.3, -10.2])
    fc = ee.FeatureCollection([ee.Feature(pt, {'arvore': t, 'i': i}) for i, t in enumerate(A['arvores'])])
    t = ee.batch.Export.table.toAsset(fc, 'arvores_turbidez_v5', C.ASSET_ARVORES)
    t.start()
    return t


# ---------------------------------------------------------------- imagens
def colecoes(regiao, inicio, fim):
    s2 = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED').filterBounds(regiao).filterDate(inicio, fim)
          .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 95)))
    s2 = ee.ImageCollection(ee.Join.saveFirst('nuvem').apply(
        s2, ee.ImageCollection('COPERNICUS/S2_CLOUD_PROBABILITY'),
        ee.Filter.equals(leftField='system:index', rightField='system:index')))
    ls = (ee.ImageCollection('LANDSAT/LC08/C02/T1_L2').merge(ee.ImageCollection('LANDSAT/LC09/C02/T1_L2'))
          .filterBounds(regiao).filterDate(inicio, fim).filter(ee.Filter.lt('CLOUD_COVER', 95)))
    return s2, ls


def datas_locais(col):
    ms = col.aggregate_array('system:time_start').getInfo()
    from datetime import datetime, timezone
    return sorted({(datetime.fromtimestamp(t / 1000, timezone.utc) - timedelta(hours=3)).date().isoformat()
                   for t in ms})


def refl_s2(img, agua):
    img = ee.Image(img)
    prob = ee.Image(img.get('nuvem')).select('probability')
    scl = img.select('SCL')
    r = img.select(['B2', 'B3', 'B4', 'B8A', 'B11']).divide(10000)
    ok = (prob.lt(C.QC['nuvem_s2cloudless']).And(scl.neq(3)).And(scl.neq(1))
          .And(r.normalizedDifference(['B3', 'B11']).gt(0)).And(agua))
    return r.select(['B2', 'B3', 'B4', 'B8A']).updateMask(ok)


def refl_landsat(img, agua):
    img = ee.Image(img)
    qa = img.select('QA_PIXEL')
    nuvem = qa.bitwiseAnd(0b11110).neq(0)
    sat = img.select('QA_RADSAT').neq(0)
    r = (img.select(['SR_B2', 'SR_B3', 'SR_B4', 'SR_B5', 'SR_B6']).multiply(0.0000275).add(-0.2)
         .rename(['B2', 'B3', 'B4', 'B8A', 'B11']))
    ok = nuvem.Not().And(sat.Not()).And(r.normalizedDifference(['B3', 'B11']).gt(0)).And(agua)
    return r.select(['B2', 'B3', 'B4', 'B8A']).updateMask(ok)


RED = (ee.Reducer.median().combine(ee.Reducer.stdDev(), sharedInputs=True)
       .combine(ee.Reducer.count(), sharedInputs=True))


def variaveis_do_dia(refl, proj, dia, landsat, min_pix, araguaia, chuva_img, hoje):
    """Janela 3 x 3 na projeção nativa, filtros de qualidade e as 9 variáveis do modelo."""
    j = refl.reduceNeighborhood(RED, ee.Kernel.square(1), skipMasked=True).reproject(proj)
    B2, B3, B4, B8A = [j.select(f'{b}_median') for b in ['B2', 'B3', 'B4', 'B8A']]
    ok = (j.select('B3_count').gte(min_pix)
          .And(B2.gt(0)).And(B3.gt(0)).And(B4.gt(0)).And(B8A.gt(0))
          .And(B3.lte(C.QC['max_B3'])).And(B4.gte(C.QC['min_B4']))
          .And(B8A.divide(B4).lte(C.QC['max_B8A_B4']))
          .And(j.select('B4_stdDev').divide(B4).lte(C.QC['max_cv_B4'])))
    b = lambda x: x.max(1e-4)
    mes = int(dia[5:7])
    ang = 2 * math.pi * mes / 12
    idade = (hoje - date.fromisoformat(dia)).days
    t = ee.Date(dia, FUSO).millis()
    return (ee.Image.cat([
        b(B4).divide(b(B3)).log().rename('lnB4B3'),
        b(B4).log().rename('lnB4'),
        b(B3).divide(b(B2)).log().rename('lnB3B2'),
        b(B8A).divide(b(B4)).log().rename('lnB8AB4'),
        araguaia.toFloat(),
        ee.Image.constant(math.sin(ang)).rename('sin_mes'),
        ee.Image.constant(math.cos(ang)).rename('cos_mes'),
        ee.Image.constant(landsat).rename('Landsat'),
        chuva_img.max(0).add(1).log().rename('log_chuva_7d'),
        ee.Image.constant(idade + 1).rename('dias'),          # +1: o zero fica reservado para "sem dado"
        ee.Image.constant(2 if landsat else 1).rename('sensor'),
    ]).toFloat().updateMask(ok).set('system:time_start', t))


# ---------------------------------------------------------------- chuva
def tabela_chuva(regiao, dias):
    """Chuva (mm) dos 7 dias que terminam em cada data, média por sub-bacia HydroBASINS nível 7.

    Calculada uma vez por execução (pequena) e aplicada depois por remap, que é barato.
    """
    imerg = ee.ImageCollection('NASA/GPM_L3/IMERG_V07').select('precipitation')
    bacias = ee.FeatureCollection('WWF/HydroSHEDS/v1/Basins/hybas_7').filterBounds(regiao)

    def por_dia(d):
        d = ee.String(d)
        fim = ee.Date(d, FUSO).advance(1, 'day')
        soma = imerg.filterDate(fim.advance(-7, 'day'), fim).sum().multiply(0.5)   # mm/h a cada 30 min
        fc = soma.reduceRegions(bacias, ee.Reducer.mean(), 5566)
        return ee.Feature(None, {'dia': d, 'pfaf': fc.aggregate_array('PFAF_ID'),
                                 'mm': fc.aggregate_array('mean')})

    linhas = ee.FeatureCollection(ee.List(dias).map(por_dia)).getInfo()['features']
    tab = {}
    for f in linhas:
        p = f['properties']
        tab[p['dia']] = {int(k): (v if v is not None else 0.0) for k, v in zip(p['pfaf'], p['mm'])}
    ultimo = imerg.sort('system:time_start', False).first().date().format('YYYY-MM-dd HH:mm').getInfo()
    return tab, ultimo


def imagem_chuva(regiao, tab_dia):
    bacias = ee.FeatureCollection('WWF/HydroSHEDS/v1/Basins/hybas_7').filterBounds(regiao)
    pfaf = ee.Image(0).int().paint(bacias, 'PFAF_ID')
    chaves = list(tab_dia)
    return pfaf.remap(chaves, [tab_dia[k] for k in chaves], 0).toFloat().rename('chuva_7d')


# ---------------------------------------------------------------- resultado
def imagem_resultado(regiao, estaticos, hoje=None):
    """Imagem com as bandas (uint16) que o site usa:
    turb_x10 (turbidez x 10; 0 = sem estimativa), dias (+1), sensor (1 = S2, 2 = Landsat), trecho (índice).
    Devolve também um resumo das imagens usadas.
    """
    hoje = hoje or date.today()
    inicio = (hoje - timedelta(days=C.DIAS_BUSCA)).isoformat()
    fim = (hoje + timedelta(days=1)).isoformat()
    agua = estaticos['agua']
    s2, ls = colecoes(regiao, inicio, fim)
    d_s2, d_ls = datas_locais(s2), datas_locais(ls)
    chuva, ultimo_imerg = tabela_chuva(regiao, sorted(set(d_s2 + d_ls)))

    diarias = []
    for dias, col, prep, landsat, min_pix in [(d_s2, s2, refl_s2, 0, C.QC['min_pix_s2']),
                                               (d_ls, ls, refl_landsat, 1, C.QC['min_pix_landsat'])]:
        for d in dias:
            do_dia = col.filterDate(ee.Date(d, FUSO), ee.Date(d, FUSO).advance(1, 'day'))
            # junta as cenas do mesmo dia ANTES da janela 3 x 3: evita a emenda entre cenas do protótipo
            proj = do_dia.first().select('B3' if not landsat else 'SR_B3').projection()
            refl = do_dia.map(lambda i: prep(i, agua)).mosaic()
            diarias.append(variaveis_do_dia(refl, proj, d, landsat, min_pix, estaticos['araguaia'],
                                            imagem_chuva(regiao, chuva.get(d, {})), hoje))

    pilha = ee.ImageCollection(diarias).sort('system:time_start').mosaic()   # a mais recente fica por cima
    log_t = pilha.select(VARS).classify(estaticos['modelo'], 'log10')
    turb_x10 = ee.Image(10).pow(log_t).multiply(10).round().clamp(1, 65534)
    saida = (ee.Image.cat([turb_x10.rename('turb_x10'), pilha.select('dias'), pilha.select('sensor')])
             .unmask(0).addBands(estaticos['trecho']).toUint16())
    resumo = {'imagens_sentinel2': len(d_s2), 'datas_landsat': len(d_ls), 'datas_s2': len(d_s2),
              'periodo': [inicio, hoje.isoformat()], 'chuva_imerg_mais_recente_utc': ultimo_imerg}
    return saida, resumo


def baixar_bloco(imagem, grade):
    """Pede ao Earth Engine um bloco já na grade das telhas. Devolve array estruturado numpy."""
    return ee.data.computePixels({'expression': imagem, 'fileFormat': 'NUMPY_NDARRAY', 'grid': grade})
