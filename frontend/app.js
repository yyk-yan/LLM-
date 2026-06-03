const API = '';  // 同源调用
const TOKEN_KEY = 'doc_fill_token';
const USER_KEY = 'doc_fill_user';

let token = localStorage.getItem(TOKEN_KEY) || '';
let currentUser = null;
try { currentUser = JSON.parse(localStorage.getItem(USER_KEY) || 'null'); } catch (_) { currentUser = null; }

let selectedSrcFiles = [];
let selectedTplFile = null;

// ========== HTTP wrapper ==========
async function api(path, opts = {}) {
  const headers = opts.headers || {};
  if (token) headers['Authorization'] = 'Bearer ' + token;
  if (opts.json !== undefined) {
    headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(opts.json);
    delete opts.json;
  }
  const res = await fetch(API + path, { ...opts, headers });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    // 登录与注册接口的 401/400 直接把后端的 detail 透出来；其他接口的 401 视为会话过期
    const isAuthEndpoint = path.startsWith('/api/auth/login') || path.startsWith('/api/auth/register');
    if (res.status === 401 && !isAuthEndpoint) {
      doLogout();
      throw new Error('未登录或登录已过期');
    }
    throw new Error(formatApiError(data, res.status));
  }
  return data;
}

// FastAPI 422 的 detail 是数组：[{loc:[...], msg, type}, ...]，把它拍平成可读字符串
function formatApiError(data, status) {
  const detail = data && data.detail;
  if (!detail) return 'HTTP ' + status;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map(e => {
      const field = Array.isArray(e.loc) ? e.loc.filter(x => x !== 'body').join('.') : '';
      const msg = e.msg || JSON.stringify(e);
      return field ? `${field}: ${msg}` : msg;
    }).join('； ');
  }
  return JSON.stringify(detail);
}

// ========== 视图切换 ==========
function showAuth() {
  document.getElementById('authView').classList.remove('hidden');
  document.getElementById('appView').classList.add('hidden');
}

function showApp() {
  document.getElementById('authView').classList.add('hidden');
  document.getElementById('appView').classList.remove('hidden');
  resetMainPanelUI();             // 每次进入主界面先清一遍上一个账号的痕迹
  renderUserBar();
  showPanel('main');
  setupAppEventsOnce();
  refreshSourceCount();           // 从后端拉本账号当前的数据源数量
}

function showPanel(name) {
  ['mainPanel', 'adminPanel', 'changePwdPanel', 'settingsPanel'].forEach(id => {
    document.getElementById(id).classList.add('hidden');
  });
  if (name === 'main') document.getElementById('mainPanel').classList.remove('hidden');
  if (name === 'admin') {
    document.getElementById('adminPanel').classList.remove('hidden');
    loadAdminData();
  }
  if (name === 'changePwd') document.getElementById('changePwdPanel').classList.remove('hidden');
  if (name === 'settings') {
    document.getElementById('settingsPanel').classList.remove('hidden');
    loadLlmSettings();
  }
}

function switchAuthTab(name) {
  document.getElementById('tabLogin').classList.toggle('active', name === 'login');
  document.getElementById('tabRegister').classList.toggle('active', name === 'register');
  document.getElementById('loginPanel').classList.toggle('hidden', name !== 'login');
  document.getElementById('registerPanel').classList.toggle('hidden', name !== 'register');
}

function renderUserBar() {
  if (!currentUser) return;
  document.getElementById('userInfo').textContent =
    `${currentUser.username} @ ${currentUser.tenant_name || currentUser.tenant_slug}`;
  const roleMap = { super_admin: '超级管理员', tenant_admin: '租户管理员', member: '成员' };
  document.getElementById('userRole').textContent = roleMap[currentUser.role] || currentUser.role;
  const isAdmin = currentUser.role === 'super_admin' || currentUser.role === 'tenant_admin';
  document.getElementById('adminLink').classList.toggle('hidden', !isAdmin);
}

