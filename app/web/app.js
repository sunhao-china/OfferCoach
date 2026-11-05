"use strict";

// OfferCoach 前端：四页（问答/模拟面试/简历分析/面试复盘），每页对应一种业务会话。
// 无构建链 vanilla JS；API 契约见 specs/03-implemented/api/web-api.md。

// ---------- 端点与常量 ----------
const API_SESSIONS = "/api/sessions";
const API_MESSAGES = (sessionId) => `/api/sessions/${sessionId}/messages`;
const API_FILES = (sessionId) => `/api/sessions/${sessionId}/files`;
const API_AUTH_REGISTER = "/api/auth/register";
const API_AUTH_LOGIN = "/api/auth/login";
const API_AUTH_LOGOUT = "/api/auth/logout";
const API_AUTH_CAPTCHA = "/api/auth/captcha";
const BUSINESSES = ["chat", "interview", "resume", "review"];
const API_HISTORY = "/api/history";
const API_DRILL_PLAN = "/api/drill/plan";
const API_DRILL_TODAY = "/api/drill/today";
const API_DRILL_ANSWER = "/api/drill/answer";
const API_DRILL_RECLASSIFY = "/api/drill/reclassify";
const API_DRILL_PROGRESS = "/api/drill/progress";

// ---------- 登录态 ----------
function token() {
  return localStorage.getItem("oc_token") || "";
}

function setAuth(data) {
  localStorage.setItem("oc_token", data.token);
  localStorage.setItem("oc_username", data.username);
}

function clearAuth() {
  localStorage.removeItem("oc_token");
  localStorage.removeItem("oc_username");
  BUSINESSES.forEach((business) => {
    forgetSession(business);
    const box = document.querySelector(`#msgs-${business}`);
    if (box) box.innerHTML = "";
  });
}

// × 关闭登录层后置位：401 不再自动重弹（用户明确选择先浏览；点「登录」或刷新页面恢复）
let authDismissed = false;

function showAuth() {
  authDismissed = false;
  document.querySelector("#auth-view").hidden = false;
  document.querySelector("#auth-user").textContent = "";
  document.querySelector("#auth-open").hidden = true;
  document.querySelector("#auth-logout").hidden = true;
  refreshCaptcha();
}

function hideAuth() {
  document.querySelector("#auth-view").hidden = true;
  document.querySelector("#auth-user").textContent =
    localStorage.getItem("oc_username") || "";
  document.querySelector("#auth-open").hidden = true;
  document.querySelector("#auth-logout").hidden = false;
}

// 关闭登录层（未登录状态下仅浏览页面；后续任何 API 调用 401 会重新弹出登录）
function closeAuth() {
  authDismissed = true;
  document.querySelector("#auth-view").hidden = true;
  document.querySelector("#auth-user").textContent = "";
  document.querySelector("#auth-open").hidden = false;
  document.querySelector("#auth-logout").hidden = true;
}

// 401：清除登录态；若用户已点 × 关闭登录层则不打扰，仅亮出「登录」入口
function onAuthExpired() {
  clearAuth();
  if (authDismissed) {
    document.querySelector("#auth-open").hidden = false;
  } else {
    showAuth();
  }
}

