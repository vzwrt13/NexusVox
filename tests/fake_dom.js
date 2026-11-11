// A stand-in DOM for the dashboard tests under Node (tests/test_recordings_js.py): enough of Element for the code
// under test. innerHTML is kept as a string (no parsing); elements made with createElement form a real tree that
// querySelector walks. Selectors: one compound (`tag.a.b`, `[attr="v"]`, `#id`), optionally after `:scope > `.
const byId = new Map();
let activeElement = null;
class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase(); this.children = []; this.parent = null; this.attrs = {};
    this.dataset = {}; this.listeners = {}; this.hidden = false; this._text = ""; this._html = "";
    const props = {};
    this.style = { props, display: "", setProperty: (k, v) => { props[k] = String(v); }, removeProperty: (k) => { delete props[k]; },
      getPropertyValue: (k) => props[k] || "" };
    this.value = ""; this.checked = false; this.disabled = false; this.width = 0;
    this.open = false; this.files = [];
    const cls = new Set();
    this.classList = { add: (...c) => c.forEach((x) => cls.add(x)), remove: (...c) => c.forEach((x) => cls.delete(x)),
      toggle: (c, on) => { const want = on === undefined ? !cls.has(c) : on; want ? cls.add(c) : cls.delete(c); return want; },
      contains: (c) => cls.has(c), _set: cls };
  }
  get className() { return [...this.classList._set].join(" "); }
  set className(v) { this.classList._set.clear(); String(v).split(/\s+/).filter(Boolean).forEach((c) => this.classList.add(c)); }
  get id() { return this.attrs.id || ""; }
  set id(v) { this.attrs.id = v; byId.set(v, this); }
  setAttribute(k, v) { if (k === "id") this.id = v; else this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  append(...nodes) { for (const n of nodes) { if (typeof n === "string") { this._text += n; continue; }
    if (n.parent) n.remove(); n.parent = this; this.children.push(n); } }
  appendChild(n) { this.append(n); return n; }
  insertBefore(n, ref) { if (n.parent) n.remove(); n.parent = this;
    const at = ref ? this.children.indexOf(ref) : -1; at < 0 ? this.children.push(n) : this.children.splice(at, 0, n); return n; }
  replaceChildren(...nodes) { [...this.children].forEach((c) => c.remove()); this._text = ""; this._html = ""; this.append(...nodes); }
  remove() { if (this.parent) { this.parent.children = this.parent.children.filter((c) => c !== this); this.parent = null; } }
  contains(n) { for (let x = n; x; x = x.parent) if (x === this) return true; return false; }
  get isConnected() { let x = this; while (x.parent) x = x.parent; return x === document.body; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this.replaceChildren(); this._text = String(v); }
  get innerHTML() { return this._html; }
  set innerHTML(v) { this.replaceChildren(); this._html = String(v); }
  insertAdjacentHTML(_, html) { this._html += html; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  dispatch(type, extra = {}) { const ev = { type, target: this, preventDefault() {}, stopPropagation() { this.stopped = true; }, ...extra };
    for (let x = this; x && !ev.stopped; x = x.parent) (x.listeners[type] || []).forEach((fn) => fn(ev)); return ev; }
  focus() { activeElement = this; }
  scrollIntoView() {}
  get offsetWidth() { return 0; }
  getBoundingClientRect() { return { width: this.width }; }
  closest(sel) { for (let x = this; x; x = x.parent) if (matches(x, sel)) return x; return null; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  click() { this.dispatch("click"); }
  all() { return this.children.flatMap((c) => [c, ...c.all()]); }
  querySelectorAll(sel) { const direct = sel.startsWith(":scope > "); const s = direct ? sel.slice(9) : sel;
    const parts = s.trim().split(/\s+/);  // `ancestor descendant`: the last compound inside one matching the first
    if (parts.length === 2) return this.all().filter((e) => matches(e, parts[1]) && ancestor(e, parts[0]));
    return (direct ? this.children : this.all()).filter((e) => matches(e, s)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
function ancestor(el, sel) { for (let x = el.parent; x; x = x.parent) if (matches(x, sel)) return true; return false; }
function matches(el, sel) {
  const m = /^([a-z]*)((?:[.#][\w-]+)*)((?:\[[\w-]+(?:="[^"]*")?\])*)$/i.exec(sel.trim());
  if (!m) throw new Error("selector not supported: " + sel);
  if (m[1] && el.tagName !== m[1].toUpperCase()) return false;
  for (const part of m[2].match(/[.#][\w-]+/g) || []) {
    if (part[0] === "." && !el.classList.contains(part.slice(1))) return false;
    if (part[0] === "#" && el.id !== part.slice(1)) return false;
  }
  for (const [, k, v] of m[3].matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)) {
    const have = k.startsWith("data-") ? el.dataset[k.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase())] : el.getAttribute(k);
    if (v === undefined ? have === undefined || have === null : String(have) !== v) return false;
  }
  return true;
}
const docListeners = {};
const document = {
  body: new El("body"),
  head: new El("head"),
  get activeElement() { return activeElement; },
  createElement: (tag) => new El(tag),
  getElementById: (id) => byId.get(id) || null,
  querySelectorAll: (sel) => document.body.querySelectorAll(sel),
  querySelector: (sel) => document.body.querySelector(sel),
  addEventListener: (type, fn) => { (docListeners[type] ||= []).push(fn); },
  hidden: false, visibilityState: "visible",
};
// The elements index.html gives the scripts under test, all attached to the page.
function page(ids) {
  for (const [id, tag] of ids) { const el = new El(tag || "div"); el.id = id; document.body.append(el); }
}
const escapeHtml = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const fetched = [];
// Answers by URL: a test sets `replies.set(url, body)` (a status other than 200 with `{status, body}`). The model
// check answers at once (the upload waits for it); every other request stays in flight.
const replies = new Map([["/api/models/current", { diarization: true }]]);
const fetch = (url, opts) => {
  fetched.push([url, (opts && opts.method) || "GET"]);
  if (!replies.has(url)) return new Promise(() => {});
  const reply = replies.get(url);
  const status = reply && reply.status ? reply.status : 200;
  const body = reply && reply.status ? reply.body : reply;
  return Promise.resolve({ ok: status < 400, status, json: async () => body });
};
const history = { replaceState() {} };
const out = (value) => console.log(JSON.stringify(value));
