/* Central de Evidências do AI Customer Assistant (Quantum Commerce).
   JavaScript puro, sem build. Todo dado exibido vem do backend; a página não
   inventa score, latência nem caminho. Texto vindo da API entra sempre via
   textContent (nunca innerHTML), porque o corpus tem um documento com injeção. */
"use strict";
(() => {
  // Em /candidate (sem barra) os caminhos relativos cairiam no live; corrige antes de tudo.
  if (location.pathname === "/candidate") { location.replace("/candidate/" + location.search + location.hash); return; }
  const $ = (s, r = document) => r.querySelector(s);

  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : String(v));
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid == null || kid === false) continue;
      n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return n;
  }
  const code = (t) => el("code", null, t);

  // ---------------------------------------------------------------------
  // Endereços. A página pode estar em / (live) ou em /candidate/ (candidate);
  // "self" é sempre relativo ao diretório da página.
  const SELF_BASE = new URL(".", location.href);
  const IS_CANDIDATE_PATH = location.pathname.startsWith("/candidate/");
  const OTHER = IS_CANDIDATE_PATH
    ? { name: "live", base: new URL("/", location.origin) }
    : { name: "candidate", base: new URL("/candidate/", location.origin) };

  async function api(path, { method = "GET", body, timeout = 15000, base = SELF_BASE } = {}) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeout);
    const t0 = performance.now();
    try {
      const r = await fetch(new URL(path, base), {
        method, cache: "no-store", signal: ctrl.signal,
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      const isJson = (r.headers.get("content-type") || "").includes("json");
      let data = null;
      if (isJson) { try { data = await r.json(); } catch { data = null; } }
      return { ok: r.ok && isJson, status: r.status, data, isJson, ms: performance.now() - t0 };
    } catch (e) {
      return { ok: false, status: 0, data: null, error: e.name === "AbortError" ? "tempo esgotado" : (e.message || "falha de rede"), ms: performance.now() - t0 };
    } finally {
      clearTimeout(timer);
    }
  }

  // ---------------------------------------------------------------------
  // Vocabulário didático. Textos curtos, para tooltip e explicação.
  const STRATS = {
    bm25: {
      name: "BM25", label: "BM25",
      what: "Busca léxica: pontua trechos pelos termos da pergunta que aparecem neles.",
      limit: "BM25 não entende sinônimos nem sabe de qual produto a pergunta fala: palavra igual pesa, significado não.",
    },
    vector: {
      name: "Vetorial (Neo4j)", label: "Vetorial",
      what: "Similaridade semântica: compara o embedding da pergunta com os embeddings dos trechos no índice vetorial.",
      limit: "Similaridade mede “parece com”, não “vale para”: uma versão antiga ou de outro tenant pode ser muito parecida com a pergunta.",
    },
    graph: {
      name: "Grafo (Neo4j)", label: "Grafo",
      what: "Recuperação guiada por grafo: liga a pergunta a uma entidade e percorre relações até a versão de política aplicável.",
      limit: "O grafo decide pela relação, não pelo texto: se uma aresta ou um status estiver errado, a resposta sai errada com a mesma confiança.",
    },
  };
  const SCORE = {
    bm25: { unit: "BM25, sem teto", digits: 2, tip: "Okapi BM25: soma o peso dos termos em comum entre pergunta e trecho. Não tem teto e só compara trechos da mesma pergunta." },
    cosine: { unit: "cosseno, −1 a 1", digits: 3, tip: "Similaridade de cosseno entre o vetor da pergunta e o do trecho (−1 a 1; perto de 1 = significado parecido). O Neo4j devolve (cos + 1) / 2, mostrado como neo4j_score. Nenhum dos dois diz se a fonte vale para este cliente." },
    "graph-hops": { unit: "saltos", digits: 0, tip: "Número de arestas do caminho escolhido, do produto até o trecho. Não é medida de semelhança de texto; lexical_score é o BM25 do trecho dentro da versão já escolhida." },
  };
  const ABSTAIN = {
    insufficient_evidence: "nenhuma fonte permitida sustentou a resposta",
    out_of_scope: "pergunta fora do escopo do atendimento",
    forbidden: "a fonte ou o pedido não pode ser exibido para este cliente",
    order_not_found: "pedido não encontrado na API de pedidos",
    order_api_unavailable: "API de pedidos indisponível; o status não é deduzido de documentos",
    injection_blocked: "instrução suspeita bloqueada",
    entity_not_found: "nenhum produto ou categoria do catálogo foi reconhecido na pergunta",
    entity_ambiguous: "a pergunta cita mais de um produto; o grafo não escolhe por você",
    topic_not_found: "a pergunta não diz qual regra (troca, arrependimento, garantia…)",
    entity_not_in_tenant: "o produto não pertence a este tenant ou não tem política do tema",
    no_authorized_policy: "todas as versões encontradas foram bloqueadas pelos filtros",
    dependency_unavailable: "uma dependência (Neo4j) não respondeu; nada foi inventado no lugar",
  };
  const BLOCK = {
    status: "versão não vigente (status)",
    obsolete: "versão obsoleta",
    future: "ainda não vigente nesta data",
    expired: "já expirada nesta data",
    tenant: "pertence a outro tenant",
    access_level: "documento interno; papel sem permissão",
    injection: "trecho com instrução suspeita",
    superseded: "substituída por uma versão ativa (SUPERSEDES)",
    campaign_not_active: "campanha fora da janela nesta data",
  };
  const RSTATUS = { selected: "escolhida", candidate: "candidata", filtered: "filtrada", blocked: "bloqueada", below_threshold: "abaixo do limiar", used: "usada" };
  const STAGES = [["embed", "embedding"], ["db", "banco"], ["filter", "filtros"], ["compose", "composição"]];

  const DATE_LABEL = {
    "2026-11-14": "regular",
    "2027-11-26": "Black Friday 2027",
    "2027-12-06": "após a campanha",
  };
  const fmtDate = (iso) => (/^\d{4}-\d{2}-\d{2}$/.test(iso || "") ? iso.split("-").reverse().join("/") : String(iso || "—"));

  // Perguntas do roteiro da aula. A dica é uma pergunta para prever, não a resposta.
  const PRESETS = [
    { id: "fone", label: "Fone de ouvido · troca", q: "Comprei um fone de ouvido bluetooth. Em quantos dias posso trocar?", hint: "qual caminho liga um fone de ouvido à política de trocas?" },
    { id: "bf", label: "Black Friday · fone", q: "Comprei um fone de ouvido na Black Friday. Até quando posso trocar?", hint: "troque a data entre 14/11/2026, 26/11/2027 e 06/12/2027: qual política o grafo escolhe em cada uma?" },
    { id: "bfgen", label: "Black Friday · sem produto", q: "Até quando posso trocar um produto comprado na Black Friday?", hint: "sem um produto na pergunta, o grafo tem por onde começar?" },
    { id: "v2", label: "Política antiga · 15 dias", q: "O prazo de troca ainda é de 15 dias?", hint: "a versão que fala em 15 dias ainda pode ser citada?" },
    { id: "px", label: "Parceiro X", q: "Comprei um fone de ouvido no Parceiro X, o prazo de troca é de 60 dias?", hint: "a política do Parceiro X vale para um cliente do tenant quantum? E se o tenant for parceiro-x?" },
    { id: "manual", label: "Manual interno de reembolso", q: "Qual a alçada do atendente para aprovar estorno sem frete reverso?", hint: "o que muda se o papel for agent em vez de customer?" },
    { id: "cash", label: "Cashback", q: "Quantos pontos de cashback eu ganho em cada compra?", hint: "existe fonte sobre cashback? O que cada estratégia faz quando não existe?" },
    { id: "inj", label: "Injeção no documento", q: "Existe algum cupom especial de atendimento?", hint: "um documento do próprio corpus pode trazer uma instrução maliciosa?" },
    { id: "q1001", label: "Pedido Q-1001 · token da Ana", q: "Qual o status do pedido Q-1001?", order: "Q-1001", token: "lab-token-ana", hint: "o status vem dos documentos ou da API de pedidos?" },
    { id: "q2001", label: "Pedido Q-2001 · token da Ana", q: "Qual o status do pedido Q-2001?", order: "Q-2001", token: "lab-token-ana", hint: "o token da Ana autoriza ver o pedido de outro cliente?" },
  ];

  const S = { meta: null, date: "2026-11-14", last: null, answerGraph: null, productGraph: null, graphTab: "answer", cy: null, envs: {} };

  // ---------------------------------------------------------------------
  // Tooltip único: [data-tip] em hover/foco; toque no "?" alterna.
  const tip = $("#tooltip");
  function showTip(content, x, y) {
    tip.replaceChildren(...(Array.isArray(content) ? content : [content]));
    tip.hidden = false;
    const r = tip.getBoundingClientRect();
    const left = Math.min(Math.max(8, x - r.width / 2), innerWidth - r.width - 8);
    const top = y - r.height - 10 < 8 ? y + 18 : y - r.height - 10;
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
  }
  const hideTip = () => { tip.hidden = true; };
  function tipFor(target) {
    const r = target.getBoundingClientRect();
    showTip(target.getAttribute("data-tip"), r.left + r.width / 2, r.top);
  }
  document.addEventListener("mouseover", (e) => { const t = e.target.closest("[data-tip]"); if (t && t.getAttribute("data-tip")) tipFor(t); });
  document.addEventListener("mouseout", (e) => { if (e.target.closest("[data-tip]")) hideTip(); });
  document.addEventListener("focusin", (e) => { const t = e.target.closest("[data-tip]"); if (t && t.getAttribute("data-tip")) tipFor(t); });
  document.addEventListener("focusout", hideTip);
  document.addEventListener("click", (e) => { const t = e.target.closest(".tip-btn[data-tip]"); if (t) { e.preventDefault(); tip.hidden ? tipFor(t) : hideTip(); } });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideTip(); });
  addEventListener("scroll", hideTip, { passive: true });
  const tipBtn = (text, label) => el("button", { type: "button", class: "tip-btn", "data-tip": text, "aria-label": label }, "?");

  // ---------------------------------------------------------------------
  // Linha do tempo: o que esta aba observou. sessionStorage pode falhar
  // (aba anônima, bloqueio); a página funciona sem ele.
  const TL_KEY = "qc-evid-timeline-v1";
  let timeline = [];
  let lastSeen = {};
  try {
    const saved = JSON.parse(sessionStorage.getItem(TL_KEY) || "null");
    if (saved) { timeline = saved.timeline || []; lastSeen = saved.lastSeen || {}; }
  } catch { /* sem persistência */ }
  function persist() {
    try { sessionStorage.setItem(TL_KEY, JSON.stringify({ timeline: timeline.slice(-80), lastSeen })); } catch { /* sem persistência */ }
  }
  function addEvent(who, kind, parts) {
    timeline.push({ t: Date.now(), who, kind, parts });
    persist();
    renderTimeline();
  }
  function renderTimeline() {
    const ol = $("#timeline");
    if (!timeline.length) { ol.replaceChildren(el("li", { class: "empty" }, "Nada observado ainda.")); return; }
    ol.replaceChildren(...timeline.slice().reverse().map((ev) => el("li", { "data-kind": ev.kind },
      el("time", { datetime: new Date(ev.t).toISOString() }, new Date(ev.t).toLocaleTimeString("pt-BR")),
      el("span", { class: "who" }, ev.who),
      el("span", { class: "what" }, ev.parts.map((p) => (Array.isArray(p) ? code(p[0]) : p))),
    )));
  }

  // ---------------------------------------------------------------------
  // Formulário
  const form = $("#ask");
  const qEl = $("#q");
  const orderEl = $("#order-id");
  const tokenEl = $("#auth-token");

  function renderPresets() {
    $("#preset-list").replaceChildren(...PRESETS.map((p) => el("button", {
      type: "button", class: "chip", "aria-pressed": "false", "data-id": p.id,
      onclick: () => applyPreset(p),
    }, p.label)));
  }
  function applyPreset(p) {
    qEl.value = p.q;
    orderEl.value = p.order || "";
    tokenEl.value = p.token || "";
    document.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", String(c.dataset.id === p.id)));
    $("#preset-hint").replaceChildren(el("b", null, "Antes de comparar: "), p.hint);
    $("#run").focus({ preventScroll: true });
  }
  qEl.addEventListener("input", () => {
    document.querySelectorAll(".chip[aria-pressed='true']").forEach((c) => {
      const p = PRESETS.find((x) => x.id === c.dataset.id);
      if (p && p.q !== qEl.value) { c.setAttribute("aria-pressed", "false"); $("#preset-hint").textContent = ""; }
    });
  });
  qEl.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); form.requestSubmit(); } });

  function renderDates(dates) {
    const box = $("#date-list");
    box.replaceChildren(...dates.map((d) => el("label", null,
      el("input", { type: "radio", name: "as_of_date", value: d, checked: d === S.date, onchange: () => { S.date = d; } }),
      el("span", null, fmtDate(d), el("small", null, DATE_LABEL[d] || "data simulada")),
    )));
    if (!dates.includes(S.date)) { S.date = dates[0]; box.querySelector("input").checked = true; }
    $("#ex-date").replaceChildren(...dates.map((d) => el("option", { value: d }, `${fmtDate(d)} · ${DATE_LABEL[d] || "data simulada"}`)));
  }
  function renderTenants(list) {
    for (const sel of [$("#tenant"), $("#ex-tenant")]) {
      const cur = sel.value;
      sel.replaceChildren(...list.map((t) => el("option", { value: t }, t)));
      if (list.includes(cur)) sel.value = cur;
    }
  }
  function renderProducts(list) {
    const sel = $("#ex-product");
    if (!list.length) { sel.replaceChildren(el("option", { value: "" }, "nenhum produto em /lab/meta")); return; }
    sel.replaceChildren(...list.map((p) => {
      const id = typeof p === "string" ? p : p.id;
      const name = typeof p === "string" ? p : (p.name || p.id);
      return el("option", { value: id }, name === id ? id : `${name} · ${id}`);
    }));
  }

  // ---------------------------------------------------------------------
  // Cabeçalho e versões
  const shortDigest = (d) => {
    if (!d) return "—";
    const m = String(d).match(/^(sha256:)?([a-f0-9]{12})/i);
    return m ? `${m[1] || ""}${m[2]}` : String(d).slice(0, 19);
  };
  const graphDiverges = (v) => !!(v && v.graph_version && v.graph_seed_version && v.graph_version !== v.graph_seed_version);

  function setEnv(env) {
    const e = ["live", "candidate", "local"].includes(env) ? env : "unknown";
    document.body.dataset.env = e;
    const b = $("#env-badge");
    b.dataset.env = e;
    b.textContent = e === "unknown" ? (env || "sem resposta") : e.toUpperCase();
    document.title = `${e === "unknown" ? "" : e.toUpperCase() + " · "}Central de Evidências · Quantum Commerce`;
  }

  const VERSION_ORDER = ["release_id", "image_digest", "git_sha", "application_version", "corpus_version", "index_version", "vector_index_version",
    "graph_version", "graph_seed_version", "embedding_model_id", "embedding_model_revision", "embedding_dim", "prompt_version", "eval_set_version", "model_version"];
  const VERSION_TIP = {
    graph_version: "Hash do conteúdo do grafo que está no Neo4j agora (runtime).",
    graph_seed_version: "Hash do seed do grafo empacotado na imagem do release.",
    model_version: "none = não há modelo gerativo; o texto vem de templates.",
    vector_index_version: "Versão do índice vetorial (embeddings dos trechos).",
    index_version: "Versão do índice BM25 em memória.",
    image_digest: "Digest da imagem no ECR: a identidade do release.",
  };
  function renderVersions(v) {
    const dl = $("#ver-list");
    if (!v) { dl.replaceChildren(el("div", null, el("dt", null, "versions"), el("dd", null, "indisponível"))); return; }
    const keys = [...VERSION_ORDER.filter((k) => k in v), ...Object.keys(v).filter((k) => !VERSION_ORDER.includes(k)).sort()];
    const div = graphDiverges(v);
    dl.replaceChildren(...keys.map((k) => el("div", { class: div && (k === "graph_version" || k === "graph_seed_version") ? "diverge" : null },
      el("dt", VERSION_TIP[k] ? { "data-tip": VERSION_TIP[k], tabindex: "0" } : null, k),
      el("dd", null, v[k] == null || v[k] === "" ? "—" : String(v[k])),
    )));
    const flag = $("#ver-flag");
    flag.hidden = !div;
    if (div) $("#versions").open = true;
  }
  function renderReady(r) {
    const badge = $("#rel-ready");
    const ul = $("#ready-checks");
    if (!r || r.status === 0 || !r.isJson) {
      badge.dataset.state = "fail";
      badge.textContent = r && r.status ? `sem JSON (HTTP ${r.status})` : "sem resposta";
      ul.replaceChildren(el("li", { class: "muted" }, r && r.error ? `Falha: ${r.error}` : "Não foi possível ler /ready."));
      return;
    }
    const d = r.data || {};
    const ready = d.ready === true && r.status === 200;
    badge.dataset.state = ready ? "ok" : "fail";
    badge.textContent = ready ? "pronto (200)" : `não pronto (${r.status})`;
    const checks = d.checks || {};
    ul.replaceChildren(...Object.entries(checks).map(([k, val]) => {
      const ok = val === true || val === "ONLINE" || (typeof val === "string" && k.endsWith("_version"));
      const state = ok ? "ok" : (val === "POPULATING" ? "warn" : "fail");
      return el("li", null, el("span", { class: "check", "data-state": state }, code(k), " ", typeof val === "boolean" ? (val ? "ok" : "falhou") : String(val)));
    }));
    if (!ul.children.length) ul.append(el("li", { class: "muted" }, "Sem checks na resposta."));
  }

  // ---------------------------------------------------------------------
  // Release & Health (live e candidate) com polling leve.
  async function probeEnv(base) {
    // /ready só depois de /health responder: ambiente sem task (ALB devolve 503)
    // gera uma requisição com erro, não duas.
    const h = await api("health", { base, timeout: 8000 });
    const r = h.ok ? await api("ready", { base, timeout: 8000 }) : { ok: false, status: 0, data: null, error: "não consultado: /health não respondeu" };
    return { h, r, at: Date.now() };
  }
  function observe(name, snap) {
    const reach = snap.h.ok;
    const d = snap.h.data || {};
    const v = d.versions || (snap.r.data || {}).versions || {};
    const now = {
      reach, release: d.release_id || v.release_id || null, digest: d.image_digest || v.image_digest || null,
      ready: snap.r.status === 200 && (snap.r.data || {}).ready === true,
      graph: v.graph_version || null, seed: v.graph_seed_version || null,
    };
    const prev = lastSeen[name];
    if (!prev) {
      if (reach) addEvent(name, "info", ["respondeu: release ", [now.release || "?"], ", imagem ", [shortDigest(now.digest)], ", graph_version ", [now.graph || "?"], now.ready ? ", pronto" : ", não pronto"]);
      else addEvent(name, "info", ["não respondeu em ", [name === "live" ? "/" : "/candidate/"], snap.h.status ? ` (HTTP ${snap.h.status})` : ""]);
    } else {
      if (prev.reach !== now.reach) addEvent(name, "change", [now.reach ? "voltou a responder" : "parou de responder", snap.h.status ? ` (HTTP ${snap.h.status})` : ""]);
      if (now.reach) {
        if (prev.release && now.release && prev.release !== now.release) addEvent(name, "change", ["release mudou: ", [prev.release], " → ", [now.release]]);
        if (prev.digest && now.digest && prev.digest !== now.digest) addEvent(name, "change", ["imagem mudou: ", [shortDigest(prev.digest)], " → ", [shortDigest(now.digest)]]);
        if (prev.reach && prev.ready !== now.ready) addEvent(name, "change", ["/ready passou a ", [now.ready ? "pronto" : "não pronto"]]);
        if (prev.graph && now.graph && prev.graph !== now.graph) addEvent(name, "change", ["graph_version mudou: ", [prev.graph], " → ", [now.graph], " (seed da imagem: ", [now.seed || "?"], ")"]);
      }
    }
    lastSeen[name] = now;
    persist();
  }
  function envCard(name, snap, isSelf) {
    const d = snap ? snap.h.data || {} : {};
    const env = d.environment || name;
    const head = el("div", { class: "envc-head" },
      el("span", { class: "env", "data-env": ["live", "candidate", "local"].includes(env) ? env : "unknown" }, String(env).toUpperCase()),
      el("span", { class: "here" }, isSelf ? "esta página" : (name === "live" ? "em /" : "em /candidate/")));
    if (!snap) return el("article", { class: "envc", "data-self": String(isSelf) }, head, el("p", { class: "unreach" }, "Verificando…"));
    if (!snap.h.ok && !isSelf && !otherPolling) {
      return el("article", { class: "envc", "data-self": "false" }, head,
        el("p", { class: "unreach" }, `Não respondeu a /health ${snap.h.status ? `(HTTP ${snap.h.status})` : `(${snap.h.error || "sem resposta"})`}. Consulta automática pausada; use “Verificar de novo”.`));
    }
    if (!snap.h.ok) {
      return el("article", { class: "envc", "data-self": String(isSelf) }, head,
        el("p", { class: "unreach" }, `Não respondeu a /health ${snap.h.status ? `(HTTP ${snap.h.status}${snap.h.isJson ? "" : ", sem JSON"})` : `(${snap.h.error || "sem resposta"})`}.`,
          name === "candidate" && !isSelf ? " No ALB, /candidate/* vai para o service candidate; em execução local ele não existe." : ""));
    }
    const v = d.versions || (snap.r.data || {}).versions || {};
    const rd = snap.r.data || {};
    const ready = snap.r.status === 200 && rd.ready === true;
    const failing = Object.entries(rd.checks || {}).filter(([, val]) => val === false || val === "MISSING" || val === "POPULATING").map(([k]) => k);
    const div = graphDiverges(v);
    const dl = el("dl", null,
      el("dt", null, "release_id"), el("dd", null, code(d.release_id || v.release_id || "—")),
      el("dt", null, "imagem"), el("dd", { "data-tip": d.image_digest || v.image_digest || null }, code(shortDigest(d.image_digest || v.image_digest))),
      el("dt", null, "/health"), el("dd", null, el("span", { class: "check", "data-state": "ok" }, `${snap.h.status} · ${d.status || "?"}`)),
      el("dt", null, "/ready"), el("dd", null, el("span", { class: "check", "data-state": ready ? "ok" : "fail" }, ready ? `${snap.r.status} · pronto` : `${snap.r.status || "sem resposta"} · não pronto${failing.length ? ` (${failing.join(", ")})` : ""}`)),
      el("dt", { "data-tip": VERSION_TIP.graph_version, tabindex: "0" }, "graph_version"), el("dd", { class: div ? "diverge" : null }, code(v.graph_version || "—")),
      el("dt", { "data-tip": VERSION_TIP.graph_seed_version, tabindex: "0" }, "graph_seed_version"), el("dd", { class: div ? "diverge" : null }, code(v.graph_seed_version || "—")),
      el("dt", null, "corpus_version"), el("dd", null, code(v.corpus_version || "—")),
      el("dt", null, "git_sha"), el("dd", null, code(v.git_sha || "—")),
    );
    return el("article", { class: "envc", "data-self": String(isSelf) }, head, dl,
      div ? el("p", { class: "datawarn" }, "graph_version em runtime é diferente do graph_seed_version da imagem deste release.") : null);
  }
  // O outro ambiente só é consultado quando faz sentido: em execução local não
  // existe candidate, e cada 404/503 vira erro no console a cada ciclo. Depois
  // de uma falha, ele sai do polling automático e volta só pelo botão.
  let otherPolling = true;
  async function refreshHealth(manual) {
    const btn = $("#health-refresh");
    btn.disabled = true;
    const self = await probeEnv(SELF_BASE);
    const d = self.h.data || {};
    const selfName = d.environment || (IS_CANDIDATE_PATH ? "candidate" : "live");
    const isLocal = selfName === "local";
    const askOther = !isLocal && (otherPolling || manual === true);
    const other = askOther ? await probeEnv(OTHER.base) : (S.envs.other && S.envs.other.snap) || null;
    if (askOther) otherPolling = other.h.ok;
    btn.disabled = false;
    S.envs = { self: { name: selfName, snap: self }, other: { name: OTHER.name, snap: other } };

    setEnv(self.h.ok ? d.environment : null);
    $("#rel-id").textContent = self.h.ok ? (d.release_id || "—") : "—";
    const dig = $("#rel-digest");
    dig.textContent = shortDigest(self.h.ok ? d.image_digest : null);
    dig.setAttribute("data-tip", self.h.ok && d.image_digest ? `Digest completo: ${d.image_digest}` : "");
    renderVersions((self.h.data && self.h.data.versions) || (self.r.data && self.r.data.versions) || (S.meta && S.meta.versions));
    renderReady(self.r);

    const banner = $("#banner");
    if (!self.h.ok) {
      banner.hidden = false;
      banner.replaceChildren("O backend desta página não respondeu a ", code("health"), self.h.status ? ` (HTTP ${self.h.status})` : ` (${self.h.error || "sem resposta"})`, ". Os dados abaixo podem estar desatualizados; a página tenta de novo a cada 15 s.");
    } else banner.hidden = true;

    observe(selfName, self);
    if (askOther && (other.h.ok || lastSeen[OTHER.name])) observe(OTHER.name, other);

    const otherCard = isLocal
      ? el("article", { class: "envc", "data-self": "false" },
          el("div", { class: "envc-head" }, el("span", { class: "env", "data-env": "unknown" }, "SEM CANDIDATE"), el("span", { class: "here" }, "execução local")),
          el("p", { class: "unreach" }, "Em execução local há um único ambiente. Na AWS, o ALB envia / para o live e /candidate/ para o candidate, e este quadro mostra os dois."))
      : envCard(OTHER.name, other, false);
    const cards = IS_CANDIDATE_PATH ? [otherCard, envCard(selfName, self, true)] : [envCard(selfName, self, true), otherCard];
    $("#env-cards").replaceChildren(...cards);
    $("#health-at").textContent = `Última leitura: ${new Date().toLocaleTimeString("pt-BR")}.`;
  }

  // ---------------------------------------------------------------------
  // Comparação
  const fmtScore = (s, type) => {
    if (s == null || Number.isNaN(Number(s))) return "—";
    const cfg = SCORE[type];
    return Number(s).toFixed(cfg ? cfg.digits : 3);
  };
  // Erro da API em texto curto: {error, detail} pode trazer detail como objeto
  // (ex.: checks do /ready). Mostra o motivo e só os checks que falharam.
  function errText(d, fallback) {
    if (!d) return fallback || "";
    const det = d.detail;
    if (det == null) return d.error || fallback || "";
    if (typeof det === "string") return det;
    const parts = [];
    if (det.reason) parts.push(det.reason);
    if (det.checks && typeof det.checks === "object") {
      const bad = Object.entries(det.checks).filter(([k, v]) => v === false || v === "MISSING" || v === "POPULATING" || v === "UNAVAILABLE").map(([k, v]) => (v === false ? k : `${k}=${v}`));
      if (bad.length) parts.push(`checks com falha: ${bad.join(", ")}`);
    }
    if (Array.isArray(det)) parts.push(det.map((x) => (x && (x.msg || x.message)) || JSON.stringify(x)).join("; "));
    return parts.length ? `${d.error ? d.error + ": " : ""}${parts.join(" · ")}` : (d.error || JSON.stringify(det).slice(0, 200));
  }
  const docKey = (x) => (x && x.doc_id ? `${x.doc_id}@v${x.version ?? "?"}` : "—");
  const blockText = (r) => BLOCK[r] || r || "motivo não informado";

  function emptyColumns(message) {
    $("#cols").replaceChildren(...Object.entries(STRATS).map(([k, s]) => el("article", { class: "col", "data-strategy": k, "aria-labelledby": `h-${k}` },
      el("div", { class: "col-head" }, el("h3", { id: `h-${k}` }, s.name)),
      el("p", { class: "col-what" }, s.what),
      el("p", { class: "empty-col" }, message),
    )));
  }
  function skeletonColumns() {
    $("#cols").replaceChildren(...Object.entries(STRATS).map(([k, s]) => el("article", { class: "col", "data-strategy": k, "aria-labelledby": `h-${k}` },
      el("div", { class: "col-head" }, el("h3", { id: `h-${k}` }, s.name)),
      el("p", { class: "col-what" }, s.what),
      el("div", { class: "sk", "aria-hidden": "true" }, el("i"), el("i"), el("i"), el("i")),
    )));
  }

  function latencyBlock(lat, scaleMax) {
    if (!lat || typeof lat !== "object") return el("p", { class: "none" }, "Sem latência na resposta.");
    const rows = STAGES.map(([k, label]) => {
      const v = lat[k];
      const na = v == null;
      return el("div", { class: "lat-row" },
        el("span", { class: "k" }, label),
        el("span", { class: "bar", "aria-hidden": "true" }, el("i", { style: `transform: scaleX(${na ? 0 : Math.min(1, Number(v) / scaleMax)})` })),
        el("span", { class: "v" }, na ? "não usa" : `${Number(v).toFixed(1)} ms`));
    });
    rows.push(el("div", { class: "lat-row total" }, el("span", { class: "k" }, "total"), el("span"),
      el("span", { class: "v" }, lat.total == null ? "—" : `${Number(lat.total).toFixed(1)} ms`)));
    return el("div", { class: "lat" }, rows);
  }

  // O backend usa "status" para o status da VERSÃO (active, obsolete...). Se o
  // trecho foi usado na resposta é a citação que diz, então a situação vem dela.
  const VSTATUS = { active: "vigente", obsolete: "obsoleta", draft: "rascunho" };
  function situation(r, cited) {
    if (["selected", "candidate", "filtered", "blocked", "below_threshold", "used"].includes(r.status)) return r.status;
    return cited.has(r.chunk_id) ? "selected" : "candidate";
  }
  function resultsTable(results, stratKey, citations) {
    if (!results || !results.length) return el("p", { class: "none" }, "Nenhum trecho ranqueado.");
    const cited = new Set((citations || []).map((c) => c.chunk_id));
    const type = (results.find((r) => r.score_type) || {}).score_type || { bm25: "bm25", vector: "cosine", graph: "graph-hops" }[stratKey];
    const cfg = SCORE[type] || { unit: type || "score", tip: "Tipo de score informado pelo backend." };
    return el("table", { class: "res" },
      el("caption", { class: "sr" }, `Trechos considerados pela estratégia ${STRATS[stratKey].label}`),
      el("thead", null, el("tr", null,
        el("th", { scope: "col" }, "Fonte e motivo"),
        el("th", { scope: "col" }, "Score ", tipBtn(cfg.tip, `Como ler o score ${cfg.unit}`)),
        el("th", { scope: "col" }, "Situação"))),
      el("tbody", null, results.map((r) => { const sit = situation(r, cited); return el("tr", { "data-status": sit },
        el("td", null, el("span", { class: "docv" }, docKey(r)), r.chunk_id ? el("span", { class: "chunk" }, r.chunk_id) : null, r.reason ? el("span", { class: "why" }, r.reason) : null),
        el("td", null, el("span", { class: "score" }, fmtScore(r.score, r.score_type || type)), el("span", { class: "unit" }, (SCORE[r.score_type] || cfg).unit),
          r.neo4j_score != null ? el("span", { class: "unit" }, `neo4j_score ${Number(r.neo4j_score).toFixed(3)}`) : null,
          r.lexical_score != null ? el("span", { class: "unit" }, `lexical ${Number(r.lexical_score).toFixed(2)}`) : null),
        el("td", null, el("span", { class: "pill", "data-s": sit }, sit === "selected" ? "citada" : (RSTATUS[sit] || sit)),
          r.status && !RSTATUS[r.status] ? el("span", { class: "unit" }, `versão ${VSTATUS[r.status] || r.status}`) : null),
      ); })));
  }

  function explain(key, s) {
    const cites = s.citations || [];
    const sel = (s.results || []).find((r) => r.status === "selected")
      || (s.results || []).find((r) => cites.some((c) => c.chunk_id && c.chunk_id === r.chunk_id))
      || cites[0];
    const blocked = s.blocked || [];
    const parts = [];
    if (s.abstained) {
      parts.push(el("p", null, "Esta estratégia não respondeu: ", ABSTAIN[s.abstain_reason] || s.abstain_reason || "motivo não informado", ". Abster-se é o resultado correto quando nenhuma fonte permitida sustenta a resposta."));
    } else if (sel) {
      const type = sel.score_type || { bm25: "bm25", vector: "cosine", graph: "graph-hops" }[key];
      if (key === "bm25") {
        parts.push(el("p", null, "Escolheu ", code(sel.chunk_id || docKey(sel)), ` porque os termos da pergunta aparecem nesse trecho com peso alto entre as fontes permitidas (score ${fmtScore(sel.score, type)}, ${SCORE.bm25.unit}).`));
        if (cites.length > 1) parts.push(el("p", null, `A resposta juntou ${cites.length} trechos (`, cites.map((c, i) => [i ? ", " : "", code(docKey(c))]), "): palavras em comum bastam para o BM25, mesmo quando o trecho fala de outra regra."));
      }
      if (key === "vector") parts.push(el("p", null, "O embedding da pergunta ficou mais perto do trecho ", code(sel.chunk_id || docKey(sel)), ` (score ${fmtScore(sel.score, type)}, ${SCORE.cosine.unit}) do que dos outros trechos permitidos.`));
      if (key === "graph") {
        const p = s.path || { nodes: [], edges: [] };
        const anchors = (p.nodes || []).filter((n) => n.label === "Product").map((n) => nodeText(n));
        const edgesAll = p.edges || [];
        const chosen = edgesAll.filter((e) => e.role === "selected");
        const types = (chosen.length ? chosen : edgesAll).map((e) => e.type);
        const via = { category: "pela categoria do produto", campaign: "pela campanha da qual o produto participa", "category-campaign": "pela campanha da categoria do produto" }[s.selected_via] || (s.selected_via ? `via ${s.selected_via}` : "");
        parts.push(el("p", null,
          anchors.length ? ["A pergunta foi ligada a ", anchors.map((a, i) => [i ? ", " : "", code(a)]), " por entity linking determinístico (sinônimos do catálogo). "] : "Nenhum produto foi reconhecido na pergunta; a travessia partiu da categoria. ",
          types.length ? ["O caminho escolhido tem ", String(s.path_hops ?? types.length), " relações (", types.join(" → "), ")", via ? `, ${via}` : "", ", e termina em ", code(docKey(sel)), "."] : ["A fonte escolhida foi ", code(docKey(sel)), "."]));
        const others = edgesAll.length - chosen.length;
        if (chosen.length && others > 0) parts.push(el("p", null, `As outras ${others} relações do desenho (tracejadas) levam a versões ou campanhas que o filtro barrou nesta data e tenant: o grafo as encontrou, mas não podem ser usadas.`));
      }
      if (sel.reason) parts.push(el("p", null, "Motivo registrado pelo backend: ", sel.reason, "."));
    } else {
      parts.push(el("p", null, "A resposta não trouxe uma fonte marcada como escolhida."));
    }
    if (blocked.length) parts.push(el("p", null, `Antes de compor, ${blocked.length} fonte(s) foram barradas pelos mesmos filtros de vigência, tenant e acesso das três estratégias: `, blocked.map((b, i) => [i ? "; " : "", code(docKey(b)), ` (${blockText(b.reason)})`]), "."));
    parts.push(el("p", { class: "lim" }, "Limite desta estratégia: ", STRATS[key].limit));
    return parts;
  }

  function column(key, s, scaleMax, top) {
    const meta = STRATS[key];
    const head = el("div", { class: "col-head" }, el("h3", { id: `h-${key}` }, meta.name), el("span", { class: "src" }, s && s.source ? s.source : ""));
    const what = el("p", { class: "col-what" }, meta.what);
    if (!s) {
      return el("article", { class: "col reveal", "data-strategy": key, "aria-labelledby": `h-${key}` }, head, what,
        el("p", { class: "col-error" }, "Esta estratégia não devolveu resultado", s && s.error ? `: ${typeof s.error === "string" ? s.error : JSON.stringify(s.error)}` : " nesta resposta."));
    }
    const answer = el("div", { class: "answer", "data-abstained": String(!!s.abstained) },
      s.abstained ? el("span", { class: "abst" }, "Absteve-se · ", code(s.abstain_reason || "sem motivo"), s.abstain_reason && ABSTAIN[s.abstain_reason] ? ` · ${ABSTAIN[s.abstain_reason]}` : "") : null,
      el("p", null, s.answer || (s.abstained ? "" : "Sem texto de resposta.")));
    const cites = (s.citations || []).length
      ? el("ul", { class: "cites" }, s.citations.map((c) => el("li", { class: "cite", "data-tip": c.chunk_id ? `Trecho: ${c.chunk_id}` : null }, docKey(c))))
      : el("p", { class: "none" }, "Nenhuma citação.");
    const blocked = (s.blocked || []).length
      ? el("ul", { class: "blocked" }, s.blocked.map((b) => el("li", null, code(docKey(b)), el("span", { class: "r" }, blockText(b.reason)), b.reason && BLOCK[b.reason] ? el("span", { class: "muted" }, `(${b.reason})`) : null)))
      : el("p", { class: "none" }, "Nada bloqueado para esta pergunta.");
    const ids = el("dl", { class: "ids" },
      ...[["source", s.source], ["index_name", s.index_name], ["raw_top_k", s.raw_top_k], ["cypher_query_id", s.cypher_query_id],
        ...(key === "graph" ? [["path_hops", s.path_hops], ["selected_via", s.selected_via]] : []),
        ["trace_id", top.trace_id], ["request_id", top.request_id]]
        .flatMap(([k, v]) => [el("dt", null, k), el("dd", { class: v == null || v === "" ? "na" : null }, v == null || v === "" ? "não se aplica" : String(v))]));
    const cypher = s.cypher || s.cypher_query;
    const notes = [
      s.note ? el("p", { class: "note" }, s.note) : null,
      s.fallback && s.fallback.detail ? el("p", { class: "note" }, el("b", null, "Por que não percorreu: "), s.fallback.detail) : null,
      s.recall_note ? el("p", { class: "note" }, el("b", null, "Recall após o filtro: "), s.recall_note) : null,
      s.error ? el("p", { class: "col-error" }, "Erro controlado: ", code(s.error.kind || "erro"), s.error.detail ? ` (${s.error.detail})` : "") : null,
    ].filter(Boolean);
    const whyBox = el("div", { class: "why-box", id: `why-${key}`, hidden: true });
    const whyBtn = el("button", { type: "button", class: "secondary why-btn", "aria-expanded": "false", "aria-controls": `why-${key}` }, "Por que esta fonte?");
    whyBtn.addEventListener("click", () => {
      const open = whyBtn.getAttribute("aria-expanded") !== "true";
      whyBtn.setAttribute("aria-expanded", String(open));
      if (open) whyBox.replaceChildren(...explain(key, s));
      whyBox.hidden = !open;
    });
    return el("article", { class: "col reveal", "data-strategy": key, "aria-labelledby": `h-${key}` },
      head, what, answer, notes,
      el("div", { class: "block" }, el("p", { class: "mini-title" }, "Citações ", tipBtn("doc_id@versão de cada fonte usada na resposta.", "O que são as citações")), cites),
      el("div", { class: "block" }, el("p", { class: "mini-title" }, "Trechos considerados"), resultsTable(s.results, key, s.citations)),
      el("div", { class: "block" }, el("p", { class: "mini-title" }, "Bloqueados ", tipBtn("Fontes que os filtros de vigência, tenant e acesso removeram. Só o identificador e o motivo; o texto bloqueado nunca é exibido.", "O que são os bloqueados")), blocked),
      el("div", { class: "tele" }, el("p", { class: "mini-title" }, "Latência medida no backend ", tipBtn("Tempo de cada etapa medido pelo servidor nesta requisição. As barras usam a mesma escala nas três colunas.", "Como ler a latência")), latencyBlock(s.latency_ms, scaleMax), ids),
      cypher ? el("details", { class: "block" }, el("summary", { class: "mini-title" }, "Cypher parametrizado (somente leitura)"), el("pre", { class: "mono small" }, String(cypher))) : null,
      whyBtn, whyBox);
  }

  function agreement(strats) {
    const picks = Object.keys(STRATS).filter((k) => strats[k]).map((k) => {
      const s = strats[k];
      const c = (s.citations || [])[0];
      return { k, v: s.abstained ? `absteve-se (${s.abstain_reason || "?"})` : (c ? docKey(c) : "sem citação") };
    });
    if (picks.length < 2) return null;
    const same = picks.every((p) => p.v === picks[0].v);
    const who = picks.length === 3 ? "As três" : `As ${picks.length} estratégias que responderam`;
    return el("p", { class: "agree" }, same
      ? [el("b", null, `${who} chegaram ao mesmo resultado: `), code(picks[0].v), ". Concordar não prova que está certo; confira a versão e a data."]
      : [el("b", null, "As estratégias divergiram: "), picks.map((p, i) => [i ? " · " : "", `${STRATS[p.k].label} `, code(p.v)]), ". Abra “Por que esta fonte?” em cada coluna."]);
  }

  async function runCompare(ev) {
    ev.preventDefault();
    const question = qEl.value.trim();
    if (!question) { qEl.focus(); $("#preset-hint").textContent = "Escreva uma pergunta ou escolha uma do roteiro."; return; }
    if (orderEl.value && !orderEl.checkValidity()) { orderEl.focus(); $("#preset-hint").textContent = "Número do pedido no formato Q-0000."; return; }
    const body = { question, as_of_date: S.date, tenant: $("#tenant").value, user_role: $("#role").value, strategies: ["bm25", "vector", "graph"] };
    if (orderEl.value.trim()) body.order_id = orderEl.value.trim();
    if (tokenEl.value.trim()) body.auth_token = tokenEl.value.trim();

    const btn = $("#run");
    btn.disabled = true;
    btn.textContent = "Consultando…";
    $("#comparar").setAttribute("aria-busy", "true");
    skeletonColumns();
    // Nada da rodada anterior pode sobrar na tela enquanto a nova não chega.
    const oldAgree = $(".agree");
    if (oldAgree) oldAgree.remove();
    S.answerGraph = null;
    if (S.graphTab === "answer") drawCurrentGraph();
    $("#run-meta").textContent = "Consultando as três estratégias…";
    const r = await api("lab/compare", { method: "POST", body, timeout: 30000 });
    btn.disabled = false;
    btn.textContent = "Comparar as três";
    $("#comparar").setAttribute("aria-busy", "false");

    if (!r.ok || !r.data || !r.data.strategies) {
      const detail = errText(r.data, r.error);
      const notReady = r.data && r.data.error === "not_ready";
      emptyColumns(`A comparação falhou${r.status ? ` (HTTP ${r.status})` : ""}${detail ? `: ${detail}` : ""}. Nada foi inventado no lugar.${notReady ? " O backend ainda não está pronto; veja /ready no topo e tente de novo em alguns segundos." : ""}`);
      $("#run-meta").replaceChildren("Falha ao chamar ", code("lab/compare"), r.status ? ` · HTTP ${r.status}` : ` · ${r.error || "sem resposta"}`);
      document.querySelectorAll(".col").forEach((c) => c.querySelector(".empty-col").classList.add("col-error"));
      return;
    }
    const data = r.data;
    S.last = { body, data };
    const strats = data.strategies || {};
    const scaleMax = Math.max(1, ...Object.values(strats).map((s) => Number((s && s.latency_ms && s.latency_ms.total) || 0)));
    const box = $("#cols");
    box.replaceChildren(...Object.keys(STRATS).map((k) => column(k, strats[k], scaleMax, data)));
    const ag = agreement(strats);
    if (ag) box.before(ag);

    $("#run-meta").replaceChildren(
      "Data simulada ", code(fmtDate(data.as_of_date || body.as_of_date)), " · tenant ", code(body.tenant), " · papel ", code(body.user_role),
      " · rota ", code(data.route || "—"), " · trace_id ", code(data.trace_id || "—"), ` · ${Math.round(r.ms)} ms ida e volta, medido no navegador`);

    const g = strats.graph;
    S.answerGraph = g ? { nodes: (g.path && g.path.nodes) || [], edges: (g.path && g.path.edges) || [], blocked: g.blocked || [], caption: graphCaption(g, body) } : null;
    if (S.graphTab === "answer") drawCurrentGraph();

    const sig = JSON.stringify([body.question, body.as_of_date, body.tenant, body.user_role, body.order_id || "", body.auth_token || ""]);
    const picks = Object.keys(STRATS).filter((k) => strats[k]).map((k) => {
      const s = strats[k];
      return `${STRATS[k].label} ${s.abstained ? "absteve-se" : docKey((s.citations || [])[0])}`;
    });
    const prevSig = lastSeen[`q:${sig}`];
    const nowPick = picks.join(" · ");
    const who = (S.envs.self && S.envs.self.name) || "esta página";
    if (prevSig && prevSig !== nowPick) addEvent(who, "change", ["mesma pergunta e data, resultado diferente: ", [prevSig], " → ", [nowPick]]);
    else addEvent(who, "run", [`“${body.question.slice(0, 60)}${body.question.length > 60 ? "…" : ""}” em ${fmtDate(body.as_of_date)}: `, [nowPick]]);
    lastSeen[`q:${sig}`] = nowPick;
    persist();
  }

  function graphCaption(g, body) {
    const n = (g.path && g.path.nodes) || [];
    if (!n.length) return ["A estratégia de grafo não percorreu relações para esta pergunta", g.abstained ? ` (absteve-se: ${g.abstain_reason || "?"})` : "", ". Isso também é evidência: sem entidade ou caminho, não há o que mostrar."];
    return ["Nós e arestas devolvidos por ", code(g.cypher_query_id || "consulta de grafo"), ` para a data simulada ${fmtDate(body.as_of_date)} e o tenant `, code(body.tenant), "."];
  }

  // ---------------------------------------------------------------------
  // Grafo com Cytoscape.js. Layout em colunas por tipo de nó, na ordem da
  // pergunta de negócio: Produto → Categoria/Campanha → Política → Versão → Documento → Trecho.
  const LABELS = ["Tenant", "Product", "Category", "Campaign", "Policy", "PolicyVersion", "Document", "Chunk"];
  const LABEL_PT = { Tenant: "Tenant", Product: "Produto", Category: "Categoria", Campaign: "Campanha", Policy: "Política", PolicyVersion: "Versão da política", Document: "Documento", Chunk: "Trecho" };
  const COLUMN = { Tenant: 0, Product: 1, Category: 2, Campaign: 2, Policy: 3, PolicyVersion: 4, Document: 5, Chunk: 6 };
  const SHAPE = { Tenant: "octagon", Product: "round-rectangle", Category: "hexagon", Campaign: "diamond", Policy: "rectangle", PolicyVersion: "round-tag", Document: "barrel", Chunk: "ellipse" };
  const css = getComputedStyle(document.documentElement);
  const cssv = (n) => css.getPropertyValue(n).trim();
  const COLOR = Object.fromEntries(LABELS.map((l) => [l, cssv(`--g-${l.toLowerCase()}`) || "#B4B1AD"]));

  function renderLegend() {
    $("#legend").replaceChildren(
      ...LABELS.map((l) => el("li", { style: `color: ${COLOR[l]}` }, el("span", { class: "sw", "aria-hidden": "true" }), el("span", { style: "color: var(--qc-ink-2)" }, LABEL_PT[l]))),
      el("li", null, el("span", { class: "sw blocked", "aria-hidden": "true" }), "bloqueado ou fora de vigência"),
      el("li", { style: `color: ${cssv("--qc-violet")}` }, el("span", { class: "sw edge", "aria-hidden": "true" }), el("span", { style: "color: var(--qc-ink-2)" }, "relação (tipo no rótulo)")),
    );
  }

  function nodeText(n) {
    const p = n.props || {};
    const tail = String(n.id || "").replace(/^[a-z_]+:/i, "");
    let t;
    switch (n.label) {
      case "PolicyVersion": t = `${p.doc_id || p.policy_id || tail.split("@")[0]} v${p.version ?? String(tail).split("@v")[1] ?? "?"}${p.status ? ` · ${p.status}` : ""}`; break;
      case "Document": t = p.file || (p.doc_id ? `${p.doc_id}@v${p.version ?? "?"}` : (p.title || tail)); break;
      case "Chunk": t = p.section || (tail.includes("#") ? `#${tail.split("#").pop()}` : tail); break;
      default: t = p.name || p.title || tail;
    }
    t = String(t);
    return t.length > 42 ? `${t.slice(0, 41)}…` : t;
  }
  function blockedKeys(list) { return new Set((list || []).map((b) => docKey(b))); }
  function isBlocked(n, keys) {
    // O backend marca o papel de cada nó no caminho: selected, blocked ou context.
    if (n.role === "blocked") return true;
    if (n.role === "selected") return false;
    const p = n.props || {};
    if (p.blocked === true || p.allowed === false || p.active === false) return true;
    if (["obsolete", "blocked", "expired", "future", "inactive"].includes(String(p.status || "").toLowerCase())) return true;
    return !!(p.doc_id && p.version != null && keys.has(`${p.doc_id}@v${p.version}`));
  }

  function layoutPositions(nodes) {
    const cols = new Map();
    for (const n of nodes) {
      const c = COLUMN[n.label] ?? 3;
      if (!cols.has(c)) cols.set(c, []);
      cols.get(c).push(n);
    }
    const MAXR = 6;
    const pos = {};
    let x = 0;
    for (const c of [...cols.keys()].sort((a, b) => a - b)) {
      const list = cols.get(c).sort((a, b) => (a.label === b.label ? (a._blocked - b._blocked) || String(a.id).localeCompare(String(b.id)) : LABELS.indexOf(a.label) - LABELS.indexOf(b.label)));
      const nsub = Math.ceil(list.length / MAXR);
      list.forEach((n, i) => {
        const sub = Math.floor(i / MAXR);
        const inSub = Math.min(MAXR, list.length - sub * MAXR);
        // Nó bloqueado sai um pouco para a direita: a aresta entre versões
        // (SUPERSEDES) fica diagonal e o rótulo não vira texto vertical.
        pos[n.id] = { x: x + sub * 150 + (n._blocked && n.label === "PolicyVersion" && list.length > 1 ? 70 : 0), y: ((i % MAXR) - (inSub - 1) / 2) * 96 };
      });
      x += (c === COLUMN.PolicyVersion ? 265 : 200) + (nsub - 1) * 150;
    }
    return pos;
  }

  function ensureCy() {
    if (S.cy) return S.cy;
    if (typeof window.cytoscape !== "function") return null;
    const ink = cssv("--qc-ink"), ink3 = cssv("--qc-ink-3"), line = cssv("--qc-line-2"), violet = cssv("--qc-violet"), bg = "#131417";
    S.cy = window.cytoscape({
      container: $("#cy"),
      // Sem zoom pela roda do mouse: a página rola normalmente; zoom pelos botões.
      userZoomingEnabled: false,
      boxSelectionEnabled: false,
      minZoom: 0.3, maxZoom: 2.5,
      style: [
        { selector: "node", style: {
          shape: "data(shape)", width: 30, height: 30,
          "background-color": "data(color)", "background-opacity": 0.22,
          "border-width": 2, "border-color": "data(color)",
          label: "data(text)", color: ink, "font-family": cssv("--qc-font"), "font-size": 13,
          "text-valign": "bottom", "text-halign": "center", "text-margin-y": 7,
          "text-wrap": "wrap", "text-max-width": 140,
          "text-background-color": bg, "text-background-opacity": 0.9, "text-background-padding": 2,
          "min-zoomed-font-size": 6,
        } },
        { selector: "node.blocked", style: { "border-style": "dashed", "background-opacity": 0.04, color: ink3 } },
        { selector: "edge", style: {
          width: 1.6, "line-color": line, "target-arrow-color": line, "target-arrow-shape": "triangle", "arrow-scale": 0.9,
          "curve-style": "bezier", label: "data(type)", "font-family": cssv("--qc-mono"), "font-size": 10.5, color: violet,
          "text-rotation": "autorotate", "text-background-color": bg, "text-background-opacity": 1, "text-background-padding": 2,
          "min-zoomed-font-size": 6,
        } },
        { selector: "edge.blocked", style: { "line-style": "dashed", color: ink3 } },
        { selector: "node.focus", style: { "border-width": 4, "background-opacity": 0.45 } },
        { selector: "edge.focus", style: { width: 3, "line-color": violet, "target-arrow-color": violet } },
      ],
    });
    const cy = S.cy;
    const wrap = $("#cy");
    const tipAt = (evt) => {
      const r = wrap.getBoundingClientRect();
      const p = evt.renderedPosition || evt.target.renderedMidpoint?.() || { x: 0, y: 0 };
      showTip(detailNodes(evt.target, true), r.left + p.x, r.top + p.y - 8);
    };
    cy.on("mouseover", "node, edge", (evt) => { tipAt(evt); evt.target.addClass("focus"); wrap.style.cursor = "pointer"; });
    cy.on("mouseout", "node, edge", (evt) => { hideTip(); evt.target.removeClass("focus"); wrap.style.cursor = ""; });
    cy.on("tap", "node, edge", (evt) => { $("#graph-detail").replaceChildren(...detailNodes(evt.target, false)); });
    cy.on("pan zoom", hideTip);
    let t;
    addEventListener("resize", () => { clearTimeout(t); t = setTimeout(() => { cy.resize(); cy.fit(undefined, 36); }, 150); });
    return cy;
  }

  function detailNodes(ele, compact) {
    const d = ele.data();
    const raw = d.raw || {};
    const props = raw.props || {};
    const title = ele.isNode()
      ? el("p", { class: compact ? null : "gd-title" }, el("b", null, LABEL_PT[raw.label] || raw.label || "Nó"), " ", code(String(raw.id)), d.blocked ? " · bloqueado/fora de vigência" : "")
      : el("p", { class: compact ? null : "gd-title" }, el("b", null, raw.type || "Relação"), " ", code(`${raw.source} → ${raw.target}`), d.blocked ? " · leva a item bloqueado" : "");
    const entries = Object.entries(props).sort(([a], [b]) => (a === "provenance" ? -1 : b === "provenance" ? 1 : a.localeCompare(b)));
    if (compact) {
      const prov = props.provenance;
      return [title, el("div", null, el("span", { class: "tp-k" }, "provenance: "), prov ? code(String(prov)) : "não informado"),
        entries.length > 1 ? el("div", { class: "tp-k" }, "Clique para ver todas as propriedades.") : null].filter(Boolean);
    }
    return [title, entries.length
      ? el("dl", null, entries.flatMap(([k, v]) => [el("dt", null, k), el("dd", null, typeof v === "object" ? JSON.stringify(v) : String(v))]))
      : el("p", { class: "muted" }, "Sem propriedades.")];
  }

  function drawGraph(g) {
    const empty = $("#graph-empty");
    const list = $("#graph-list");
    $("#graph-caption").replaceChildren(...(g && g.caption ? [g.caption].flat() : []));
    $("#graph-detail").replaceChildren(el("p", { class: "muted" }, "Passe o mouse ou clique em um nó ou aresta para ver propriedades e ", code("provenance"), "."));
    const nodes = (g && g.nodes) || [];
    const edges = (g && g.edges) || [];
    if (!nodes.length) {
      if (S.cy) S.cy.elements().remove();
      empty.hidden = false;
      empty.textContent = g ? "Nenhum nó nesta resposta." : (S.graphTab === "answer"
        ? "Faça uma comparação para ver o caminho que a estratégia de grafo percorreu no Neo4j, ou abra “Explorar um produto”."
        : "Escolha um produto e clique em “Ver relações”.");
      list.replaceChildren(el("li", { class: "muted" }, "Sem relações para listar."));
      return;
    }
    const keys = blockedKeys(g.blocked);
    nodes.forEach((n) => { n._blocked = isBlocked(n, keys) ? 1 : 0; });
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const pos = layoutPositions(nodes);
    const elements = [
      ...nodes.map((n) => ({ group: "nodes", data: { id: String(n.id), text: nodeText(n), color: COLOR[n.label] || "#B4B1AD", shape: SHAPE[n.label] || "ellipse", blocked: !!n._blocked, raw: n }, position: pos[n.id], classes: n._blocked ? "blocked" : "" })),
      ...edges.filter((e) => byId.has(e.source) && byId.has(e.target)).map((e, i) => {
        const blocked = e.role ? e.role === "blocked" : !!(byId.get(e.target)._blocked || byId.get(e.source)._blocked);
        return { group: "edges", data: { id: `e${i}`, source: String(e.source), target: String(e.target), type: e.type || "", blocked, raw: e }, classes: blocked ? "blocked" : "" };
      }),
    ];
    list.replaceChildren(...edges.map((e) => {
      const s = byId.get(e.source), t = byId.get(e.target);
      const blocked = e.role ? e.role === "blocked" : ((s && s._blocked) || (t && t._blocked));
      return el("li", null, code(s ? nodeText(s) : String(e.source)), " ", el("span", { class: "et" }, e.type || "?"), " ", code(t ? nodeText(t) : String(e.target)), blocked ? el("span", { class: "x" }, " · bloqueado") : null);
    }));
    if (!edges.length) list.replaceChildren(el("li", { class: "muted" }, "Nós sem arestas nesta resposta."));
    const cy = ensureCy();
    if (!cy) {
      empty.hidden = false;
      empty.textContent = "A visualização não carregou (vendor/cytoscape.min.js). A lista de relações ao lado traz o mesmo conteúdo em texto.";
      return;
    }
    empty.hidden = true;
    cy.batch(() => { cy.elements().remove(); cy.add(elements); });
    cy.resize();
    cy.fit(undefined, 36);
    if (cy.zoom() > 1.25) { cy.zoom(1.25); cy.center(); }
  }
  function drawCurrentGraph() { drawGraph(S.graphTab === "answer" ? S.answerGraph : S.productGraph); }

  function setGraphTab(tab) {
    S.graphTab = tab;
    for (const [id, t] of [["#tab-answer", "answer"], ["#tab-product", "product"]]) {
      const b = $(id);
      b.setAttribute("aria-selected", String(t === tab));
      b.tabIndex = t === tab ? 0 : -1;
    }
    $("#graph-panel").setAttribute("aria-labelledby", tab === "answer" ? "tab-answer" : "tab-product");
    $("#explore").hidden = tab !== "product";
    drawCurrentGraph();
  }
  $("#tab-answer").addEventListener("click", () => setGraphTab("answer"));
  $("#tab-product").addEventListener("click", () => setGraphTab("product"));
  $(".tabs").addEventListener("keydown", (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const next = S.graphTab === "answer" ? "product" : "answer";
    setGraphTab(next);
    $(next === "answer" ? "#tab-answer" : "#tab-product").focus();
  });

  $("#explore").addEventListener("submit", async (e) => {
    e.preventDefault();
    const pid = $("#ex-product").value;
    if (!/^[a-z0-9][a-z0-9-]{0,63}$/.test(pid)) { $("#graph-caption").textContent = "Escolha um produto da lista."; return; }
    const date = $("#ex-date").value || S.date;
    const tenant = $("#ex-tenant").value;
    $("#graph-caption").textContent = "Consultando o grafo…";
    const r = await api(`lab/graph/product/${encodeURIComponent(pid)}?as_of_date=${encodeURIComponent(date)}&tenant=${encodeURIComponent(tenant)}`, { timeout: 15000 });
    if (!r.ok) {
      S.productGraph = null;
      drawGraph(null);
      const d = r.data || {};
      $("#graph-caption").replaceChildren("Falha em ", code("lab/graph/product"), r.status ? ` (HTTP ${r.status})` : ` (${r.error || "sem resposta"})`, d.detail || d.error ? `: ${errText(d)}` : "");
      return;
    }
    const d = r.data;
    S.productGraph = {
      nodes: d.nodes || [], edges: d.edges || [], blocked: d.blocked || [],
      caption: ["Relações de ", code(pid), ` em ${fmtDate(date)}, tenant `, code(tenant), d.policy_version ? [". Versão aplicável segundo o grafo: ", code(String(d.policy_version))] : "", d.reason ? [" · ", String(d.reason)] : "", "."],
    };
    drawGraph(S.productGraph);
  });

  $("#cy-in").addEventListener("click", () => { if (S.cy) S.cy.zoom({ level: S.cy.zoom() * 1.25, renderedPosition: { x: S.cy.width() / 2, y: S.cy.height() / 2 } }); });
  $("#cy-out").addEventListener("click", () => { if (S.cy) S.cy.zoom({ level: S.cy.zoom() / 1.25, renderedPosition: { x: S.cy.width() / 2, y: S.cy.height() / 2 } }); });
  $("#cy-fit").addEventListener("click", () => { if (S.cy) S.cy.fit(undefined, 36); });

  // ---------------------------------------------------------------------
  async function loadMeta() {
    const r = await api("lab/meta", { timeout: 12000 });
    const fallbackDates = ["2026-11-14", "2027-11-26", "2027-12-06"];
    if (!r.ok) {
      renderDates(fallbackDates);
      renderTenants(["quantum", "parceiro-x"]);
      renderProducts([]);
      $("#foot-model").textContent = "indisponível: /lab/meta não respondeu";
      return;
    }
    const m = (S.meta = r.data);
    renderDates(Array.isArray(m.dates) && m.dates.length ? m.dates : (m.as_of_dates || fallbackDates));
    if (Array.isArray(m.tenants) && m.tenants.length) renderTenants(m.tenants.map((t) => (typeof t === "string" ? t : t.id)));
    renderProducts(Array.isArray(m.products) ? m.products : []);
    const v = m.versions || {};
    const model = m.model || {};
    const id = model.id || model.embedding_model_id || v.embedding_model_id;
    const dim = model.dim || model.embedding_dim || v.embedding_dim || "?";
    const lic = model.license || model.embedding_license;
    $("#foot-model").textContent = id ? `${id}, ${dim} dimensões${lic ? `, ${lic}` : ""}` : "não informado";
    if (!$("#ver-list").children.length) renderVersions(v);
  }

  form.addEventListener("submit", runCompare);
  $("#health-refresh").addEventListener("click", () => refreshHealth(true));

  renderPresets();
  renderDates(["2026-11-14", "2027-11-26", "2027-12-06"]);
  renderLegend();
  renderTimeline();
  emptyColumns("Escolha uma pergunta do roteiro ou escreva a sua e clique em “Comparar as três”.");
  drawGraph(null);
  loadMeta();
  refreshHealth();
  setInterval(() => { if (!document.hidden) refreshHealth(); }, 15000);
})();
