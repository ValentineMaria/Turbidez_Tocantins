"""
Atualização do mapa (GitHub Actions a cada 3 dias, ou manual).

1. Monta no Earth Engine o mapa pixel a pixel do estado (gee.imagem_resultado).
2. Baixa, bloco a bloco, só as telhas que têm água, já na grade do mapa web.
3. Gera as telhas PNG (zoom 9 a 13), o resumo de cada trecho de rio e a estimativa nos pontos da SEMARH.

Saídas: _telhas/{z}/{x}/{y}.png e site/data/{atual_trechos,atual_pontos,metadados}.json,
dados/historico_app.csv (estimativas acumuladas nos pontos).
"""
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import config as C
import telhas as T


def classe(t):
    return C.NOMES_CLASSE[int(np.digitize(t, C.LIMITES_CLASSE))]


class Acumulador:
    """Junta as telhas à medida que chegam, guardando só o necessário (turbidez, dias e pixels por trecho)."""

    def __init__(self):
        self.base, self.dias = {}, {}
        self._tr = {'trecho': [], 'turb': [], 'dias': [], 'sensor': []}

    def adicionar(self, tiles):
        for xy, b in tiles.items():
            t = b['turb_x10']
            if not t.any():
                continue
            self.base[xy], self.dias[xy] = t.copy(), b['dias'].copy()
            m = (t > 0) & (b['trecho'] > 0)
            for k, a in (('trecho', b['trecho']), ('turb', t), ('dias', b['dias']), ('sensor', b['sensor'])):
                self._tr[k].append(a[m])

    def por_trecho(self):
        return {k: (np.concatenate(v) if v else np.array([], np.uint16)) for k, v in self._tr.items()}


def resumir_trechos(por_trecho, hoje, info, min_pix=5):
    lo, hi = info['intervalo_80pct_log10']['sem_ajuste']
    df = pd.DataFrame(por_trecho)
    out = {}
    if df.empty:
        return out
    for idx, g in df.groupby('trecho'):
        if len(g) < min_pix:
            continue
        t = float(np.median(g['turb'])) / 10
        d = int(Counter(g['dias']).most_common(1)[0][0]) - 1
        out[str(int(idx))] = {
            'turb_ntu': round(t, 1), 'faixa_80': [round(t * 10 ** lo, 1), round(t * 10 ** hi, 1)],
            'classe': classe(t), 'data_imagem': (hoje - timedelta(days=d)).isoformat(),
            'n_pix': int(len(g)), 'landsat_pct': round(100 * float(np.mean(g['sensor'] == 2))),
            'p10_p90': [round(float(np.percentile(g['turb'], 10)) / 10, 1),
                        round(float(np.percentile(g['turb'], 90)) / 10, 1)]}
    return out


def estimar_pontos(base, dias, pontos, hoje, info):
    out = {}
    for p in pontos.itertuples():
        if not p.no_dominio:
            out[str(p.ponto)] = {'status': 'fora_dominio'}
            continue
        v = T.valor_no_ponto(base, C.ZOOM_BASE, p.lon, p.lat, 1) or T.valor_no_ponto(base, C.ZOOM_BASE, p.lon, p.lat, 3)
        if v is None:
            out[str(p.ponto)] = {'status': 'sem_imagem'}
            continue
        d = T.valor_no_ponto(dias, C.ZOOM_BASE, p.lon, p.lat, 3) or 1
        com_ajuste = not pd.isna(p.ajuste_log10)
        log_t = np.log10(v / 10) + (p.ajuste_log10 if com_ajuste else 0)
        lo, hi = info['intervalo_80pct_log10']['com_ajuste' if com_ajuste else 'sem_ajuste']
        t = 10 ** log_t
        out[str(p.ponto)] = {'status': 'ok', 'turb_ntu': round(t, 1), 'classe': classe(t),
                             'faixa_80': [round(10 ** (log_t + lo), 1), round(10 ** (log_t + hi), 1)],
                             'ajuste_ponto': com_ajuste, 'turb_geral_ntu': round(v / 10, 1),
                             'data_imagem': (hoje - timedelta(days=int(d) - 1)).isoformat()}
    return out


def baixar_tudo(imagem, lista_telhas, acum):
    import gee as G
    blocos = T.blocos_de_telhas(lista_telhas, C.BLOCO_TELHAS)
    falhas = []

    def um(bx, by, bloco):
        grade = T.grade_bloco(bx, by, C.ZOOM_BASE, bloco)
        for tent in range(4):
            try:
                a = G.baixar_bloco(imagem, grade)
                return {k: T.cortar_bloco(a[k], bx, by, bloco) for k in ('turb_x10', 'dias', 'sensor', 'trecho')}
            except Exception as e:
                if 'memory' in str(e).lower() and bloco > 1:
                    raise MemoryError(str(e))
                time.sleep(5 * (tent + 1))
                erro = e
        raise erro

    def por_telha(r):
        return {xy: {k: r[k][xy] for k in r} for xy in r['turb_x10']}

    t0 = time.time()
    with ThreadPoolExecutor(C.PEDIDOS_PARALELOS) as ex:
        fut = {ex.submit(um, bx, by, C.BLOCO_TELHAS): (bx, by) for (bx, by) in blocos}
        for i, f in enumerate(as_completed(fut), 1):
            bx, by = fut[f]
            try:
                acum.adicionar(por_telha(f.result()))
            except MemoryError:
                # bloco pesado demais: refaz telha por telha
                for (x, y) in blocos[(bx, by)]:
                    try:
                        acum.adicionar(por_telha(um(x, y, 1)))
                    except Exception as e:
                        falhas.append([x, y, str(e)[:120]])
            except Exception as e:
                falhas.append([bx, by, str(e)[:120]])
            if i % 25 == 0 or i == len(blocos):
                print(f'  {i}/{len(blocos)} blocos ({time.time() - t0:.0f} s, {len(falhas)} falhas)')
    return falhas


