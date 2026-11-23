# Turbidez dos rios do Tocantins

Mapa da turbidez da água estimada por satélite nos rios do Tocantins, com a série histórica oficial dos pontos de monitoramento da SEMARH-TO.

- **Visão do estado:** rios coloridos por trecho, em três classes (clara, moderada, turva).
- **Ao aproximar:** a água pixel a pixel (~19 m), com a imagem sem nuvem mais recente.
- **Ao tocar num ponto da SEMARH:** a estimativa atual, o gráfico com as medições oficiais e as estimativas do satélite nas mesmas datas, e o quanto o satélite acerta naquele ponto.

Tudo roda de graça: o GitHub Actions faz o processamento, o GitHub Pages hospeda o site e o Earth Engine fornece as imagens (uso não comercial).

## Como funciona

```
GitHub Actions — a cada 3 dias                                   GitHub Pages
┌───────────────────────────────────────────────────────────┐    ┌───────────────────────┐
│ Earth Engine, em cada pixel de água:                      │    │ site/ (Leaflet)       │
│   imagem limpa mais recente (Sentinel-2 ou Landsat 8/9)   │    │  + telhas/ (PNG)      │
│   → mediana 3 x 3 → filtros de qualidade                  │──▶ │  + data/ (JSON)       │
│   → modelo v5 (400 árvores, dentro do Earth Engine)       │    │                       │
│ Python: telhas PNG (zoom 9–13), resumo por trecho,        │    └───────────────────────┘
│   estimativa nos pontos (com o ajuste de cada ponto)      │
└───────────────────────────────────────────────────────────┘
```

**O modelo** é o v5 (Sentinel-2 + Landsat 8/9 + chuva de 7 dias + ajuste por ponto). As 400 árvores do Extra Trees foram convertidas para o formato do Earth Engine (`modelo/arvores_v5.json`); `pipeline/testar.py` confere a cada execução que elas reproduzem o modelo original.

**Confiabilidade** (validação com cada ano inteiro deixado de fora, 468 coletas em 45 pontos):

| | Classe certa | A menos de 2× do medido | R² (log) |
|---|---|---|---|
| Modelo geral (trechos e pixels) | 69% | 69% | 0,51 |
| Com ajuste do ponto (pontos SEMARH com histórico) | 72% | 72% | 0,58 |

Em 8 de cada 10 casos, o valor real fica entre 0,4× e 2,4× a estimativa. O site mostra essa faixa, e não um valor exato. Acima de 40 NTU, o modelo subestima.

## Estrutura

| Pasta / arquivo | O que é |
|---|---|
| `pipeline/preparar.py` | Roda **uma vez**: limite do estado, trechos de rio, bacia do Araguaia, lista de telhas com água, modelo como asset |
| `pipeline/atualizar.py` | Roda **a cada 3 dias**: mapa pixel a pixel, telhas, trechos, pontos |
| `pipeline/series.py` | Séries e confiabilidade dos pontos, a partir de `dados/` (sem Earth Engine) |
| `pipeline/gee.py` | Tudo o que roda no Earth Engine |
| `pipeline/telhas.py` | Contas das telhas, cores, pirâmide de zooms |
| `pipeline/testar.py` | Testes que rodam sem o Earth Engine |
| `pipeline/config.py` | Parâmetros (janela de dias, máscara de água, zooms...) |
| `modelo/` | Árvores do modelo v5, ajustes por ponto e intervalos |
| `dados/coletas_semarh.csv` | **Medições oficiais da SEMARH** (é aqui que entram as coletas novas) |
| `dados/validacao_v5.csv` | Estimativas do modelo nas datas das coletas (validação) |
| `dados/historico_app.csv` | Estimativas que o app já fez nos pontos (cresce a cada atualização) |
| `site/` | O site |
| `workflows_github/` | As três tarefas automáticas (vão para `.github/workflows/` no GitHub) |