// ========== 登录 / 注册 / 退出 ==========
async function doLogin() {
  const id = document.getElementById('loginId').value.trim();
  const pwd = document.getElementById('loginPwd').value;
  if (!id || !pwd) {
    return showMsg('loginMsg', 'msg-err', '请输入账号和密码');
  }
  try {
    const data = await api('/api/auth/login', { method: 'POST', json: { username_or_email: id, password: pwd } });
    saveAuth(data.access_token, data.user);
    showApp();
  } catch (e) {
    showMsg('loginMsg', 'msg-err', '登录失败：' + e.message);
  }
}

async function doRegister() {
  const code = document.getElementById('regCode').value.trim();
  const username = document.getElementById('regUsername').value.trim();
  const email = document.getElementById('regEmail').value.trim();
  const pwd = document.getElementById('regPwd').value;
  if (!code || !username || !email || !pwd) {
    return showMsg('regMsg', 'msg-err', '请填写所有必填项');
  }
  try {
    const data = await api('/api/auth/register', {
      method: 'POST',
      json: { invitation_code: code, username, email, password: pwd }
    });
    saveAuth(data.access_token, data.user);
    showApp();
  } catch (e) {
    showMsg('regMsg', 'msg-err', '注册失败：' + e.message);
  }
}

function saveAuth(t, user) {
  token = t;
  currentUser = user;
  localStorage.setItem(TOKEN_KEY, t);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

function resetMainPanelUI() {
  // 切换账号或退出时调用：清空填表/管理面板里的所有 DOM 残留
  selectedSrcFiles = [];
  selectedTplFile = null;

  const ids = [
    'srcList', 'srcMsg', 'tplInfo', 'fillResult',
    'usersBox', 'invBox', 'tenantsBox', 'pwdMsg',
    'loginMsg', 'regMsg', 'llmMsg',
  ];
  ids.forEach(id => { const el = document.getElementById(id); if (el) el.innerHTML = ''; });

  const valIds = [
    'reqInput', 'loginId', 'loginPwd',
    'regCode', 'regUsername', 'regEmail', 'regPwd',
    'oldPwd', 'newPwd', 'invEmail',
    'newTenantSlug', 'newTenantName', 'newTenantAdminEmail',
    'llmModel', 'llmBaseUrl', 'llmApiKey',
  ];
  valIds.forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });

  // 文件 input 必须重置 value 才能再次选择同名文件
  const srcInput = document.getElementById('srcInput');
  const tplInput = document.getElementById('tplInput');
  if (srcInput) srcInput.value = '';
  if (tplInput) tplInput.value = '';

  const srcCnt = document.getElementById('srcCnt');
  if (srcCnt) srcCnt.textContent = '0 个';

  const uploadBtn = document.getElementById('uploadBtn');
  const fillBtn = document.getElementById('fillBtn');
  if (uploadBtn) uploadBtn.disabled = true;
  if (fillBtn) fillBtn.disabled = true;

  const fillLoading = document.getElementById('fillLoading');
  if (fillLoading) fillLoading.style.display = 'none';
}

function doLogout() {
  token = '';
  currentUser = null;
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  resetMainPanelUI();
  showAuth();
}

// ========== 改密 ==========
async function changePassword() {
  const oldPwd = document.getElementById('oldPwd').value;
  const newPwd = document.getElementById('newPwd').value;
  if (!oldPwd || !newPwd) return showMsg('pwdMsg', 'msg-err', '两项都需填写');
  try {
    await api('/api/auth/change-password', { method: 'POST', json: { old_password: oldPwd, new_password: newPwd } });
    showMsg('pwdMsg', 'msg-info', '✅ 密码已更新');
    document.getElementById('oldPwd').value = '';
    document.getElementById('newPwd').value = '';
  } catch (e) {
    showMsg('pwdMsg', 'msg-err', '失败：' + e.message);
  }
}