// ---------- 工具 ----------
async function api(path, options = {}) {
  // FormData 由浏览器自动带 multipart boundary，不能手动指定 content-type
  const headers = options.body instanceof FormData
    ? {}
    : { "Content-Type": "application/json" };
  const current = token();
  if (current) headers.Authorization = `Bearer ${current}`;
  const response = await fetch(path, { headers, ...options });
  if (response.status === 401) {
    onAuthExpired();
    throw new Error("登录已过期，请重新登录");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败（${response.status}）`);
  }
  return response.json();
}

let toastTimer = null;
function toast(text) {
  const node = document.querySelector("#toast");
  node.textContent = text;
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 3000);
}

function sessionKey(business) {
  return `oc_session_${business}`;
}
function currentSession(business) {
  return localStorage.getItem(sessionKey(business)) || "";
}
function rememberSession(business, sessionId) {
  localStorage.setItem(sessionKey(business), sessionId);
}
function forgetSession(business) {
  localStorage.removeItem(sessionKey(business));
}

// ---------- 消息渲染 ----------
function addMessage(business, role, content) {
  const box = document.querySelector(`#msgs-${business}`);
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = content;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

function setLoading(business, on) {
  const button = document.querySelector(`form[data-business="${business}"] button`);
  button.disabled = on;
  button.textContent = on ? "思考中…" : business === "resume" ? "分析" : "发送";
}

// ---------- 面试会话选项（难度/岗位，创建时锁定） ----------
function interviewOpts() {
  const difficulty = document.querySelector("#interview-difficulty").value;
  const position = document.querySelector("#interview-position").value.trim();
  return position ? { difficulty, position } : { difficulty };
}

function restoreInterviewOpts() {
  const saved = JSON.parse(localStorage.getItem("oc_interview_opts") || "{}");
  if (saved.difficulty) {
    document.querySelector("#interview-difficulty").value = saved.difficulty;
  }
  if (saved.position) {
    document.querySelector("#interview-position").value = saved.position;
  }
}

// ---------- 登录/注册/登出 ----------
let captchaId = "";

// 拉取新验证码（SVG 转 data URL 展示）；失败保持旧图不报错
async function refreshCaptcha() {
  try {
    const response = await fetch(API_AUTH_CAPTCHA);
    const data = await response.json();
    captchaId = data.captcha_id;
    document.querySelector("#auth-captcha-img").src =
      "data:image/svg+xml;utf8," + encodeURIComponent(data.image);
  } catch (error) {
    // 网络异常时旧验证码仍在；提交会得到服务端 400/401 提示
  }
}

function captchaInput() {
  return {
    id: captchaId,
    code: document.querySelector("#auth-captcha-input").value.trim(),
  };
}

async function authAction(mode) {
  const username = document.querySelector("#auth-username").value.trim();
  const password = document.querySelector("#auth-password").value;
  const errorNode = document.querySelector("#auth-error");
  errorNode.textContent = "";
  if (!username || !password) {
    errorNode.textContent = "请输入用户名和密码";
    return;
  }
  const captcha = captchaInput();
  let response;
  try {
    response = await fetch(mode === "register" ? API_AUTH_REGISTER : API_AUTH_LOGIN, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username,
        password,
        captcha_id: captcha.id,
        captcha_code: captcha.code,
      }),
    });
  } catch (error) {
    errorNode.textContent = "网络错误，请重试";
    return;
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    // 422 detail 是 pydantic 数组：取各条 msg 拼成一行可读文案
    const detail = Array.isArray(body.detail)
      ? body.detail.map((item) => item.msg).join("；")
      : body.detail;
    errorNode.textContent = detail || `请求失败（${response.status}）`;
    if (String(detail || "").includes("验证码")) {
      document.querySelector("#auth-captcha-input").value = "";
      refreshCaptcha();  // 验证码一次一换，失败后必须换新
    }
    return;
  }
  setAuth(body);
  document.querySelector("#auth-password").value = "";
  document.querySelector("#auth-captcha-input").value = "";
  hideAuth();
  await Promise.all(BUSINESSES.map((business) => loadHistory(business).catch(() => {})));
  loadReviewTargets().catch(() => {});
}

async function logout() {
  const current = token();
  if (current) {
    await fetch(API_AUTH_LOGOUT, {
      method: "POST",
      headers: { Authorization: `Bearer ${current}` },
    }).catch(() => {});
  }
  clearAuth();
  showAuth();
}

// ---------- 会话列表页（继续 / 删除） ----------
const SESSION_LABELS = { chat: "问答", interview: "模拟面试", resume: "简历分析", review: "面试复盘" };
const DIFFICULTY_LABELS = { entry: "入门", medium: "中等", hard: "困难" };

function sessionNote(session) {
  if (session.business === "interview") {
    const difficulty = DIFFICULTY_LABELS[session.difficulty] || "";
    return [difficulty, session.position].filter(Boolean).join(" · ");
  }
  if (session.business === "review") {
    const target = session.target_session_id ? `${session.target_session_id.slice(0, 8)}…` : "—";
    return `复盘对象：${target}`;
  }
  return "";
}

