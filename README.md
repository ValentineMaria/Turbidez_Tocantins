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

## Publicar pela primeira vez

### 1. Repositório

1. No GitHub, crie um repositório **público** (por exemplo, `turbidez-tocantins`).
2. **Add file → Upload files** e arraste todo o conteúdo desta pasta, **menos** `workflows_github`.
3. Crie as tarefas automáticas: **Add file → Create new file**, nome `.github/workflows/preparar.yml`, cole o conteúdo de `workflows_github/preparar.yml` e salve. Repita com `atualizar.yml` e `publicar.yml`.

### 2. Conta de serviço do Earth Engine

1. Em [console.cloud.google.com](https://console.cloud.google.com), projeto **monitoramento-de-turbidez-01**: **IAM e administrador → Contas de serviço → Criar conta de serviço** (nome: `turbidez-app`).
2. Papéis: **Earth Engine Resource Writer** (o app salva o modelo como asset) e **Service Usage Consumer**.
3. Na conta criada: **Chaves → Adicionar chave → JSON**. Guarde o arquivo; ele é uma senha.

### 3. Chave no GitHub

**Settings → Secrets and variables → Actions → New repository secret**. Nome: `GEE_SERVICE_ACCOUNT_KEY`. Valor: todo o conteúdo do `.json`.

### 4. Site

**Settings → Pages → Build and deployment → Source: GitHub Actions.**

### 5. Rodar

Na aba **Actions**:

1. **Preparar (uma vez) → Run workflow.** Leva de 30 minutos a 2 horas. No log, confira a linha "Validação da bacia".
2. **Atualizar mapa → Run workflow**, escrevendo `60` no campo *limite_telhas*. É um teste rápido (~10 min) só com a região de Palmas. No fim, o endereço do site aparece no resumo da execução (`https://SEU-USUARIO.github.io/turbidez-tocantins/`).
3. Se o teste deu certo, rode **Atualizar mapa** de novo com o campo vazio, para o estado inteiro. A primeira vez leva de 1 a 3 horas.

Depois disso, a atualização roda sozinha a cada 3 dias.

## Coletas novas da SEMARH

1. Abra `dados/coletas_semarh.csv` no GitHub (ícone de lápis) ou baixe, edite e envie de volta.
2. Acrescente uma linha por coleta: `ponto,data,turbidez_ntu,fonte`, por exemplo `12,2026-03-18,8.4,Boletim SEMARH 2026_01`. A data vai no formato ano-mês-dia; o decimal pode ser ponto ou vírgula.
3. Salve (*Commit changes*). A tarefa **Publicar site** roda sozinha e o gráfico do ponto é atualizado em poucos minutos.

Coletas novas também servem para **retreinar o modelo** mais adiante: com elas, o projeto de modelagem gera um novo `arvores_v5.json` e `modelo_v5_info.json` (script `modelo/converter_arvores.py`).

## Cota do Earth Engine

O protótipo de 15/11/2026 mediu ~4 EECU-hora por atualização do estado. A cada 3 dias, são ~40 EECU-hora por mês, dentro do nível gratuito *Community* (150 por mês).

**Para atualizar todo dia**, vincule uma conta de faturamento ao projeto (nível *Contributor*: 1.000 EECU-hora por mês, sem cobrança do Earth Engine para uso não comercial) e, em `.github/workflows/atualizar.yml`, troque `'17 9 */3 * *'` por `'17 9 * * *'`. O consumo de cada mês aparece no Google Cloud em **Earth Engine → Uso**.

## Ver o site no seu computador

```bash
python -m http.server -d site
```

e abra `http://localhost:8000`. Sem as telhas, aparecem só os pontos e as séries.
