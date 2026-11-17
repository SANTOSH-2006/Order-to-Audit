let cat = null;
const $ = id => document.getElementById(id);
async function load() {
  try { cat = await (await fetch('/api/catalog')).json(); localStorage.setItem('catalog', JSON.stringify(cat)); }
  catch (e) { cat = JSON.parse(localStorage.getItem('catalog') || 'null'); $('status').textContent = 'Offline: using saved price list'; }
  if (!cat) { $('status').textContent = 'Open this page once with network to load prices.'; return; }
  $('cust').innerHTML = cat.customers.map(c => `<option value="${c.id}">${c.name} (${c.type})</option>`).join('');
  $('lines').innerHTML = cat.items.map(i => `<div class="prow" data-id="${i.id}"><div class="n"><b>${i.name}</b><br><small class="muted price"></small></div>
    <button type="button" data-d="-1">&minus;</button><input type="number" min="0" value="0" inputmode="numeric">
    <button type="button" data-d="1">+</button>${i.carton ? `<select><option value="packet">packets</option><option value="carton">cartons (${i.carton})</option></select>` : ''}</div>`).join('');
  document.querySelectorAll('.prow button').forEach(b => b.onclick = () => { const inp = b.parentNode.querySelector('input'); inp.value = Math.max(0, +inp.value + +b.dataset.d); recalc(); });
  document.querySelectorAll('.prow input,.prow select,#cust').forEach(e => e.oninput = recalc);
  recalc();
}
const price = i => { const k = $('cust').value + ':' + i.id; return cat.prices[k] ?? i.price; };
function recalc() {
  let t = 0;
  document.querySelectorAll('.prow').forEach(r => {
    const i = cat.items.find(x => x.id == r.dataset.id), sel = r.querySelector('select');
    const mult = sel && sel.value === 'carton' ? i.carton : 1;
    r.querySelector('.price').textContent = 'Rs ' + (price(i) / 100).toFixed(2) + ' per packet';
    t += (+r.querySelector('input').value) * mult * price(i);
  });
  $('total').textContent = (t / 100).toFixed(2);
}
$('submit').onclick = async () => {
  const lines = [...document.querySelectorAll('.prow')].map(r => ({item_id: r.dataset.id, qty: r.querySelector('input').value, unit: (r.querySelector('select') || {value: 'packet'}).value})).filter(l => +l.qty > 0);
  if (!lines.length) return alert('Add at least one product');
  const q = readQ(); q.push({client_uuid: uuid(), customer_id: $('cust').value, lines}); writeQ(q);
  const res = await flushQueue(false);
  if (res.left) alert('No network - order saved on this phone and will be sent automatically.'); else location.href = '/m';
};
load();
