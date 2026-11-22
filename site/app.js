// Turbidez dos rios do Tocantins
(function () {
  'use strict';

  // mesmas paradas de cor das telhas (pipeline/telhas.py)
  const PARADAS = [[1, '#1d6a86'], [3, '#3b8fa3'], [5, '#7fa98f'], [10, '#ad9a42'], [15, '#b47a33'], [30, '#8c4a22'], [60, '#62321a']];
  const CLASSES = {
    clara: { nome: 'Água clara', faixa: 'abaixo de 5 NTU', v: '--clara' },
    moderada: { nome: 'Turbidez moderada', faixa: 'entre 5 e 15 NTU', v: '--moderada' },
    turva: { nome: 'Água turva', faixa: 'acima de 15 NTU', v: '--turva' },
  };
  const NOME_CURTO = { clara: 'clara', moderada: 'moderada', turva: 'turva' };
  const MESES = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];

  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const cor = (c) => css(c ? CLASSES[c].v : '--sem');
  const num = (x) => x.toLocaleString('pt-BR', { maximumFractionDigits: x < 10 ? 1 : 0 });
  const dataBR = (iso) => { const [a, m, d] = iso.split('-'); return `${+d} ${MESES[m - 1]} ${a}`; };
  const dias = (iso) => Math.round((Date.now() - new Date(iso + 'T12:00:00')) / 864e5);
  const ha = (iso) => { const d = dias(iso); return d <= 0 ? 'hoje' : d === 1 ? 'ontem' : `há ${d} dias`; };
  const pos = (v) => (Math.log10(v) - Math.log10(1)) / (Math.log10(60) - Math.log10(1)) * 100;
  const classeDe = (t) => (t < 5 ? 'clara' : t < 15 ? 'moderada' : 'turva');
  const pct = (x) => `${Math.round(x)}%`;

  async function json(url, opcional) {
    try {
      const r = await fetch(url, { cache: 'no-cache' });
      if (!r.ok) throw new Error(r.status);
      return await r.json();
    } catch (e) { if (opcional) return null; throw e; }
  }

  // ---------------- mapa ----------------
  const largo = innerWidth > 720;
  const mapa = L.map('mapa', { zoomControl: false, minZoom: 5, maxZoom: 17 })
    .fitBounds([[-13.5, -50.8], [-5.1, -45.6]], { paddingTopLeft: largo ? [400, 0] : [0, 0], paddingBottomRight: largo ? [0, 0] : [0, 140] });
  L.control.zoom({ position: 'topright' }).addTo(mapa);
  L.control.scale({ imperial: false, position: 'bottomright' }).addTo(mapa);
  // OpenStreetMap (sem chave). No tema escuro, o CSS escurece só o fundo (variável --filtro-fundo).
  const fundos = {
    mapa: L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">colaboradores do OpenStreetMap</a>',
      maxZoom: 19, className: 'fundo-osm' }),
    satelite: L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
      attribution: 'Imagens &copy; Esri, Maxar, Earthstar Geographics', maxZoom: 19 }),
  };
  let fundo = fundos.mapa.addTo(mapa);
  document.querySelectorAll('[data-fundo]').forEach((b) => b.addEventListener('click', () => {
    mapa.removeLayer(fundo); fundo = fundos[b.dataset.fundo].addTo(mapa); fundo.bringToBack();
    document.querySelectorAll('[data-fundo]').forEach((o) => o.setAttribute('aria-pressed', o === b));
  }));
  const tela = L.canvas({ tolerance: 8 });

  // camada pixel a pixel: só pede as telhas que existem (índice gerado pelo robô)
  function camadaPixels(indice) {
    const tem = {};
    Object.entries(indice).forEach(([z, l]) => { tem[z] = new Set(l); });
    const zs = Object.keys(tem).map(Number);
    const Camada = L.TileLayer.extend({
      createTile(c, feito) {
        if (!tem[c.z] || !tem[c.z].has(`${c.x}/${c.y}`)) {
          const d = document.createElement('div'); setTimeout(() => feito(null, d), 0); return d;
        }
        return L.TileLayer.prototype.createTile.call(this, c, feito);
      },
    });
    return new Camada('telhas/{z}/{x}/{y}.png', {
      minZoom: Math.min(...zs), minNativeZoom: Math.min(...zs), maxNativeZoom: Math.max(...zs), maxZoom: 17,
      opacity: 0.95, zIndex: 5, attribution: 'Turbidez: modelo v5 (SEMARH-TO, Sentinel-2, Landsat, IMERG)',
    });
  }

  // ---------------- estado ----------------
  let D = {}, camTrechos = null, camPontos = null, temPixels = false, selec = null;
  const painel = document.getElementById('painel');

  async function iniciar() {
    const [meta, trechos, atT, pontos, atP, series, conf, indice] = await Promise.all([
      json('data/metadados.json', true), json('data/trechos.geojson', true), json('data/atual_trechos.json', true),
      json('data/pontos.geojson'), json('data/atual_pontos.json', true), json('data/series.json'),
      json('data/confiabilidade.json'), json('telhas/indice.json', true)]);
    D = { meta, atT: atT || {}, atP: atP || {}, series, conf, pontos: {} };
    pontos.features.forEach((f) => { D.pontos[f.properties.ponto] = f.properties; });

    montarEscala();
    textoAtualizacao();
    document.getElementById('acerto-geral').textContent = pct(conf.geral.classe_certa_pct);
    document.getElementById('acerto-ajuste').textContent = pct(conf.ajustado.classe_certa_pct);
    document.getElementById('n-calib').textContent = conf.n_coletas;

    if (indice && Object.keys(indice).length) { camadaPixels(indice).addTo(mapa); temPixels = true; }

    if (trechos) {
      camTrechos = L.geoJSON(trechos, {
        renderer: tela, style: estiloTrecho,
        onEachFeature: (f, l) => {
          l.bindTooltip(() => rotuloTrecho(f.properties), { sticky: true });
          l.on('click', (e) => { L.DomEvent.stop(e); selecionar('trecho', f, l); });
        },
      }).addTo(mapa);
    }
    camPontos = L.geoJSON(pontos, {
      pointToLayer: (f, ll) => L.circleMarker(ll, estiloPonto(f)),
      onEachFeature: (f, l) => {
        l.bindTooltip(() => rotuloPonto(f.properties));
        l.on('click', (e) => { L.DomEvent.stop(e); selecionar('ponto', f, l); });
      },
    }).addTo(mapa);
    mapa.on('zoomend', () => camTrechos && camTrechos.setStyle(estiloTrecho));
  }

  function textoAtualizacao() {
    const m = D.meta, el = document.getElementById('atualizacao');
    if (!m) { el.textContent = 'O mapa por satélite aparece depois da primeira atualização. Os pontos já mostram as medições oficiais.'; return; }
    const quando = new Date(m.atualizado_em).toLocaleDateString('pt-BR', { day: 'numeric', month: 'long' });
    el.textContent = `Estimativa por satélite atualizada em ${quando}, com imagens de até ${m.janela_dias} dias. ` +
      `A mais recente é de ${dataBR(m.imagem_mais_recente)}.`;
    const c = m.classes_pct;
    document.getElementById('resumo').textContent =
      `Na água mapeada: ${pct(c.clara)} clara, ${pct(c.moderada)} moderada e ${pct(c.turva)} turva.`;
  }

  function montarEscala() {
    const g = PARADAS.map(([v, c]) => `${c} ${pos(v).toFixed(1)}%`).join(', ');
    document.getElementById('barra').style.background = `linear-gradient(90deg, ${g})`;
    const marcas = [1, 5, 15, 40].map((v) => `<span style="left:${pos(v)}%">${v}${v === 40 ? ' NTU' : ''}</span>`);
    const nomes = [['clara', 2.2], ['moderada', 8.7], ['turva', 31]].map(([n, v]) => `<span class="cls" style="left:${pos(v)}%">${n}</span>`);
    document.getElementById('marcas').innerHTML = marcas.join('') + nomes.join('');
  }

  // ---------------- estilos ----------------
  function estiloTrecho(f) {
    const p = f.properties, a = D.atT[p.idx], perto = mapa.getZoom() >= 10 && temPixels, sel = selec === f;
    const w = Math.max(2, Math.min(7, 2 + 2.4 * Math.log10((p.largura_m || 60) / 60)));
    return {
      color: sel ? css('--tinta') : cor(a && a.classe), weight: perto ? (sel ? 3 : 1.2) : w + (sel ? 2 : 0),
      opacity: perto ? (sel ? 0.9 : 0.35) : (a ? 0.95 : 0.55), lineCap: 'round', dashArray: perto && !sel ? '4 4' : null,
    };
  }
  function estiloPonto(f) {
    const p = f.properties, a = D.atP[p.ponto], ok = a && a.status === 'ok', sel = selec === f;
    return {
      radius: sel ? 9 : 6, weight: sel ? 3 : 1.5, color: p.no_dominio ? css('--superficie') : css('--tinta-2'),
      fillColor: ok ? cor(a.classe) : css('--sem'), fillOpacity: p.no_dominio ? 1 : 0.15,
    };
  }

  // ---------------- textos ----------------
  function nomeTrecho(p) {
    const pp = p.ponto_proximo && D.pontos[p.ponto_proximo];
    return pp ? { rio: pp.rio, local: `Trecho perto de ${pp.municipio}, bacia do ${p.bacia.replace('Rio ', '')}` }
      : { rio: 'Trecho de rio', local: `Bacia do ${p.bacia.replace('Rio ', '')}` };
  }
  function rotuloTrecho(p) {
    const a = D.atT[p.idx], n = nomeTrecho(p);
    return a ? `${n.rio}: ${NOME_CURTO[a.classe]}, cerca de ${num(a.turb_ntu)} NTU` : `${n.rio}: sem imagem limpa recente`;
  }
  function rotuloPonto(p) {
    const a = D.atP[p.ponto];
    const est = a && a.status === 'ok' ? `cerca de ${num(a.turb_ntu)} NTU` : p.no_dominio ? 'sem imagem limpa recente' : 'só medições oficiais';
    return `Ponto ${p.ponto}, ${p.rio}: ${est}`;
  }

  function blocoConfiabilidade(classe, tipo) {
    const c = D.conf[tipo].por_classe_estimada[classe];
    if (!c) return '';
    const partes = ['clara', 'moderada', 'turva'].map((k) => [k, c.real_pct[k] || 0]).filter(([, v]) => v > 0);
    return `<div class="conf">
      <h3>Quão confiável é essa classe</h3>
      <div class="pilha" role="img" aria-label="Distribuição das medições reais">${partes.map(([k, v]) =>
        `<span style="flex:${v};background:${cor(k)}" title="${NOME_CURTO[k]}: ${pct(v)}"></span>`).join('')}</div>
      <p>Quando o satélite indicou água ${NOME_CURTO[classe]}${tipo === 'ajustado' ? ' em pontos com histórico' : ''}, a medição de campo
        confirmou em <strong>${pct(c.acerto_pct)}</strong> das ${c.n} vezes.</p>
      <p class="leg">${partes.map(([k, v]) => `${pct(v)} ${NOME_CURTO[k]}`).join(', ')}</p>
    </div>`;
  }

  function blocoEstimativa(a, extra) {
    return `<div class="estimativa">
      <span class="classe" style="--cor:${cor(a.classe)}">${CLASSES[a.classe].nome}</span>
      <span class="valor">${num(a.faixa_80[0])} a ${num(a.faixa_80[1])} <small>NTU</small></span>
      <p class="nota">Estimativa central de ${num(a.turb_ntu)} NTU. Em 8 de cada 10 casos, o valor real fica nessa faixa.${extra || ''}</p>
    </div>`;
  }

  // ---------------- seleção ----------------
  function selecionar(tipo, f, l) {
    const ant = selec; selec = f;
    if (camTrechos) camTrechos.setStyle(estiloTrecho);
    camPontos.eachLayer((x) => { if (x.feature === ant || x.feature === f) x.setStyle(estiloPonto(x.feature)); });
    if (l.bringToFront) l.bringToFront();
    document.getElementById('detalhe').innerHTML = tipo === 'trecho' ? htmlTrecho(f.properties) : htmlPonto(f.properties);
    painel.classList.add('com-detalhe');
    document.getElementById('corpo').scrollTop = 0;
  }

  function htmlTrecho(p) {
    const a = D.atT[p.idx], n = nomeTrecho(p);
    let h = `<h2>${n.rio}</h2><p class="local">${n.local}</p>`;
    if (a) {
      h += blocoEstimativa(a);
      h += `<dl class="dados">
        <dt>Imagem</dt><dd>${dataBR(a.data_imagem)} (${ha(a.data_imagem)})${a.landsat_pct > 50 ? ', Landsat' : ''}</dd>
        <dt>Variação no trecho</dt><dd>${num(a.p10_p90[0])} a ${num(a.p10_p90[1])} NTU entre os pixels</dd>
        <dt>Largura</dt><dd>cerca de ${num(p.largura_m)} m</dd>
        <dt>Extensão</dt><dd>${num(p.comprimento_km)} km</dd></dl>`;
      h += blocoConfiabilidade(a.classe, 'geral');
    } else {
      h += `<p class="nota" style="margin-top:12px">Sem imagem de satélite limpa nos últimos 30 dias, em geral por nuvem.</p>
        <dl class="dados"><dt>Largura</dt><dd>cerca de ${num(p.largura_m)} m</dd><dt>Extensão</dt><dd>${num(p.comprimento_km)} km</dd></dl>`;
    }
    if (p.ponto_proximo) h += `<p class="nota" style="margin-top:14px">O ponto ${p.ponto_proximo} da SEMARH fica perto deste trecho. Toque nele para ver as medições oficiais.</p>`;
    return h;
  }

  function htmlPonto(p) {
    const a = D.atP[p.ponto], s = D.series[p.ponto] || {};
    let h = `<h2>${p.rio}</h2><p class="local">Ponto ${p.ponto} da SEMARH, ${p.municipio}</p>`;
    if (a && a.status === 'ok') {
      const aj = a.ajuste_ponto && p.fator_ajuste
        ? ` Corrigida pelo histórico deste ponto: o satélite ${p.fator_ajuste < 1 ? 'costuma superestimar' : 'costuma subestimar'} aqui (fator ${num(p.fator_ajuste)}).` : '';
      h += blocoEstimativa(a, aj);
      h += `<dl class="dados"><dt>Imagem</dt><dd>${dataBR(a.data_imagem)} (${ha(a.data_imagem)})</dd>`;
    } else if (!p.no_dominio) {
      h += `<p class="nota" style="margin-top:12px">Rio com menos de 60 m de largura: o satélite não estima a turbidez aqui. O gráfico mostra as medições oficiais.</p><dl class="dados">`;
    } else {
      h += `<p class="nota" style="margin-top:12px">Sem imagem de satélite limpa nos últimos 30 dias, ou o mapa por satélite ainda não foi gerado.</p><dl class="dados">`;
    }
    if (p.ultima_medicao) h += `<dt>Última medição</dt><dd>${num(p.ultima_medicao[1])} NTU em ${dataBR(p.ultima_medicao[0])}</dd>`;
    h += `<dt>Medições oficiais</dt><dd>${p.n_medicoes}</dd></dl>`;
    h += `<h3>Série histórica</h3><div class="grafico">${grafico(s)}</div>`;
    if (p.acerto) {
      h += `<p class="nota" style="margin-top:8px">Nas ${p.acerto.n} coletas em que havia imagem de satélite, a estimativa acertou a classe em
        <strong>${p.acerto.classe_certa}</strong> e ficou a menos de 2 vezes do valor medido em ${p.acerto.dentro_fator2}
        (teste com o ano da coleta deixado de fora do modelo).</p>`;
    }
    if (a && a.status === 'ok') h += blocoConfiabilidade(a.classe, a.ajuste_ponto ? 'ajustado' : 'geral');
    return h;
  }

  // ---------------- gráfico (SVG, escala log) ----------------
  function grafico(s) {
    const med = (s.medicoes || []).map(([d, v]) => [d, v]);
    const val = (s.validacao || []).map(([d, , e]) => [d, e]);
    const app = (s.app || []).map(([d, v]) => [d, v]);
    const todos = med.concat(val, app);
    if (!todos.length) return '<p class="nota">Sem medições registradas.</p>';
    const W = 340, H = 180, m = { l: 30, r: 8, t: 8, b: 22 };
    const t = (d) => new Date(d + 'T12:00:00').getTime();
    const t0 = Math.min(...todos.map(([d]) => t(d))), t1 = Math.max(Date.now(), ...todos.map(([d]) => t(d)));
    const vmax = Math.max(20, ...todos.map(([, v]) => v)) * 1.25, vmin = Math.min(0.5, ...todos.map(([, v]) => v)) * 0.8;
    const x = (d) => m.l + (t(d) - t0) / (t1 - t0 || 1) * (W - m.l - m.r);
    const y = (v) => H - m.b - (Math.log10(Math.max(v, vmin)) - Math.log10(vmin)) / (Math.log10(vmax) - Math.log10(vmin)) * (H - m.t - m.b);
    let g = '';
    [1, 5, 15, 50, 150].filter((v) => v > vmin && v < vmax).forEach((v) => {
      g += `<line class="grade" x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"/>`;
      g += `<text x="${m.l - 4}" y="${y(v) + 3}" text-anchor="end">${v}</text>`;
    });
    const a0 = new Date(t0).getFullYear(), a1 = new Date(t1).getFullYear(), passo = a1 - a0 > 6 ? 2 : 1;
    for (let a = a0 + 1; a <= a1; a += passo) {
      const xx = x(`${a}-01-01`);
      if (xx > m.l + 10 && xx < W - m.r - 5) g += `<text x="${xx}" y="${H - 6}" text-anchor="middle">${a}</text>`;
    }
    let lim = '';
    [5, 15].filter((v) => v > vmin && v < vmax).forEach((v) => {
      lim += `<line class="limite" x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"/>`;
    });
    const tit = (d, v, o) => `<title>${o}: ${num(v)} NTU em ${dataBR(d)}</title>`;
    const pv = val.map(([d, v]) => `<circle cx="${x(d)}" cy="${y(v)}" r="3.4" fill="none" stroke="${css('--clara')}" stroke-width="1.4">${tit(d, v, 'Satélite (validação)')}</circle>`).join('');
    const pm = med.map(([d, v]) => `<circle cx="${x(d)}" cy="${y(v)}" r="2.6" fill="${css('--medido')}">${tit(d, v, 'Medição SEMARH')}</circle>`).join('');
    const pa = app.map(([d, v]) => `<rect x="${x(d) - 3}" y="${y(v) - 3}" width="6" height="6" fill="${cor(classeDe(v))}">${tit(d, v, 'Satélite (app)')}</rect>`).join('');
    const svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Turbidez ao longo do tempo, em escala logarítmica">
      <g class="eixo">${g}</g>${lim}${pv}${pm}${pa}
      <text class="lim-txt" x="${W - m.r}" y="${y(5) - 3}" text-anchor="end">5 NTU</text>
      <text class="lim-txt" x="${W - m.r}" y="${y(15) - 3}" text-anchor="end">15 NTU</text></svg>`;
    const leg = `<div class="legenda-graf">
      <span><svg viewBox="0 0 12 12"><circle cx="6" cy="6" r="3.5" fill="${css('--medido')}"/></svg>Medição SEMARH</span>
      ${val.length ? `<span><svg viewBox="0 0 12 12"><circle cx="6" cy="6" r="4" fill="none" stroke="${css('--clara')}" stroke-width="1.5"/></svg>Satélite na mesma data</span>` : ''}
      ${app.length ? `<span><svg viewBox="0 0 12 12"><rect x="2" y="2" width="8" height="8" fill="${css('--moderada')}"/></svg>Satélite (atualizações do app)</span>` : ''}
    </div>`;
    return svg + leg;
  }

  document.getElementById('alca').addEventListener('click', (e) => {
    const aberto = painel.classList.toggle('aberto');
    e.currentTarget.setAttribute('aria-expanded', aberto);
  });

  iniciar().catch((e) => {
    document.getElementById('atualizacao').textContent = 'Não foi possível carregar os dados do mapa. Tente recarregar a página.';
    console.error(e);
  });
})();
