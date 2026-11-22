"""Parâmetros do app. Os de imagem são os mesmos do treino do modelo v5."""
import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SITE = RAIZ / 'site'
SITE_DADOS = SITE / 'data'
ESTATICO = RAIZ / 'pipeline' / 'estatico'      # gerado uma vez pelo preparar.py
MODELO = RAIZ / 'modelo'
DADOS = RAIZ / 'dados'
SAIDA_TELHAS = RAIZ / '_telhas'                 # telhas geradas a cada atualização (não vão para o main)

PROJETO = os.environ.get('GEE_PROJECT') or 'monitoramento-de-turbidez-01'
ASSET_ARVORES = f'projects/{PROJETO}/assets/turbidez_v5_arvores'

# ---------- janela de imagens ----------
DIAS_BUSCA = 30            # imagem limpa mais recente dos últimos 30 dias

# ---------- máscara de água (protótipo de 15/11/2026) ----------
OCORRENCIA_MIN = 50        # % do tempo com água (JRC)
VAZAO_MIN = 20             # m³/s (HydroSHEDS)
BUFFER_RIO_M = 10000       # alcança as margens dos reservatórios
MIN_CORPO_PIX = 256        # descarta açudes/lagoas isolados (< ~23 ha)
LARGURA_MIN_M = 60         # domínio do modelo
BUFFER_TRECHO_M = 1500     # pixels usados para resumir cada trecho

# ---------- filtros de qualidade do treino ----------
QC = dict(max_B3=0.25, min_B4=0.002, max_B8A_B4=1.3, max_cv_B4=0.5, min_pix_s2=3, min_pix_landsat=2,
          nuvem_s2cloudless=40)

# ---------- telhas ----------
ZOOM_BASE = 13             # ~19 m por pixel em Tocantins; o mapa amplia até o zoom 15 a partir daqui
ZOOM_MIN = 9
BLOCO_TELHAS = 1           # um pedido por telha (≈ 5 x 5 km): processa menos terra em volta dos rios
PEDIDOS_PARALELOS = 4      # o nível gratuito do Earth Engine limita pedidos simultâneos

# ---------- classes ----------
LIMITES_CLASSE = (5, 15)   # NTU
NOMES_CLASSE = ('clara', 'moderada', 'turva')