async function loadSessions() {
  const box = document.querySelector("#sessions-list");
  let list;
  try {
    const data = await api(API_SESSIONS);
    list = data.sessions;
  } catch (error) {
    box.innerHTML = "";
    box.appendChild(emptySessions(`加载失败：${error.message}`));
    return;
  }
  box.innerHTML = "";
  if (!list.length) {
    box.appendChild(emptySessions("还没有会话，去各页开始第一场练习吧。"));
    return;
  }
  list.forEach((session) => {
    const row = document.createElement("div");
    row.className = "session-row";
    const badge = document.createElement("span");
    badge.className = `badge badge-${session.business}`;
    badge.textContent = SESSION_LABELS[session.business] || session.business;
    const meta = document.createElement("span");
    meta.className = "session-meta";
    meta.textContent = new Date(session.created_at).toLocaleString();
    const note = document.createElement("span");
    note.className = "session-note";
    note.textContent = sessionNote(session);
    const actions = document.createElement("span");
    actions.className = "session-actions";
    const resume = document.createElement("button");
    resume.textContent = "继续";
    resume.addEventListener("click", () => resumeSession(session));
    const del = document.createElement("button");
    del.className = "danger";
    del.textContent = "删除";
    del.addEventListener("click", () => deleteSession(session));
    actions.append(resume, del);
    row.append(badge, meta, note, actions);
    box.appendChild(row);
  });
}

function emptySessions(text) {
  const node = document.createElement("p");
  node.className = "sessions-empty";
  node.textContent = text;
  return node;
}

async function resumeSession(session) {
  rememberSession(session.business, session.session_id);
  document.querySelector(`#msgs-${session.business}`).innerHTML = "";
  switchPage(session.business);
  try {
    await loadHistory(session.business);
  } catch (error) {
    toast(error.message);
  }
}

async function deleteSession(session) {
  const label = SESSION_LABELS[session.business] || "该";
  if (!confirm(`确定删除该${label}会话？删除后不可恢复。`)) return;
  try {
    await api(`${API_SESSIONS}/${session.session_id}`, { method: "DELETE" });
  } catch (error) {
    toast(error.message);
    return;
  }
  if (currentSession(session.business) === session.session_id) {
    forgetSession(session.business);
    document.querySelector(`#msgs-${session.business}`).innerHTML = "";
  }
  toast("会话已删除");
  await loadSessions();
}

// ---------- 会话管理 ----------
async function createSession(business, extra = {}) {
  const body = { business, ...extra };
  if (business === "interview") {
    const opts = interviewOpts();
    Object.assign(body, opts);
    localStorage.setItem("oc_interview_opts", JSON.stringify(opts));
  }
  const created = await api(API_SESSIONS, {
    method: "POST",
    body: JSON.stringify(body),
  });
  rememberSession(business, created.session_id);
  return created.session_id;
}

async function ensureSession(business) {
  const existing = currentSession(business);
  if (existing) return existing;
  if (business === "review") throw new Error("请先选择要复盘的面试会话");
  return createSession(business);
}

async function newSession(business) {
  forgetSession(business);
  document.querySelector(`#msgs-${business}`).innerHTML = "";
  if (business === "review") {
    document.querySelector("#review-target").value = "";
    document.querySelector("#review-start").disabled = true;
    return;
  }
  await createSession(business);
  addMessage(business, "assistant", "新会话已就绪，开始吧。");
}

// ---------- 消息收发 ----------
// 长消息（如整份简历）气泡只显示前 120 字，全文照常提交
function bubbleText(text) {
  return text.length > 120 ? `${text.slice(0, 120)}…（全文已提交）` : text;
}

// 流式渲染：content 块追加到最后一条助手气泡；无助手气泡时新建
function appendToLastMessage(business, text) {
  const box = document.querySelector(`#msgs-${business}`);
  const last = box.lastElementChild;
  if (last && last.classList.contains("assistant")) {
    last.textContent += text;
  } else {
    addMessage(business, "assistant", text);
  }
  box.scrollTop = box.scrollHeight;
}

async function sendMessage(business, text, extra = {}) {
  if (!text.trim()) return;
  let sessionId;
  try {
    sessionId = await ensureSession(business);
  } catch (error) {
    toast(error.message);
    return;
  }
  addMessage(business, "user", bubbleText(text));
  setLoading(business, true);
  try {
    await streamReply(business, sessionId, { content: text, ...extra });
  } catch (error) {
    toast(error.message);
  } finally {
    setLoading(business, false);
  }
}