// ========== LLM 设置 ==========
async function loadLlmSettings() {
  try {
    const data = await api('/api/auth/llm-settings');
    document.getElementById('llmModel').value = data.model || '';
    document.getElementById('llmBaseUrl').value = data.base_url || '';
    document.getElementById('llmApiKey').value = '';  // 永远不回显
    document.getElementById('llmApiKeyHint').textContent =
      data.api_key_set ? '✅ 已保存（出于安全考虑不显示明文，留空提交则保持不变）' : '';
    document.getElementById('llmMsg').innerHTML = '';
  } catch (e) {
    showMsg('llmMsg', 'msg-err', '加载失败：' + e.message);
  }
}

async function saveLlmSettings() {
  const body = {
    model: document.getElementById('llmModel').value,
    base_url: document.getElementById('llmBaseUrl').value,
  };
  // api_key 框为空表示"不修改"；非空时才提交
  const k = document.getElementById('llmApiKey').value;
  if (k) body.api_key = k;
  try {
    await api('/api/auth/llm-settings', { method: 'PUT', json: body });
    showMsg('llmMsg', 'msg-info', '✅ 已保存');
    loadLlmSettings();
  } catch (e) {
    showMsg('llmMsg', 'msg-err', '保存失败：' + e.message);
  }
}

async function clearLlmApiKey() {
  if (!confirm('确定要清除已保存的 API Key 吗？')) return;
  try {
    await api('/api/auth/llm-settings', { method: 'PUT', json: { api_key: '' } });
    showMsg('llmMsg', 'msg-info', '✅ API Key 已清除');
    loadLlmSettings();
  } catch (e) {
    showMsg('llmMsg', 'msg-err', '清除失败：' + e.message);
  }
}

// ========== 管理面板 ==========
async function loadAdminData() {
  if (currentUser.role === 'super_admin') {
    document.getElementById('tenantsCard').classList.remove('hidden');
    document.getElementById('invTenantWrap').classList.remove('hidden');
    await loadTenants();         // 同时填充邀请码面板的租户下拉
  } else {
    document.getElementById('tenantsCard').classList.add('hidden');
    document.getElementById('invTenantWrap').classList.add('hidden');
  }
  loadUsers();
  loadInvitations();
}

async function loadUsers() {
  try {
    const data = await api('/api/admin/users');
    const items = data.items || [];
    const rows = items.map(u =>
      `<tr>
        <td>${u.id}</td>
        <td>${u.username}</td>
        <td>${u.email}</td>
        <td>${u.tenant_slug || ''}</td>
        <td>
          <select onchange="updateUser(${u.id}, 'role', this.value)" ${u.id === currentUser.id ? 'disabled' : ''}>
            ${roleOption('member', u.role)}
            ${roleOption('tenant_admin', u.role)}
            ${currentUser.role === 'super_admin' ? roleOption('super_admin', u.role) : ''}
          </select>
        </td>
        <td>
          <button class="btn btn-ghost" onclick="updateUser(${u.id}, 'is_active', ${!u.is_active})" ${u.id === currentUser.id ? 'disabled' : ''}>
            ${u.is_active ? '停用' : '启用'}
          </button>
        </td>
      </tr>`
    ).join('');
    document.getElementById('usersBox').innerHTML = `
      <table>
        <thead><tr><th>ID</th><th>用户名</th><th>邮箱</th><th>租户</th><th>角色</th><th>状态</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="6" style="text-align:center;color:#999">暂无</td></tr>'}</tbody>
      </table>`;
  } catch (e) {
    document.getElementById('usersBox').innerHTML = `<div class="msg msg-err">${e.message}</div>`;
  }
}

function roleOption(value, current) {
  const labels = { member: '成员', tenant_admin: '租户管理员', super_admin: '超级管理员' };
  return `<option value="${value}" ${value === current ? 'selected' : ''}>${labels[value]}</option>`;
}

async function updateUser(userId, field, value) {
  try {
    await api(`/api/admin/users/${userId}`, { method: 'PATCH', json: { [field]: value } });
    loadUsers();
  } catch (e) {
    alert('更新失败：' + e.message);
    loadUsers();
  }
}

