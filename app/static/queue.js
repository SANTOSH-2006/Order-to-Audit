// Offline outbox: orders are written to localStorage first, then sent. Retried on reconnect.
// Each order carries a client_uuid, so the server ignores duplicates if a retry races a slow success.
const QK = 'orderQueue';
const readQ = () => { try { return JSON.parse(localStorage.getItem(QK) || '[]'); } catch (e) { return []; } };
const writeQ = q => localStorage.setItem(QK, JSON.stringify(q));
function uuid() { return (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2)); }
async function flushQueue(quiet) {
  let q = readQ(), sent = [];
  for (const o of q) {
    try {
      const r = await fetch('/api/orders', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(o)});
      if (r.ok) { sent.push((await r.json()).order_no); q = q.filter(x => x.client_uuid !== o.client_uuid); }
      else if (r.status === 422) { q = q.filter(x => x.client_uuid !== o.client_uuid); alert('Order rejected: ' + (await r.json()).error); }
      else break;                    // 401 / 5xx: keep and retry later
    } catch (e) { break; }           // offline
  }
  writeQ(q);
  const el = document.getElementById('queue') || document.getElementById('status');
  if (el) el.textContent = q.length ? q.length + ' order(s) waiting to send (offline)' : (sent.length && !quiet ? 'Sent: ' + sent.join(', ') : '');
  return {sent, left: q.length};
}
window.addEventListener('online', () => flushQueue(false));