// SSE 流式读取：逐帧解析 data: JSON，content 追加渲染，done 帧随流结束
async function streamReply(business, sessionId, body) {
  const headers = { "Content-Type": "application/json" };
  const current = token();
  if (current) headers.Authorization = `Bearer ${current}`;
  const response = await fetch(API_MESSAGES(sessionId), {
    method: "POST",
    headers,
    body: JSON.stringify({ ...body, stream: true }),
  });
  if (response.status === 401) {
    onAuthExpired();
    throw new Error("登录已过期，请重新登录");
  }
  if (!response.ok) {
    const parsed = await response.json().catch(() => ({}));
    throw new Error(parsed.detail || `请求失败（${response.status}）`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      for (const line of block.split("\n")) {
        if (!line.startsWith("data: ")) continue;
        const event = JSON.parse(line.slice(6));
        if (typeof event.content === "string") {
          // token 级帧随服务端产出到达，无需人工节奏
          appendToLastMessage(business, event.content);
        } else if (typeof event.error === "string") {
          toast(`服务端出错：${event.error}`);
          return;
        }
      }
    }
  }
}

async function loadHistory(business) {
  const sessionId = currentSession(business);
  if (!sessionId) return;
  const history = await api(API_MESSAGES(sessionId));
  history.messages.forEach((message) => addMessage(business, message.role, message.content));
}

// ---------- 简历页文件上传 ----------
async function uploadResume() {
  const file = document.querySelector("#resume-file").files[0];
  if (!file) {
    toast("请先选择 PDF / docx 文件");
    return;
  }
  let sessionId;
  try {
    sessionId = await ensureSession("resume");
  } catch (error) {
    toast(error.message);
    return;
  }
  const form = new FormData();
  form.append("file", file);
  setLoading("resume", true);
  try {
    const result = await api(API_FILES(sessionId), { method: "POST", body: form });
    document.querySelector('form[data-business="resume"] textarea').value = result.text;
    toast(`已提取 ${result.text.length} 字，正在分析…`);
    await sendMessage("resume", result.text, resumeJdExtra());
  } catch (error) {
    toast(error.message);
  } finally {
    setLoading("resume", false);
  }
}

function resumeJdExtra() {
  const jd = document.querySelector("#resume-jd").value;
  return jd.trim() ? { jd_text: jd } : {};
}

// ---------- 复盘页目标选择 ----------
async function loadReviewTargets() {
  const list = await api(API_SESSIONS);
  const select = document.querySelector("#review-target");
  select.innerHTML = '<option value="">选择要复盘的面试会话…</option>';
  list.sessions
    .filter((session) => session.business === "interview")
    .forEach((session) => {
      const option = document.createElement("option");
      option.value = session.session_id;
      const when = new Date(session.created_at).toLocaleString();
      option.textContent = `${session.session_id.slice(0, 8)}…（${when}）`;
      select.appendChild(option);
    });
}

async function startReview() {
  const target = document.querySelector("#review-target").value;
  if (!target) return;
  forgetSession("review");
  document.querySelector("#msgs-review").innerHTML = "";
  await createSession("review", { target_session_id: target });
  addMessage("review", "assistant", `已创建复盘会话（复盘对象：${target.slice(0, 8)}…）`);
  await sendMessage("review", "请复盘这次面试");
}

// ---------- 历史回顾页（练习成果浏览） ----------
let historyRecords = [];

function historyFilter() {
  const chip = document.querySelector("#history-filters .chip.active");
  return chip ? chip.dataset.filter : "all";
}

function historyNote(record) {
  if (record.business === "interview") {
    const difficulty = DIFFICULTY_LABELS[record.difficulty] || "";
    return [difficulty, record.position].filter(Boolean).join(" · ");
  }
  if (record.business === "review") {
    const target = record.target_session_id
      ? `${record.target_session_id.slice(0, 8)}…`
      : "—";
    return `复盘对象：${target}`;
  }
  return "";
}

async function loadHistoryRecords() {
  const box = document.querySelector("#history-list");
  try {
    const data = await api(API_HISTORY);
    historyRecords = data.records;
  } catch (error) {
    box.innerHTML = "";
    box.appendChild(emptySessions(`加载失败：${error.message}`));
    return;
  }
  renderHistoryRecords();
}