async function createInvitation() {
  const role = document.getElementById('invRole').value;
  const email = document.getElementById('invEmail').value.trim() || null;
  const ttl = parseInt(document.getElementById('invTtl').value) || 72;
  const body = { role, email, ttl_hours: ttl };
  if (currentUser.role === 'super_admin') {
    const tid = document.getElementById('invTenantId').value;
    if (!tid) return alert('请选择租户');
    body.tenant_id = parseInt(tid);
  }
  try {
    await api('/api/admin/invitations', { method: 'POST', json: body });
    document.getElementById('invEmail').value = '';
    loadInvitations();
  } catch (e) {
    alert('生成邀请码失败：' + e.message);
  }
}

async function loadInvitations() {
  try {
    const data = await api('/api/admin/invitations');
    const items = data.items || [];
    const rows = items.map(i =>
      `<tr>
        <td><span class="copy-code" onclick="copyText('${i.code}')" title="点击复制">${i.code}</span></td>
        <td>${roleLabel(i.role)}</td>
        <td>${i.email || '不限'}</td>
        <td>${i.tenant_slug || ''}</td>
        <td>${i.is_valid ? '<span class="ok">有效</span>' : (i.used_at ? '已使用' : '已过期')}</td>
        <td>${(i.expires_at || '').replace('T', ' ').slice(0, 19)}</td>
      </tr>`
    ).join('');
    document.getElementById('invBox').innerHTML = `
      <table>
        <thead><tr><th>邀请码</th><th>角色</th><th>限定邮箱</th><th>租户</th><th>状态</th><th>过期时间(UTC)</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="6" style="text-align:center;color:#999">暂无</td></tr>'}</tbody>
      </table>`;
  } catch (e) {
    document.getElementById('invBox').innerHTML = `<div class="msg msg-err">${e.message}</div>`;
  }
}

function roleLabel(r) {
  return ({ super_admin: '超级管理员', tenant_admin: '租户管理员', member: '成员' })[r] || r;
}

function copyText(s) {
  navigator.clipboard.writeText(s).then(() => {
    alert('已复制：' + s);
  });
}

