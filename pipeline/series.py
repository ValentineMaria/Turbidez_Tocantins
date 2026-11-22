"""
Monta os dados dos pontos da SEMARH para o site (não usa o Earth Engine; roda em segundos):

  site/data/pontos.geojson      pontos, domínio do modelo, ajuste e acerto histórico de cada um
  site/data/series.json         série de cada ponto: medições oficiais, estimativas de validação e do app
  site/data/confiabilidade.json acerto do modelo por classe (validação com o ano inteiro fora)

Para incluir coletas novas da SEMARH: acrescente linhas em dados/coletas_semarh.csv
(ponto, data no formato AAAA-MM-DD, turbidez_ntu, fonte) e rode este script (ou o workflow).
"""
import json
import shutil
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import config as C


def ler_coletas():
    c = pd.read_csv(C.DADOS / 'coletas_semarh.csv')
    faltam = {'ponto', 'data', 'turbidez_ntu'} - set(c.columns)
    if faltam:
        sys.exit(f'coletas_semarh.csv sem as colunas: {faltam}')
    c['data'] = pd.to_datetime(c['data'], errors='coerce')
    c['turbidez_ntu'] = pd.to_numeric(c['turbidez_ntu'].astype(str).str.replace(',', '.'), errors='coerce')
    ruins = c[c['data'].isna() | c['turbidez_ntu'].isna()]
    if len(ruins):
        print(f'Aviso: {len(ruins)} linhas com data ou turbidez inválida foram ignoradas:')
        print(ruins.head(10).to_string(index=False))
    c = c.drop(ruins.index)
    c['data'] = c['data'].dt.strftime('%Y-%m-%d')
    return c.drop_duplicates(['ponto', 'data'], keep='last').sort_values(['ponto', 'data'])


def main():
    pts = pd.read_csv(C.DADOS / 'pontos_semarh.csv')
    col = ler_coletas()
    val = pd.read_csv(C.DADOS / 'validacao_v5.csv')
    app = pd.read_csv(C.DADOS / 'historico_app.csv') if (C.DADOS / 'historico_app.csv').exists() else None
    conf = json.loads((C.DADOS / 'confiabilidade.json').read_text())

    series = {}
    for p in pts['ponto']:
        s = {'medicoes': col.loc[col['ponto'] == p, ['data', 'turbidez_ntu']].values.tolist(),
             'validacao': val.loc[val['ponto'] == p, ['data', 'medido_ntu', 'estimado_ajustado_ntu']].values.tolist()}
        if app is not None:
            s['app'] = app.loc[app['ponto'] == p, ['data_imagem', 'turb_ntu']].values.tolist()
        series[str(p)] = s

    feats = []
    for r in pts.itertuples():
        pp = conf['por_ponto'].get(str(r.ponto))
        m = col[col['ponto'] == r.ponto]
        feats.append({'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [r.lon, r.lat]},
                      'properties': {
                          'ponto': int(r.ponto), 'rio': r.rio, 'municipio': r.municipio, 'bacia': r.bacia,
                          'largura_m': int(r.largura_m), 'no_dominio': bool(r.no_dominio),
                          'fator_ajuste': None if pd.isna(r.ajuste_log10) else round(10 ** r.ajuste_log10, 2),
                          'n_medicoes': int(len(m)),
                          'ultima_medicao': m.iloc[-1][['data', 'turbidez_ntu']].tolist() if len(m) else None,
                          'acerto': pp}})
    C.SITE_DADOS.mkdir(parents=True, exist_ok=True)
    (C.SITE_DADOS / 'pontos.geojson').write_text(json.dumps({'type': 'FeatureCollection', 'features': feats},
                                                            ensure_ascii=False), encoding='utf-8')
    (C.SITE_DADOS / 'series.json').write_text(json.dumps(series, ensure_ascii=False), encoding='utf-8')
    shutil.copy(C.DADOS / 'confiabilidade.json', C.SITE_DADOS / 'confiabilidade.json')
    print(f'✅ {len(feats)} pontos, {len(col)} medições oficiais ({col["data"].min()} a {col["data"].max()})')


if __name__ == '__main__':
    main()