function renderHistoryRecords() {
  const box = document.querySelector("#history-list");
  const filter = historyFilter();
  const records = historyRecords.filter(
    (record) => filter === "all" || record.business === filter
  );
  box.innerHTML = "";
  if (!records.length) {
    box.appendChild(emptySessions("还没有练习记录，去模拟面试或简历分析页开始吧。"));
    return;
  }
  records.forEach((record) => {
    const row = document.createElement("div");
    row.className = "session-row";
    const badge = document.createElement("span");
    badge.className = `badge badge-${record.business}`;
    badge.textContent = SESSION_LABELS[record.business] || record.business;
    const meta = document.createElement("span");
    meta.className = "session-meta";
    meta.textContent = new Date(record.created_at).toLocaleString();
    const note = document.createElement("span");
    note.className = "session-note";
    note.textContent = historyNote(record);
    if (!record.completed) {
      note.textContent = (note.textContent ? `${note.textContent} · ` : "") + "未完成";
    }
    const preview = document.createElement("span");
    preview.className = "history-preview";
    preview.textContent = record.preview;
    const actions = document.createElement("span");
    actions.className = "session-actions";
    const view = document.createElement("button");
    view.textContent = "查看";
    view.addEventListener("click", () => openHistoryDetail(record));
    actions.append(view);
    row.append(badge, meta, note, preview, actions);
    box.appendChild(row);
  });
}

function addHistoryMessage(role, content) {
  const box = document.querySelector("#msgs-history-detail");
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = content;
  box.appendChild(div);
}

async function openHistoryDetail(record) {
  document.querySelector("#history-list").hidden = true;
  document.querySelector("#history-filters").hidden = true;
  document.querySelector("#history-detail").hidden = false;
  const box = document.querySelector("#msgs-history-detail");
  box.innerHTML = "";
  try {
    const history = await api(API_MESSAGES(record.session_id));
    history.messages.forEach((message) => addHistoryMessage(message.role, message.content));
    box.scrollTop = box.scrollHeight;
  } catch (error) {
    toast(error.message);
    closeHistoryDetail();
  }
}

function closeHistoryDetail() {
  document.querySelector("#history-detail").hidden = true;
  document.querySelector("#history-list").hidden = false;
  document.querySelector("#history-filters").hidden = false;
  document.querySelector("#msgs-history-detail").innerHTML = "";
}

// ---------- 每日练习页（计划 / 今日题单 / 判分 / 进度） ----------
const DRILL_DIFFICULTY_LABELS = { entry: "入门", medium: "中等", hard: "困难", mixed: "混合" };
// 库内归一化岗位 → 展示名（未收录的原样显示）
const DRILL_POSITION_DISPLAYS = {
  java_backend: "Java 后端",
  python_backend: "Python",
  c_backend: "C",
  cpp_backend: "C++",
  csharp_backend: "C#",
  ai_agent: "AI Agent",
  project_manager: "项目经理",
};

async function loadDrill() {
  await Promise.all([loadDrillPlan(), loadDrillToday(), loadDrillProgress()]);
}

async function loadDrillPlan() {
  const plan = await api(API_DRILL_PLAN); // 无计划时返回 null
  document.querySelector("#drill-position").value = plan && plan.position
    ? DRILL_POSITION_DISPLAYS[plan.position] || plan.position
    : "";
  document.querySelector("#drill-difficulty").value = plan ? plan.difficulty : "mixed";
  document.querySelector("#drill-count").value = plan ? plan.daily_count : 5;
}