async function loadTenants() {
  try {
    const data = await api('/api/admin/tenants');
    const items = data.items || [];
    const rows = items.map(t =>
      `<tr><td>${t.id}</td><td>${t.slug}</td><td>${t.name}</td><td>${t.user_count}</td></tr>`
    ).join('');
    document.getElementById('tenantsBox').innerHTML = `
      <table>
        <thead><tr><th>ID</th><th>slug</th><th>名称</th><th>成员数</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;

    // 同步填充邀请码面板的租户下拉
    const sel = document.getElementById('invTenantId');
    if (sel) {
      const prev = sel.value;
      sel.innerHTML = items.map(t =>
        `<option value="${t.id}">${t.slug} — ${t.name}</option>`
      ).join('');
      // 保留之前选中项；否则默认当前用户所在租户
      if (prev && items.some(t => String(t.id) === prev)) {
        sel.value = prev;
      } else if (items.some(t => t.id === currentUser.tenant_id)) {
        sel.value = String(currentUser.tenant_id);
      }
    }
  } catch (e) {
    document.getElementById('tenantsBox').innerHTML = `<div class="msg msg-err">${e.message}</div>`;
  }
}

async function createTenant() {
  const slug = document.getElementById('newTenantSlug').value.trim();
  const name = document.getElementById('newTenantName').value.trim();
  const adminEmail = document.getElementById('newTenantAdminEmail').value.trim();
  if (!slug || !name) return alert('slug 和名称必填');
  if (!adminEmail) return alert('管理员邮箱必填，新租户至少需要一名管理员');
  try {
    const data = await api('/api/admin/tenants', { method: 'POST', json: { slug, name, admin_email: adminEmail } });
    document.getElementById('newTenantSlug').value = '';
    document.getElementById('newTenantName').value = '';
    document.getElementById('newTenantAdminEmail').value = '';
    if (data.admin_invitation) {
      alert(`租户已创建。\n管理员邀请码：${data.admin_invitation.code}\n请发送给 ${data.admin_invitation.email}`);
    }
    await loadTenants();
    loadInvitations();
  } catch (e) {
    alert('创建失败：' + e.message);
  }
}

// ========== 主功能：上传 / 填表 ==========
function setupDrop(zoneId, onFiles) {
  const zone = document.getElementById(zoneId);
  zone.addEventListener('dragover', e => { e.preventDefault(); zone.style.borderColor = '#1677ff'; });
  zone.addEventListener('dragleave', () => { zone.style.borderColor = '#d9d9d9'; });
  zone.addEventListener('drop', e => {
    e.preventDefault();
    zone.style.borderColor = '#d9d9d9';
    onFiles(e.dataTransfer.files);
  });
}

let _appEventsBound = false;
function setupAppEventsOnce() {
  if (_appEventsBound) return;
  _appEventsBound = true;
  setupDrop('srcZone', files => {
    document.getElementById('srcInput').files = files;
    onSrcSelected(files);
  });
  setupDrop('tplZone', files => {
    document.getElementById('tplInput').files = files;
    onTplSelected(files);
  });
  document.getElementById('srcInput').addEventListener('change', e => onSrcSelected(e.target.files));
  document.getElementById('tplInput').addEventListener('change', e => onTplSelected(e.target.files));
  refreshSourceCount();
}

async function refreshSourceCount() {
  try {
    const data = await api('/api/sources');
    document.getElementById('srcCnt').textContent = `${data.count} 个`;
  } catch (_) { /* 忽略 */ }
}

function onSrcSelected(files) {
  selectedSrcFiles = Array.from(files);
  const list = document.getElementById('srcList');
  list.innerHTML = selectedSrcFiles.map(f =>
    `<div class="file-item"><span class="file-name">📄 ${f.name}</span><span class="file-size">${(f.size/1024).toFixed(1)} KB</span><span class="tag tag-wait">待上传</span></div>`
  ).join('');
  document.getElementById('uploadBtn').disabled = selectedSrcFiles.length === 0;
  document.getElementById('srcMsg').innerHTML = '';
}

function onTplSelected(files) {
  if (!files.length) return;
  selectedTplFile = files[0];
  document.getElementById('tplInfo').innerHTML =
    `<div class="file-item"><span class="file-name">📋 ${selectedTplFile.name}</span><span class="file-size">${(selectedTplFile.size/1024).toFixed(1)} KB</span><span class="tag tag-wait">已选择</span></div>`;
  document.getElementById('fillBtn').disabled = false;
}

async function uploadSources() {
  if (!selectedSrcFiles.length) return;
  const btn = document.getElementById('uploadBtn');
  btn.disabled = true;
  btn.textContent = '上传中...';
  document.getElementById('srcMsg').innerHTML = '<div class="msg msg-info">正在提取文档内容...</div>';

  const fd = new FormData();
  selectedSrcFiles.forEach(f => fd.append('files', f));

  try {
    const data = await api('/api/upload-sources', { method: 'POST', body: fd });
    const list = document.getElementById('srcList');
    list.innerHTML = data.details.map(d =>
      `<div class="file-item">
        <span class="file-name">📄 ${d.filename}</span>
        <span class="file-size">${d.chars ? d.chars.toLocaleString() + ' 字符' : ''}</span>
        <span class="tag ${d.status === '成功' ? 'tag-ok' : 'tag-err'}">${d.status}</span>
      </div>`
    ).join('');
    document.getElementById('srcCnt').textContent = `${data.total_sources} 个`;
    document.getElementById('srcMsg').innerHTML =
      `<div class="msg msg-info">✅ 成功提取 ${data.uploaded} 个文件，共 ${data.total_sources} 个数据源已就绪</div>`;
  } catch (e) {
    document.getElementById('srcMsg').innerHTML = `<div class="msg msg-err">❌ 上传失败: ${e.message}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '上传并提取文本';
  }
}

