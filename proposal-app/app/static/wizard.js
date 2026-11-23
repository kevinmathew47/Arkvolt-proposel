/* Proposal wizard (spec §7). State lives in S; every change re-runs the engine on the server (/api/compute)
   so the live summary, prices and PDF always use the same numbers. */
(function () {
  const CTX = window.CTX, $ = (q, el = document) => el.querySelector(q), $$ = (q, el = document) => [...el.querySelectorAll(q)];
  const inr = v => v == null || isNaN(v) ? '—' : Math.round(v).toLocaleString('en-IN');
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  let pid = window.PID, sum = {}, step = 1, tCompute, tAdvise, tSave, dirty = false, seen = false, seenFor = '';
  const S = Object.assign({customer: {salutation: 'Mr.', site_same: true}, bill: {months: '2', phase: '1'}, options: []}, window.INPUTS || {});
  S.customer = S.customer || {}; S.bill = S.bill || {}; S.options = S.options || [];
  if (S.clarifications && !S.clarifications_text) S.clarifications_text = S.clarifications.join('\n');

  // ---------- binding ----------
  const get = (path) => path.split('.').reduce((o, k) => (o || {})[k], S);
  const set = (path, v) => { const ks = path.split('.'); let o = S; ks.slice(0, -1).forEach(k => o = o[k] = o[k] || {}); o[ks.at(-1)] = v; };
  function bindAll() {
    $$('[data-k]').forEach(el => {
      const v = get(el.dataset.k);
      if (el.type === 'checkbox') el.checked = !!v; else if (v != null) el.value = v;
      if (!el._b) { el._b = 1; el.addEventListener(el.tagName === 'SELECT' || el.type === 'checkbox' ? 'change' : 'input', () => {
        set(el.dataset.k, el.type === 'checkbox' ? el.checked : el.value); changed(el.dataset.k); }); }
    });
  }
  function billMode() {
    const nb = !!S.bill.no_bill;
    $('#nobill_box').style.display = nb ? '' : 'none';
    $('#bill_box').style.display = nb ? 'none' : '';
    $('#advice').style.display = nb ? 'none' : '';
  }
  function changed(k) {
    if (k === 'bill.no_bill') { if (S.bill.no_bill && !S.bill.tariff) set('bill.tariff', 'LT-1'); bindAll(); billMode(); }
    dirty = true; $('#saved').textContent = 'Unsaved changes';
    if (k && k.startsWith('bill.')) { clearTimeout(tAdvise); tAdvise = setTimeout(advise, 500); }
    $('#a_cno').textContent = S.bill.consumer_no || '—'; $('#a_ph').textContent = S.customer.phone || '—';
    clearTimeout(tCompute); tCompute = setTimeout(compute, 350);
    if (pid) { clearTimeout(tSave); tSave = setTimeout(() => save(true), 4000); }
  }
  function inputs() {
    const x = JSON.parse(JSON.stringify(S));
    x.clarifications = (S.clarifications_text || '').split('\n').map(s => s.trim()).filter(Boolean);
    return x;
  }
  async function api(url, body, raw) {
    const r = await fetch(url, {method: 'POST', headers: raw ? {} : {'Content-Type': 'application/json'}, body: raw ? body : JSON.stringify(body)});
    if (r.status === 401) { alert('Session ended — please log in again.'); location.href = '/login'; }
    const ct = r.headers.get('content-type') || '';
    const data = ct.includes('json') ? await r.json() : await r.text();
    return {ok: r.ok, data};
  }

  // ---------- steps ----------
  function go(n) {
    step = n;
    $$('.step').forEach(s => s.classList.toggle('on', +s.dataset.step === n));
    $$('#steps button').forEach(b => { b.classList.toggle('on', +b.dataset.s === n); if (+b.dataset.s < n) b.classList.add('done'); });
    if (n === 3) renderOpts(); if (n === 4) renderPrices(); if (n === 5) { renderReview(); preview(); }
    window.scrollTo({top: 0, behavior: 'smooth'});
  }
  $$('#steps button').forEach(b => b.onclick = () => go(+b.dataset.s));

  // ---------- live summary ----------
  async function compute() {
    const {data} = await api('/api/compute' + (pid ? `?pid=${pid}` : ''), inputs());
    sum = data || {};
    renderSummary();
    if (step === 3) renderOptStatus(); if (step === 4) renderPrices(); if (step === 5) renderReview();
    const f = sum.feasibility;
    $('#feas').innerHTML = f ? (f.ok ? `<span class="ok">✓ Enough transformer capacity for ${sum.kw_text || 'the plant'}</span>`
                                     : `<span class="bad">✗ Only ${f.balance_kw} kW left — less than the plant</span>`) : '';
  }
  function renderSummary() {
    const s = sum, b = s.brand;
    let h = `<div class="small" style="color:#9FB0C8">Brand</div><div class="brand ${b || ''}">${b === 'ADROIT' ? 'Adroit Universe' : b === 'ARKVOLT' ? 'ARKVOLT Energy' : '—'}</div>`;
    if (b) h += `<div class="small" style="color:#9FB0C8">${b === 'ADROIT' ? 'LT-1 domestic · PM Surya Ghar · net metering' : 'Non-domestic · no subsidy · net billing · 40% WDV'}</div>`;
    h += '<h3>Bill</h3>';
    h += kv('Use', s.cons ? inr(s.cons) + ' units/month' + (s.assumed ? ' <span style="color:#FFD27A">(assumed)</span>' : '') : '—') + kv('Bill today', s.bill_today_month ? '₹' + inr(s.bill_today_month) + '/month' : '—');
    h += kv('Subsidy', b === 'ADROIT' ? '₹' + inr(s.cfa_max) + ' ✓' : b ? 'Nil' : '—') + kv('Suggested', s.suggested_kwp ? s.suggested_kwp.toFixed(1) + ' kWp' : '—');
    if (s.feasibility) h += kv('Transformer', s.feasibility.ok ? '✓ OK' : '✗ short');
    if ((s.options || []).length) {
      h += '<h3>Options</h3>';
      s.options.forEach(o => h += kv(`${o.letter} · ${esc(o.label)} ${o.kwp.toFixed(2)} kWp`, o.payback_text ? `${o.payback_text}<div class="small" style="color:#9FB0C8">net ₹${inr(o.split && o.split.net_cost)}</div>` : '—'));
    }
    const li = (a, c) => (a || []).map(x => `<li class="${c}">${esc(x)}</li>`).join('');
    const all = li(s.errors, 'e') + li(s.warnings, 'w') + li(s.hints, 'h');
    if (all) h += `<h3>Checks</h3><ul>${all}</ul>`;
    $('#summary').innerHTML = h;
  }
  const kv = (k, v) => `<div class="kv"><span>${k}</span><b>${v}</b></div>`;

  // ---------- bill: photo / PDF / advisor ----------
  function fillFromBill(b) {
    const m = $('#billmsg');
    const set1 = (k, v) => { if (v != null && v !== '') set(k, String(v)); };
    set1('bill.consumer_no', b.consumer_no); set1('bill.tariff', b.tariff); set1('bill.phase', b.phase);
    if (b.connected_load_kw) set1('bill.connected_load_kw', (+b.connected_load_kw).toFixed(2));
    set1('bill.section', b.section); set1('bill.months', b.months); set1('bill.units', b.units_total); set1('bill.bill_amount', b.bill_amount);
    if (b.units_by_zone && Object.keys(b.units_by_zone).length) S.bill.units_by_zone = b.units_by_zone;
    ['bill_date', 'fixed_charge', 'file', 'tod'].forEach(k => { if (b[k] != null) S.bill[k] = b[k]; });
    if (b.source === 'pdf' && !S.customer.name && b.name) { S.customer.name = b.name; S.customer.addr1 = S.customer.addr1 || b.address || ''; }
    bindAll(); changed('bill.units');
    const got = ['consumer_no', 'tariff', 'connected_load_kw', 'units_total', 'bill_amount'].filter(k => b[k]).length;
    let h = got ? `<div class="msg">Read ${got} key fields from the ${b.source === 'pdf' ? 'bill PDF' : 'photo'}` +
      (b.units_by_zone && Object.keys(b.units_by_zone).length ? ' · ' + Object.entries(b.units_by_zone).map(([z, u]) => `${z} ${u}`).join(', ') + ' units' : '') + '. Please check them below.</div>' : '';
    if ((b.warnings || []).length) h += `<div class="warnbox">${b.warnings.map(esc).join('<br>')}</div>`;
    m.innerHTML = h;
  }
  async function upload(url, input) {
    const f = input.files[0]; if (!f) return;
    $('#billmsg').innerHTML = '<span class="muted">Reading…</span>';
    const fd = new FormData(); fd.append('file', f);
    const {data} = await api(url, fd, true); input.value = '';
    fillFromBill(data || {});
  }
  async function advise() {
    const el = $('#advice');
    if (!S.bill.tariff || !(S.bill.units || S.bill.bill_amount)) { el.innerHTML = ''; return; }
    const {data: a} = await api('/api/advise', S.bill);
    if (!a || a.error) { el.innerHTML = a && a.error ? `<div class="warnbox">${esc(a.error)}</div>` : ''; return; }
    const r = a.recommend, rh = a.recommend_hybrid;
    let h = `<div class="advice"><div class="row" style="justify-content:space-between;align-items:center"><h3 style="margin:0">Recommendation</h3><span class="badge b-${a.brand}">${a.brand}</span></div>`;
    h += `<div style="font-size:18px;font-weight:800;margin:6px 0">${a.target_kw} kW plant${r ? ` · ${esc(r.name)} · payback ${r.payback_text}` : ''}</div>`;
    h += '<ul class="small" style="margin:4px 0 8px 18px;padding:0">' + a.reasons.map(x => `<li>${esc(x)}</li>`).join('') + '</ul>';
    if (r) h += `<div class="small">On-grid: net ₹${inr(r.net_cost)}, saves ₹${inr(r.saving1)} in year 1. ` + (rh ? `Hybrid (${esc(rh.name)}): net ₹${inr(rh.net_cost)}, payback ${rh.payback_text}, adds backup.` : '') + '</div>';
    h += `<div class="row" style="margin-top:10px">${r ? `<button class="btn g sm" onclick="W.useRec(${r.package_id})">Use ${esc(r.name)}</button>` : ''}
          ${rh ? `<button class="btn o sm" onclick="W.useRec(${r ? r.package_id : 'null'}, ${rh.package_id})">On-grid + hybrid</button>` : ''}
          <button class="btn o sm" onclick="W.quick(${a.target_kw})">Compare all four at ${a.target_kw} kW</button></div></div>`;
    el.innerHTML = h;
  }
  function copy(which) {
    const v = which === 'cno' ? S.bill.consumer_no : S.customer.phone;
    if (!v) { alert(which === 'cno' ? 'Enter or read the consumer number first.' : 'Enter the mobile number in step 1.'); return; }
    navigator.clipboard.writeText(v).then(() => { $('#billmsg').innerHTML = `<div class="msg">Copied ${esc(v)} — paste it on the KSEB page.</div>`; });
  }

  // ---------- options ----------
  const brandNow = () => sum.brand || (S.bill.tariff === 'LT-1' ? 'ADROIT' : S.bill.tariff ? 'ARKVOLT' : 'ADROIT');
  const pkgs = () => CTX.packages.filter(p => p.brand === brandNow());
  const prods = cat => Object.entries(CTX.products).filter(([, p]) => p.category === cat && p.active !== false);
  function fromPackage(p) { return {type: p.type, kw: p.kw, package_id: p.id, module: p.module, qty: p.qty, inverter: p.inverter, battery: p.battery || '', price: p.price}; }
  function quick(kw) {
    if (!kw || kw <= 0) return alert('Enter the plant size in kW.');
    const list = pkgs().filter(p => p.kw === kw);
    const order = (p) => (p.type === 'hybrid' ? 2 : 0) + (p.module.startsWith('AVD') ? 1 : 0);
    S.options = list.length ? list.sort((a, b) => order(a) - order(b)).slice(0, 4).map(fromPackage)
      : [['ongrid', 'STC550'], ['ongrid', 'AVD720'], ['hybrid', 'STC550'], ['hybrid', 'AVD720']]
          .map(([type, module]) => ({type, kw, module, battery: type === 'hybrid' ? 'MIRAKAL' : ''}));
    changed(); go(3);
  }
  function useRec(a, b) {
    S.options = [a, b].filter(x => x != null).map(id => fromPackage(CTX.packages.find(p => p.id === id)));
    changed(); go(3);
  }
  function addOpt() { if (S.options.length >= 4) return alert('Up to 4 options.'); S.options.push({type: 'ongrid', kw: 3, module: 'STC550'}); changed(); renderOpts(); }
  function renderOpts() {
    const box = $('#opts'), L = 'ABCD';
    if (!S.options.length) { box.innerHTML = '<div class="muted">No options yet — use a quick pick above or <b>+ Add option</b>.</div>'; return; }
    box.innerHTML = S.options.map((o, i) => {
      const ps = pkgs().filter(p => p.kw == o.kw && p.type === o.type);
      const mods = prods('module'), invs = prods('inverter').filter(([, p]) => !!p.hybrid === (o.type === 'hybrid')), bats = prods('battery');
      return `<div class="opt" data-i="${i}" draggable="true">
        <div class="hd"><span class="letter">${L[i]}</span><span class="small muted">drag to reorder</span><button class="btn o sm" data-act="del">✕</button></div>
        <div class="seg"><label><input type="radio" name="t${i}" value="ongrid" ${o.type !== 'hybrid' ? 'checked' : ''} data-f="type"><span>On-grid</span></label><label><input type="radio" name="t${i}" value="hybrid" ${o.type === 'hybrid' ? 'checked' : ''} data-f="type"><span>Hybrid</span></label></div>
        <div class="grid g2" style="margin-top:10px">
          <div><label>Plant (kW) — any size</label><input type="number" min="1" step="0.1" data-f="kw" value="${o.kw}" list="kwlist"></div>
          <div><label>Package</label><select data-f="package_id"><option value="">Custom</option>${ps.map(p => `<option value="${p.id}" ${o.package_id == p.id ? 'selected' : ''}>${esc(p.name)} · ₹${inr(p.price)}</option>`).join('')}</select></div>
          <div><label>Panel</label><select data-f="module">${mods.map(([c, p]) => `<option value="${c}" ${o.module === c ? 'selected' : ''}>${esc(p.make.split(' ')[0])} ${p.wp} Wp</option>`).join('')}</select></div>
          <div><label>Quantity</label><input type="number" min="1" data-f="qty" value="${o.qty || ''}" placeholder="auto"></div>
          <div><label>Inverter</label><div class="row" style="gap:6px;flex-wrap:nowrap"><select data-f="inverter"><option value="">Auto</option>${invs.map(([c, p]) => `<option value="${c}" ${o.inverter === c ? 'selected' : ''}>${esc(p.model)} (${p.kw} kW)</option>`).join('')}</select><input type="number" min="1" data-f="inverter_qty" value="${o.inverter_qty || ''}" placeholder="×" title="Number of inverters (blank = auto)" style="width:58px"></div></div>
          <div><label>Battery</label>${o.type === 'hybrid' ? `<div class="row" style="gap:6px;flex-wrap:nowrap"><select data-f="battery">${bats.map(([c, p]) => `<option value="${c}" ${o.battery === c ? 'selected' : ''}>${esc(p.model)}</option>`).join('')}</select><input type="number" min="1" data-f="battery_qty" value="${o.battery_qty || ''}" placeholder="×" title="Number of batteries (blank = one per inverter)" style="width:58px"></div>` : '<div class="muted" style="padding:9px 0">—</div>'}</div>
        </div><div class="st" data-st="${i}"></div></div>`;
    }).join('');
    $$('.opt', box).forEach(card => {
      const i = +card.dataset.i, o = S.options[i];
      $$('[data-f]', card).forEach(el => el.addEventListener('change', () => {
        const f = el.dataset.f; let v = el.value;
        if (f === 'package_id') { if (v) { Object.assign(o, fromPackage(CTX.packages.find(p => p.id == v))); } else { o.package_id = null; } }
        else {
          o[f] = ['kw', 'qty', 'inverter_qty', 'battery_qty'].includes(f) ? (v === '' ? '' : +v) : v;
          if (f === 'type' || f === 'kw') { o.inverter = ''; o.inverter_qty = ''; o.battery_qty = ''; if (f === 'type') o.battery = v === 'hybrid' ? 'MIRAKAL' : ''; }
          if (f === 'kw' || f === 'module') o.qty = '';  // server picks the count (rounded to an even split over the inverters)
          if (f !== 'qty' && f !== 'inverter') { const p = pkgs().find(p => p.kw == o.kw && p.type === o.type && p.module === o.module); if (p) Object.assign(o, fromPackage(p)); else o.package_id = null; }
          if (!o.package_id) delete o.price;
        }
        changed(); renderOpts();
      }));
      $('[data-act=del]', card).onclick = () => { S.options.splice(i, 1); changed(); renderOpts(); };
      card.addEventListener('dragstart', e => e.dataTransfer.setData('i', i));
      card.addEventListener('dragover', e => e.preventDefault());
      card.addEventListener('drop', e => { e.preventDefault(); const from = +e.dataTransfer.getData('i'); const [x] = S.options.splice(from, 1); S.options.splice(i, 0, x); changed(); renderOpts(); });
    });
    renderOptStatus();
  }
  function renderOptStatus() {
    (sum.options || []).forEach((o, i) => {
      const el = $(`[data-st="${i}"]`); if (!el) return;
      const s = o.string, err = s.errors.length, w = s.warnings.length;
      const card = $(`.opt[data-i="${i}"]`);
      if (card) { const q = $('[data-f=qty]', card); if (q && !q.value) q.placeholder = o.qty + ' (auto)';
        const n = $('[data-f=inverter_qty]', card); if (n && !n.value) n.placeholder = '× ' + o.n_inv;
        const nb = $('[data-f=battery_qty]', card); if (nb && !nb.value && o.n_bat) nb.placeholder = '× ' + o.n_bat; }
      el.innerHTML = `<b>${o.qty} modules · DC ${o.kwp.toFixed(2)} kWp · ${o.n_inv > 1 ? o.n_inv + ' × ' : ''}${esc(o.inverter)}${o.n_bat ? ' · ' + o.n_bat + (o.n_bat > 1 ? ' batteries' : ' battery') : ''}</b> · ${err ? `<span class="bad">✗ ${esc(s.errors.join('; '))}</span>` : w ? `<span class="amb">⚠ ${esc(s.warnings.join('; '))}</span>` : '<span class="ok">✓ string OK</span>'}<div class="muted" style="margin-top:3px">${esc(s.text)}</div>`;
    });
  }

  // ---------- prices ----------
  function renderPrices() {
    const t = $('#prices'), os = sum.options || [];
    if (!os.length) { t.innerHTML = '<tr><td class="muted">Add options in step 3 first.</td></tr>'; return; }
    const row = (label, f, cls = '') => `<tr class="${cls}"><td><b>${label}</b></td>${os.map(f).join('')}</tr>`;
    const sp = o => o.split || {};
    t.innerHTML = `<thead><tr><th></th>${os.map(o => `<th class="r">${o.letter} · ${esc(o.label)}</th>`).join('')}</tr></thead>` +
      row('Package / suggested ₹', o => `<td class="r">${o.package_price ? inr(o.package_price) : o.suggested_price ? `${inr(o.suggested_price)}<div class="small muted">custom · ₹${inr(o.rate_per_kw)}/kW</div>` : '<span class="muted">custom — enter price</span>'}</td>`) +
      row('Override / price ₹', (o, i) => `<td class="r"><input type="number" step="500" data-ov="${i}" value="${S.options[i].price_override || (o.package_price ? '' : (S.options[i].price || ''))}" placeholder="${o.package_price ? '' : (o.suggested_price ? inr(o.suggested_price) : 'enter price')}" style="text-align:right;max-width:130px"></td>`) +
      row('EPC value', o => `<td class="r">${inr(sp(o).epc)}</td>`) + row('GST', o => `<td class="r">${inr(sp(o).gst)}</td>`) +
      row('KSEB charges', o => `<td class="r">${inr(sp(o).kseb)}</td>`) + row('Subsidy', o => `<td class="r">${sp(o).cfa ? '(' + inr(sp(o).cfa) + ')' : 'Nil'}</td>`) +
      row('Net cost', o => `<td class="r"><b>${inr(sp(o).net_cost)}</b></td>`) +
      row('Saving, year 1', o => `<td class="r">${inr(o.saving1)}</td>`) +
      row('Payback (live)', o => `<td class="r"><b class="${o.payback && o.payback < 6 ? 'ok' : ''}">${o.payback_text || '—'}</b></td>`);
    $$('[data-ov]', t).forEach(el => el.addEventListener('change', () => {
      const o = S.options[+el.dataset.ov], v = el.value ? +el.value : null;
      if (o.package_id) o.price_override = v; else { o.price = v; o.price_override = null; }
      changed();
    }));
  }

  // ---------- review / generate ----------
  function renderReview() {
    const s = sum, el = $('#review_warn');
    let h = '';
    if ((s.errors || []).length) h += `<div class="err"><b>Must fix before generating:</b>\n• ${s.errors.map(esc).join('\n• ')}</div>`;
    const overridden = (s.options || []).some(o => o.overridden);
    if (overridden && !(S.discount_reason || '').trim()) h += '<div class="err" style="margin-top:8px">Enter the discount reason in step 4 (a price was overridden).</div>';
    const wkey = (s.warnings || []).join('|');
    if (wkey !== seenFor) { seen = false; seenFor = wkey; }
    if ((s.warnings || []).length) h += `<div class="warnbox" style="margin-top:8px"><b>Warnings</b><ul style="margin:4px 0 6px 18px;padding:0">${s.warnings.map(w => `<li>${esc(w)}</li>`).join('')}</ul><label class="inline" style="font-size:14px"><input type="checkbox" id="seen" ${seen ? 'checked' : ''} onchange="W.seen(this.checked)"> <b>I have seen the warnings</b></label></div>`;
    if (s.needs_approval && s.approved) h += '<div class="msg" style="margin-top:8px">✓ Price override approved by a manager.</div>';
    else if (s.needs_approval) h += '<div class="warnbox" style="margin-top:8px">This quote needs <b>manager approval</b> (price override below the allowed margin). Save it; a manager approves it on the proposal page. Changing a price afterwards needs a fresh approval.</div>';
    el.innerHTML = h;
    $('#genbtn').disabled = !!((s.errors || []).length || (overridden && !(S.discount_reason || '').trim()));
  }
  async function save(quiet) {
    const {ok, data} = await api('/api/proposals', {id: pid, inputs: inputs()});
    if (ok && data.id) {
      if (!pid) { pid = data.id; history.replaceState(null, '', `/p/${pid}/edit`); }
      dirty = false; $('#saved').textContent = 'Saved ' + new Date().toLocaleTimeString();
    } else if (!quiet) alert((data && data.error) || 'Could not save');
    return pid;
  }
  async function generate() {
    const m = $('#genmsg'); m.innerHTML = '<span class="muted">Generating PDF… (about 10 seconds)</span>';
    $('#genbtn').disabled = true;
    try {
      if (!pid) await save(true);
      const {ok, data} = await api(`/api/proposals/${pid}/generate`, {inputs: inputs(), warnings_seen: seen});
      if (!ok && data.pending) { m.innerHTML = `<div class="warnbox" style="margin-top:8px;white-space:pre-wrap">${esc(data.error)}<br><button class="btn g sm" style="margin-top:8px" onclick="W.generate()">↻ Check approval &amp; download</button></div>`; return; }
      if (!ok) { m.innerHTML = `<div class="err" style="margin-top:8px">${esc(data.error || 'Failed')}</div>`; return; }
      m.innerHTML = `<div class="msg" style="margin-top:10px"><b class="ok">✓ PDF ready — ${esc(data.ref)}</b> · ${data.pages} pages · ${Math.round(data.size_kb / 1024 * 10) / 10} MB<br>
        <a class="btn g" style="margin-top:8px" href="${data.pdf}?dl=1">⬇ Download PDF</a> <a class="btn o" style="margin-top:8px" href="${data.pdf}" target="_blank">Open in browser</a> <a class="btn o" style="margin-top:8px" href="/p/${pid}">Proposal page</a></div>`;
      dirty = false; $('#saved').textContent = 'Saved';
    } finally { renderReview(); }
  }
  async function preview() {
    const {data} = await api('/api/preview', inputs());
    $('#preview').srcdoc = typeof data === 'string' ? data : '<p>Preview not available</p>';
  }
  window.addEventListener('beforeunload', e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });

  window.W = {go, save, generate, preview, copy, quick, useRec, addOpt, seen: v => { seen = v; },
    photo: el => upload('/api/bill/photo', el), pdf: el => upload('/api/bill/pdf', el)};
  document.body.insertAdjacentHTML('beforeend', '<datalist id="kwlist">' + [1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 25, 30, 50].map(k => `<option value="${k}">`).join('') + '</datalist>');
  bindAll(); billMode(); go(1); compute(); advise();
  $('#a_cno').textContent = S.bill.consumer_no || '—'; $('#a_ph').textContent = S.customer.phone || '—';
})();