async function saveDrillPlan() {
  const body = {
    position: document.querySelector("#drill-position").value.trim() || null,
    difficulty: document.querySelector("#drill-difficulty").value,
    daily_count: Number(document.querySelector("#drill-count").value),
  };
  const button = document.querySelector("#drill-save-plan");
  button.disabled = true;
  try {
    await api(API_DRILL_PLAN, { method: "PUT", body: JSON.stringify(body) });
    toast("计划已保存");
    await Promise.all([loadDrillToday(), loadDrillProgress()]);
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

async function loadDrillToday() {
  const box = document.querySelector("#drill-list");
  let today;
  try {
    today = await api(API_DRILL_TODAY);
  } catch (error) {
    box.innerHTML = "";
    box.appendChild(emptySessions(error.message)); // 400：还没有设置练习计划…
    return;
  }
  renderDrillToday(today);
}

function renderDrillToday(today) {
  const box = document.querySelector("#drill-list");
  box.innerHTML = "";
  const head = document.createElement("div");
  head.className = "drill-day-head";
  head.textContent = today.completed
    ? `今日 ${today.total} 题全部完成，明天再来一组新题 🎉`
    : `今日题单：已完成 ${today.done} / ${today.total}`;
  if (today.shortage) {
    head.textContent += "（题库不足当日题量）";
  }
  box.appendChild(head);
  today.remaining.forEach((question) => box.appendChild(drillCard(question)));
  if (!today.remaining.length && !today.completed) {
    box.appendChild(emptySessions("今日题单已答完。"));
  }
}

function drillCard(question) {
  const card = document.createElement("div");
  card.className = "drill-card";
  card.dataset.difficulty = question.difficulty;
  const meta = document.createElement("div");
  meta.className = "drill-meta";
  const label = document.createElement("span");
  label.className = "drill-meta-label";
  label.textContent = `${DRILL_DIFFICULTY_LABELS[question.difficulty] || question.difficulty} · ${DRILL_POSITION_DISPLAYS[question.position] || question.position} · ${question.category}`;
  const reclassify = document.createElement("button");
  reclassify.className = "drill-reclassify";
  reclassify.textContent = "分类有误？";
  reclassify.title = "让 AI 重新识别这道题的岗位与类别";
  reclassify.addEventListener("click", () => reclassifyDrill(question.question_id, card));
  meta.append(label, reclassify);
  const text = document.createElement("div");
  text.className = "drill-question";
  text.textContent = question.question;
  const textarea = document.createElement("textarea");
  textarea.rows = 4;
  textarea.placeholder = "写下你的回答（至少 20 字），再提交判分…";
  const actions = document.createElement("div");
  actions.className = "drill-actions";
  const submit = document.createElement("button");
  submit.textContent = "提交判分";
  submit.addEventListener("click", () =>
    submitDrillAnswer(question.question_id, textarea, card, submit)
  );
  actions.append(submit);
  const result = document.createElement("div");
  result.className = "drill-result";
  result.hidden = true;
  card.append(meta, text, textarea, actions, result);
  return card;
}

async function submitDrillAnswer(questionId, textarea, card, submit) {
  const answer = textarea.value.trim();
  if (answer.length < 20) {
    toast("回答至少 20 字，写得更具体些吧");
    return;
  }
  submit.disabled = true;
  submit.textContent = "判分中…";
  try {
    const result = await api(API_DRILL_ANSWER, {
      method: "POST",
      body: JSON.stringify({ question_id: questionId, answer }),
    });
    renderDrillResult(card, result);
    await loadDrillProgress();
    if (result.completed) {
      const today = await api(API_DRILL_TODAY).catch(() => null);
      if (today) renderDrillToday(today);
    }
  } catch (error) {
    toast(error.message);
    submit.disabled = false;
    submit.textContent = "提交判分";
  }
}

function renderDrillResult(card, result) {
  card.querySelector("textarea").remove();
  card.querySelector(".drill-actions").remove();
  const resultNode = card.querySelector(".drill-result");
  resultNode.hidden = false;
  resultNode.innerHTML = "";

  const score = document.createElement("div");
  score.className = "drill-score";
  [
    ["正确性", result.score.correctness],
    ["深度", result.score.depth],
    ["表达", result.score.expression],
  ].forEach(([label, value]) => {
    const item = document.createElement("span");
    item.className = "drill-dim";
    item.textContent = `${label} ${"★".repeat(value)}${"☆".repeat(5 - value)}`;
    score.appendChild(item);
  });
  resultNode.appendChild(score);

  const feedback = document.createElement("div");
  feedback.className = "drill-feedback";
  feedback.textContent = result.feedback || "（无点评）";
  resultNode.appendChild(feedback);

  const tags = document.createElement("div");
  tags.className = "drill-tags";
  result.strengths.forEach((item) => {
    const tag = document.createElement("span");
    tag.className = "tag-good";
    tag.textContent = `优点：${item}`;
    tags.appendChild(tag);
  });
  result.weaknesses.forEach((item) => {
    const tag = document.createElement("span");
    tag.className = "tag-bad";
    tag.textContent = `待改进：${item}`;
    tags.appendChild(tag);
  });
  resultNode.appendChild(tags);

  const refs = document.createElement("details");
  refs.className = "drill-refs";
  const summary = document.createElement("summary");
  summary.textContent = "参考要点对照";
  const list = document.createElement("ol");
  result.reference_points.forEach((point) => {
    const li = document.createElement("li");
    li.textContent = point;
    list.appendChild(li);
  });
  refs.append(summary, list);
  resultNode.appendChild(refs);

  const done = document.createElement("p");
  done.className = "drill-done";
  done.textContent = result.completed
    ? "今日题单全部完成 🎉"
    : `已完成 ${result.progress.done} / ${result.progress.total}`;
  resultNode.appendChild(done);
}

async function reclassifyDrill(questionId, card) {
  const button = card.querySelector(".drill-reclassify");
  button.disabled = true;
  button.textContent = "识别中…";
  try {
    const result = await api(API_DRILL_RECLASSIFY, {
      method: "POST",
      body: JSON.stringify({ question_id: questionId }),
    });
    if (result.changed) {
      const difficulty = DRILL_DIFFICULTY_LABELS[card.dataset.difficulty] || card.dataset.difficulty;
      const display = DRILL_POSITION_DISPLAYS[result.position] || result.position;
      card.querySelector(".drill-meta-label").textContent =
        `${difficulty} · ${display} · ${result.category}`;
      toast(`已纠正分类：${display} · ${result.category}`);
    } else {
      toast("AI 识别结果与当前分类一致，无需纠正");
    }
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "分类有误？";
  }
}

async function loadDrillProgress() {
  const progress = await api(API_DRILL_PROGRESS);
  const parts = [];
  if (progress.today && progress.today.total) {
    parts.push(`今日 ${progress.today.done}/${progress.today.total}`);
  }
  parts.push(`连续 ${progress.streak} 天`);
  parts.push(`累计 ${progress.total_attempts} 题`);
  document.querySelector("#drill-stats").textContent = parts.join(" · ");
}

// ---------- 页签与初始化 ----------
function switchPage(business) {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.page === business);
  });
  document.querySelectorAll(".page").forEach((page) => {
    page.classList.toggle("active", page.id === `page-${business}`);
  });
  if (business === "sessions") loadSessions().catch(() => {});
  if (business === "history") {
    closeHistoryDetail();
    loadHistoryRecords().catch(() => {});
  }
  if (business === "drill") loadDrill().catch(() => {});
  if (business === "review") loadReviewTargets().catch(() => {});
}