async function clearSources() {
  try {
    await api('/api/sources', { method: 'DELETE' });
    selectedSrcFiles = [];
    document.getElementById('srcList').innerHTML = '';
    document.getElementById('srcCnt').textContent = '0 个';
    document.getElementById('srcMsg').innerHTML = '<div class="msg msg-info">数据源已清空</div>';
    document.getElementById('uploadBtn').disabled = true;
  } catch (e) {
    alert('清空失败: ' + e.message);
  }
}

async function fillTemplate() {
  if (!selectedTplFile) return;
  const btn = document.getElementById('fillBtn');
  btn.disabled = true;
  document.getElementById('fillLoading').style.display = 'flex';
  document.getElementById('fillResult').innerHTML = '';

  const fd = new FormData();
  fd.append('template', selectedTplFile);
  const req = document.getElementById('reqInput').value.trim();
  if (req) fd.append('requirement', req);

  const start = Date.now();
  try {
    const res = await fetch(API + '/api/fill-template', {
      method: 'POST',
      body: fd,
      headers: token ? { 'Authorization': 'Bearer ' + token } : {},
    });
    const data = await res.json();
    if (!res.ok) {
      document.getElementById('fillResult').innerHTML =
        `<div class="result-box"><div class="rrow"><span class="rlabel">状态</span><span class="rval err">❌ 失败</span></div><div class="rrow"><span class="rlabel">错误信息</span><span class="rval err">${data.detail}</span></div></div>`;
      return;
    }
    const hits = data.rag_hits || [];
    let ragHtml;
    if (hits.length) {
      const items = hits.map(h =>
        `<div class="rrow"><span class="rlabel" style="width:auto">🎯 ${h.target}</span><span class="rval">← ${h.source}（相似度 ${h.similarity}）</span></div>`
      ).join('');
      ragHtml = `
        <div class="rrow"><span class="rlabel">RAG 命中</span><span class="rval ok">${hits.length} 个字段直接命中知识库</span></div>
        ${items}`;
    } else {
      ragHtml = `<div class="rrow"><span class="rlabel">RAG 命中</span><span class="rval">本次无命中（首次运行或全部走 LLM 匹配）</span></div>`;
    }
    document.getElementById('fillResult').innerHTML = `
      <div class="result-box">
        <div class="rrow"><span class="rlabel">状态</span><span class="rval ok">✅ 填表成功</span></div>
        <div class="rrow"><span class="rlabel">响应时间</span><span class="rval">${data.elapsed_seconds} 秒</span></div>
        <div class="rrow"><span class="rlabel">输出文件</span><span class="rval">${data.output_file}</span></div>
        ${ragHtml}
        <a class="dl-btn" href="javascript:void(0)" onclick="downloadFile('${data.output_file}')">⬇️ 下载填写结果</a>
      </div>`;
  } catch (e) {
    document.getElementById('fillResult').innerHTML =
      `<div class="result-box"><div class="rrow"><span class="rlabel">状态</span><span class="rval err">❌ 请求失败</span></div><div class="rrow"><span class="rlabel">错误</span><span class="rval err">${e.message}</span></div></div>`;
  } finally {
    btn.disabled = false;
    document.getElementById('fillLoading').style.display = 'none';
  }
}

async function downloadFile(filename) {
  // 下载需要带 Authorization 头，所以走 fetch + Blob，而不是 <a href>
  try {
    const res = await fetch(API + '/api/download/' + encodeURIComponent(filename), {
      headers: token ? { 'Authorization': 'Bearer ' + token } : {},
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || ('HTTP ' + res.status));
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    alert('下载失败：' + e.message);
  }
}

// ========== 工具 ==========
function showMsg(elId, cls, text) {
  document.getElementById(elId).innerHTML = `<div class="msg ${cls}">${text}</div>`;
}

// ========== 启动 ==========
(async function init() {
  if (!token || !currentUser) {
    showAuth();
    return;
  }
  // 校验 token 是否仍然有效
  try {
    const me = await api('/api/auth/me');
    currentUser = me;
    localStorage.setItem(USER_KEY, JSON.stringify(me));
    showApp();
  } catch (_) {
    doLogout();
  }
})();
