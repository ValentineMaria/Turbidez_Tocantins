"""Converte o Extra Trees (scikit-learn) em textos de árvore no formato aceito pelo Earth Engine
(ee.Classifier.decisionTreeEnsemble), o mesmo formato do pacote R 'rpart'.
Uso: python converter_arvores.py modelo_turbidez_app_v5.joblib arvores_v5.json
"""
import json, sys


def arvore_para_texto(est, nomes):
    t = est.tree_
    linhas = [f'1) root {t.n_node_samples[0]} 9999 {float(t.value[0].ravel()[0]):.7g}']

    def desce(no, num, prof):
        esq, dir_ = t.children_left[no], t.children_right[no]
        if esq == dir_:
            return
        var, lim = nomes[t.feature[no]], float(t.threshold[no])
        for filho, n_filho, sinal in ((esq, 2 * num, '<='), (dir_, 2 * num + 1, '>')):
            folha = t.children_left[filho] == t.children_right[filho]
            v = float(t.value[filho].ravel()[0])
            linhas.append(f"{'  ' * prof}{n_filho}) {var}{sinal}{lim:.9g} {t.n_node_samples[filho]} 0 {v:.7g}{' *' if folha else ''}")
            desce(filho, n_filho, prof + 1)

    desce(0, 1, 1)
    return '\n'.join(linhas)


def avaliar_texto(texto, x):
    """Lê o texto como o Earth Engine faria e devolve a previsão de uma árvore (para conferência)."""
    nos = {}
    for ln in texto.splitlines():
        num, resto = ln.strip().split(') ', 1)
        partes = resto.split(' ')
        nos[int(num)] = (partes[0], float(partes[3]), ln.endswith('*'))
    n = 1
    while True:
        filhos = [k for k in (2 * n, 2 * n + 1) if k in nos]
        if not filhos:
            return nos[n][1]
        for k in filhos:
            cond = nos[k][0]
            op = '<=' if '<=' in cond else '>'
            var, lim = cond.split(op)
            if (x[var] <= float(lim)) == (op == '<='):
                n = k
                break


if __name__ == '__main__':
    import joblib   # só para converter um modelo novo (precisa do scikit-learn da mesma versão do treino)
    pk = joblib.load(sys.argv[1])
    nomes = pk['variaveis']
    textos = [arvore_para_texto(e, nomes) for e in pk['modelo'].estimators_]
    json.dump({'variaveis': nomes, 'arvores': textos, 'versao': pk['versao']}, open(sys.argv[2], 'w'))
    print(len(textos), 'árvores;', round(sum(map(len, textos)) / 1e6, 2), 'MB de texto')