function bind() {
  document.querySelector("#auth-login").addEventListener("click", () => authAction("login"));
  document.querySelector("#auth-register").addEventListener("click", () => authAction("register"));
  document.querySelector("#auth-close").addEventListener("click", closeAuth);
  document.querySelector("#auth-open").addEventListener("click", showAuth);
  document.querySelector("#auth-password").addEventListener("keydown", (event) => {
    if (event.key === "Enter") authAction("login");
  });
  document.querySelector("#auth-captcha-img").addEventListener("click", refreshCaptcha);
  document.querySelector("#auth-logout").addEventListener("click", logout);
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => switchPage(tab.dataset.page));
  });
  document.querySelectorAll(".new-session").forEach((button) => {
    button.addEventListener("click", () => newSession(button.dataset.business));
  });
  document.querySelectorAll("form.composer").forEach((form) => {
    const business = form.dataset.business;
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const input = form.querySelector("textarea, input");
      const text = input.value;
      input.value = "";
      const extra = business === "resume" ? resumeJdExtra() : {};
      sendMessage(business, text, extra);
    });
  });
  document.querySelector("#resume-upload").addEventListener("click", uploadResume);
  document.querySelector("#review-start").addEventListener("click", startReview);
  document.querySelector("#review-target").addEventListener("change", (event) => {
    document.querySelector("#review-start").disabled = !event.target.value;
  });
  document.querySelector("#sessions-refresh").addEventListener("click", () => {
    loadSessions().catch(() => {});
  });
  document.querySelector("#history-refresh").addEventListener("click", () => {
    loadHistoryRecords().catch(() => {});
  });
  document.querySelector("#history-back").addEventListener("click", closeHistoryDetail);
  document.querySelectorAll("#history-filters .chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      document.querySelectorAll("#history-filters .chip").forEach((other) => {
        other.classList.toggle("active", other === chip);
      });
      renderHistoryRecords();
    });
  });
  document.querySelector("#drill-save-plan").addEventListener("click", saveDrillPlan);
}

async function init() {
  bind();
  restoreInterviewOpts();
  if (!token()) {
    showAuth();
    return;
  }
  hideAuth();
  await Promise.all(BUSINESSES.map((business) => loadHistory(business).catch(() => {})));
  loadReviewTargets().catch(() => {});
}

init();