def main():
    import gee as G
    G.iniciar()
    hoje = date.today()
    cfg = json.loads((C.ESTATICO / 'config.json').read_text())
    lista = [tuple(t) for t in json.loads((C.ESTATICO / 'telhas_agua.json').read_text())]
    limite = int(os.environ.get('LIMITE_TELHAS') or 0)
    if limite:   # teste rápido: só as telhas mais próximas de Palmas
        px, py = T.lonlat_para_pixel(-48.36, -10.18, C.ZOOM_BASE)
        lista = sorted(lista, key=lambda t: (t[0] - px / 256) ** 2 + (t[1] - py / 256) ** 2)[:limite]
        print(f'MODO TESTE: só {len(lista)} telhas perto de Palmas')
    trechos = [f['properties'] for f in json.loads((C.SITE_DADOS / 'trechos.geojson').read_text())['features']]
    pontos = pd.read_csv(C.DADOS / 'pontos_semarh.csv')
    info = json.loads((C.MODELO / 'modelo_v5_info.json').read_text())

    estado = G.estado()
    estaticos = {'agua': G.mascara_agua(estado), 'araguaia': G.imagem_araguaia(cfg['araguaia_hybas8']),
                 'trecho': G.imagem_trechos(cfg['campo_id'], trechos), 'modelo': G.classificador()}
    imagem, resumo = G.imagem_resultado(estado, estaticos, hoje)
    print(f"Imagens: {resumo['datas_s2']} datas Sentinel-2, {resumo['datas_landsat']} datas Landsat | "
          f"chuva IMERG até {resumo['chuva_imerg_mais_recente_utc']} UTC | {len(lista)} telhas com água")

    t0 = time.time()
    acum = Acumulador()
    falhas = baixar_tudo(imagem, lista, acum)
    minutos = (time.time() - t0) / 60
    base, dias, por_trecho = acum.base, acum.dias, acum.por_trecho()
    if not base:
        sys.exit('Nenhuma estimativa foi gerada; o site não foi alterado.')

    n_png = T.piramide(base, C.ZOOM_BASE, C.ZOOM_MIN, C.SAIDA_TELHAS)
    at_trechos = resumir_trechos(por_trecho, hoje, info)
    at_pontos = estimar_pontos(base, dias, pontos, hoje, info)

    todos = np.concatenate([a[a > 0] for a in base.values()])
    idade = np.concatenate([d[b > 0] for b, d in zip(base.values(), dias.values())]) - 1
    meta = {
        'atualizado_em': datetime.now(timezone.utc).isoformat(timespec='minutes'),
        'janela_dias': C.DIAS_BUSCA, **resumo,
        'telhas_calculadas': len(lista), 'telhas_com_estimativa': len(base), 'telhas_png': n_png,
        'falhas': len(falhas), 'minutos_download': round(minutos, 1),
        'pixels_com_estimativa': int(todos.size),
        'classes_pct': {n: round(100 * float(np.mean(np.digitize(todos / 10, C.LIMITES_CLASSE) == i)), 1)
                        for i, n in enumerate(C.NOMES_CLASSE)},
        'idade_mediana_dias': int(np.median(idade)),
        'imagem_mais_recente': (hoje - timedelta(days=int(idade.min()))).isoformat(),
        'trechos_com_estimativa': len(at_trechos), 'trechos_total': len(trechos),
        'zoom_telhas': [C.ZOOM_MIN, C.ZOOM_BASE],
        'modelo': info['versao'],
    }
    C.SITE_DADOS.mkdir(parents=True, exist_ok=True)
    for nome, obj in (('atual_trechos', at_trechos), ('atual_pontos', at_pontos), ('metadados', meta)):
        (C.SITE_DADOS / f'{nome}.json').write_text(json.dumps(obj, ensure_ascii=False, indent=None), encoding='utf-8')
    if falhas:
        (C.RAIZ / '_falhas.json').write_text(json.dumps(falhas))

    novos = pd.DataFrame([{'ponto': int(k), 'data_imagem': v['data_imagem'], 'turb_ntu': v['turb_ntu']}
                          for k, v in at_pontos.items() if v['status'] == 'ok'])
    arq = C.DADOS / 'historico_app.csv'
    if len(novos):
        hist = pd.concat([pd.read_csv(arq), novos]) if arq.exists() else novos
        hist.drop_duplicates(['ponto', 'data_imagem'], keep='last').sort_values(['ponto', 'data_imagem']).to_csv(arq, index=False)
    print(f"✅ {meta['pixels_com_estimativa']:,} pixels | {n_png} telhas PNG | {len(at_trechos)} trechos | "
          f"{sum(v['status'] == 'ok' for v in at_pontos.values())} pontos | {len(falhas)} falhas | {minutos:.1f} min")


if __name__ == '__main__':
    main()
