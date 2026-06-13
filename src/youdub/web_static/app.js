const STEPS = [
  ["extract-audio", "提取音频"],
  ["separate-audio", "人声分离"],
  ["transcribe", "识别"],
  ["translate", "翻译"],
  ["tts", "配音"],
  ["transcribe-tts", "配音识别"],
  ["subtitle", "字幕"],
  ["synthesize", "合成"],
  ["prepare-publish", "发布包"],
  ["publish-bilibili", "Bilibili dry-run"],
]

const state = {
  tasks: [],
  selectedId: null,
  loading: false,
}

const $ = (id) => document.getElementById(id)

async function api(path, options = {}) {
  const headers = options.body instanceof FormData ? {} : {"Content-Type": "application/json"}
  const response = await fetch(path, {...options, headers: {...headers, ...(options.headers || {})}})
  if (!response.ok) {
    let detail = response.statusText
    try {
      const payload = await response.json()
      detail = payload.detail || detail
    } catch {}
    throw new Error(detail)
  }
  if (response.status === 204) return null
  return response.json()
}

function fmtTime(value) {
  if (!value) return "-"
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function fmtSize(bytes) {
  if (!Number.isFinite(bytes)) return ""
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function statusLabel(status) {
  return {
    pending: "等待",
    running: "运行中",
    success: "成功",
    failed: "失败",
    skipped: "跳过",
  }[status] || status || "等待"
}

function statusClass(status) {
  if (status === "success") return "status-success"
  if (status === "failed") return "status-failed"
  if (status === "running") return "status-running"
  return "status-pending"
}

function setMessage(id, text, isError = false) {
  const node = $(id)
  node.textContent = text || ""
  node.style.color = isError ? "var(--danger)" : "var(--muted)"
}

async function refreshDoctor() {
  try {
    const doctor = await api("/api/doctor")
    const cookie = doctor.cookies_configured ? "cookies ready" : "no cookies"
    const openai = doctor.openai_api_key_configured ? "OpenAI ready" : "OpenAI not set"
    $("runtimeLine").textContent = `${cookie} · ${openai} · ${doctor.root}`
  } catch (error) {
    $("runtimeLine").textContent = error.message
  }
}

async function refreshTasks() {
  const payload = await api("/api/tasks")
  state.tasks = payload.tasks
  renderTasks()
  if (state.selectedId) {
    const selected = state.tasks.find((task) => task.id === state.selectedId)
    if (selected) renderDetail(selected)
  }
}

function renderTasks() {
  $("taskCount").textContent = String(state.tasks.length)
  const list = $("taskList")
  list.innerHTML = ""
  if (!state.tasks.length) {
    const empty = document.createElement("div")
    empty.className = "empty-state"
    empty.innerHTML = "<p>暂无任务</p>"
    list.appendChild(empty)
    return
  }
  for (const task of state.tasks) {
    const item = document.createElement("button")
    item.type = "button"
    item.className = `task-item ${task.id === state.selectedId ? "active" : ""}`
    item.innerHTML = `
      <span class="task-title">${escapeHtml(task.title || task.source || task.id)}</span>
      <span class="task-meta">
        <span class="status-badge ${statusClass(task.status)}">${statusLabel(task.running ? "running" : task.status)}</span>
        <span>${fmtTime(task.updated_at)}</span>
      </span>
    `
    item.addEventListener("click", () => selectTask(task.id))
    list.appendChild(item)
  }
}

async function selectTask(taskId) {
  state.selectedId = taskId
  const task = await api(`/api/tasks/${taskId}`)
  renderTasks()
  renderDetail(task)
}

function renderDetail(task) {
  $("emptyDetail").classList.add("hidden")
  $("taskDetail").classList.remove("hidden")
  $("detailTitle").textContent = task.title || task.id
  $("detailSource").textContent = task.source || ""
  $("detailStatus").className = `status-badge ${statusClass(task.running ? "running" : task.status)}`
  $("detailStatus").textContent = statusLabel(task.running ? "running" : task.status)
  $("detailId").textContent = task.id
  $("detailAuthor").textContent = task.author || "-"
  $("detailFolder").textContent = task.folder
  $("detailUpdated").textContent = fmtTime(task.updated_at)
  $("detailError").classList.toggle("hidden", !task.error)
  $("detailError").textContent = task.error || ""
  $("runAllButton").disabled = task.running
  $("deleteButton").disabled = task.running
  renderSteps(task)
  renderArtifacts(task)
}

function renderSteps(task) {
  const grid = $("stepsGrid")
  grid.innerHTML = ""
  for (const [step, label] of STEPS) {
    const status = task.steps?.[step] || "pending"
    const card = document.createElement("div")
    card.className = "step-card"
    const buttonLabel = step === "publish-bilibili" ? "dry-run" : "运行"
    card.innerHTML = `
      <div>
        <h3>${label}</h3>
        <span class="status-badge ${statusClass(status)}">${statusLabel(status)}</span>
      </div>
      <button class="button secondary" type="button" ${task.running ? "disabled" : ""}>${buttonLabel}</button>
    `
    card.querySelector("button").addEventListener("click", () => runStep(task.id, step))
    grid.appendChild(card)
  }
}

function renderArtifacts(task) {
  const list = $("artifactList")
  list.innerHTML = ""
  const artifacts = task.artifacts || []
  if (!artifacts.length) {
    list.textContent = "暂无产物"
  } else {
    for (const artifact of artifacts) {
      const link = document.createElement("a")
      link.className = "artifact-link"
      link.href = `/api/tasks/${task.id}/artifacts/${artifact.key}?download=1`
      link.textContent = `${artifact.name} · ${fmtSize(artifact.size)}`
      list.appendChild(link)
    }
  }
  const final = artifacts.find((item) => item.key === "final-video")
  $("videoPreview").classList.toggle("hidden", !final)
  if (final) {
    $("finalVideo").src = `/api/tasks/${task.id}/artifacts/final-video`
  }
}

async function runStep(taskId, step) {
  await api(`/api/tasks/${taskId}/run`, {
    method: "POST",
    body: JSON.stringify({step}),
  })
  await refreshTasks()
}

async function runAll() {
  if (!state.selectedId) return
  await api(`/api/tasks/${state.selectedId}/run-all`, {method: "POST"})
  await refreshTasks()
}

async function deleteSelected() {
  if (!state.selectedId) return
  if (!window.confirm("删除任务记录？任务目录不会被删除。")) return
  await api(`/api/tasks/${state.selectedId}`, {method: "DELETE"})
  state.selectedId = null
  $("taskDetail").classList.add("hidden")
  $("emptyDetail").classList.remove("hidden")
  await refreshTasks()
}

function switchCreateTab(name) {
  document.querySelectorAll("[data-create-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.createTab === name)
  })
  $("urlForm").classList.toggle("hidden", name !== "url")
  $("localForm").classList.toggle("hidden", name !== "local")
  $("uploadForm").classList.toggle("hidden", name !== "upload")
}

async function submitUrl(event) {
  event.preventDefault()
  setMessage("createMessage", "")
  try {
    const task = await api("/api/tasks/url", {
      method: "POST",
      body: JSON.stringify({
        url: $("urlInput").value,
        use_cookies: $("urlUseCookies").checked,
        force_download: $("urlForce").checked,
      }),
    })
    $("urlInput").value = ""
    setMessage("createMessage", "任务已创建")
    await refreshTasks()
    selectTask(task.id)
  } catch (error) {
    setMessage("createMessage", error.message, true)
  }
}

async function submitLocal(event) {
  event.preventDefault()
  setMessage("createMessage", "")
  try {
    const task = await api("/api/tasks/local", {
      method: "POST",
      body: JSON.stringify({
        source: $("localSourceInput").value,
        title: $("localTitleInput").value,
      }),
    })
    setMessage("createMessage", "任务已创建")
    await refreshTasks()
    selectTask(task.id)
  } catch (error) {
    setMessage("createMessage", error.message, true)
  }
}

async function submitUpload(event) {
  event.preventDefault()
  setMessage("createMessage", "")
  const file = $("uploadFileInput").files[0]
  if (!file) {
    setMessage("createMessage", "请选择视频文件", true)
    return
  }
  const form = new FormData()
  form.append("file", file)
  form.append("title", $("uploadTitleInput").value)
  try {
    const task = await api("/api/tasks/upload", {method: "POST", body: form})
    $("uploadFileInput").value = ""
    setMessage("createMessage", "任务已创建")
    await refreshTasks()
    selectTask(task.id)
  } catch (error) {
    setMessage("createMessage", error.message, true)
  }
}

function openSettings() {
  $("settingsDrawer").classList.add("open")
  $("settingsDrawer").setAttribute("aria-hidden", "false")
  loadSettings()
}

function closeSettings() {
  $("settingsDrawer").classList.remove("open")
  $("settingsDrawer").setAttribute("aria-hidden", "true")
}

async function loadSettings() {
  setMessage("settingsMessage", "")
  const [cookies, openai, ytdlp] = await Promise.all([
    api("/api/settings/cookies"),
    api("/api/settings/openai"),
    api("/api/settings/ytdlp"),
  ])
  $("cookiesInput").value = ""
  $("cookiesInput").placeholder = cookies.exists ? "已保存 cookies；重新粘贴会覆盖，留空保存会保留现有内容。" : "粘贴 Netscape 格式 cookies.txt 内容。"
  $("cookieInfo").textContent = formatCookieInfo(cookies)
  $("openaiBaseInput").value = openai.base_url || ""
  $("openaiKeyInput").value = openai.has_api_key ? "********" : ""
  $("openaiModelInput").value = openai.model || ""
  $("proxyInput").value = ytdlp.proxy || ""
  $("maxHeightInput").value = Number.isFinite(Number(ytdlp.max_height)) ? String(ytdlp.max_height) : "0"
}

async function saveSettings(event) {
  event.preventDefault()
  setMessage("settingsMessage", "")
  try {
    await api("/api/settings/cookies", {
      method: "POST",
      body: JSON.stringify({content: $("cookiesInput").value || null, clear: false}),
    })
    await api("/api/settings/openai", {
      method: "POST",
      body: JSON.stringify({
        base_url: $("openaiBaseInput").value,
        api_key: $("openaiKeyInput").value,
        model: $("openaiModelInput").value,
      }),
    })
    await api("/api/settings/ytdlp", {
      method: "POST",
      body: JSON.stringify({
        proxy: $("proxyInput").value,
        max_height: Number($("maxHeightInput").value || 0),
      }),
    })
    setMessage("settingsMessage", "已保存")
    await refreshDoctor()
    await loadSettings()
  } catch (error) {
    setMessage("settingsMessage", error.message, true)
  }
}

function formatCookieInfo(cookies) {
  if (!cookies.exists) return `未保存 cookies · ${cookies.path || "未配置路径"}`
  const names = cookies.cookie_names || []
  const domains = cookies.cookie_domains || []
  const loginNames = names.filter((name) => ["SID", "HSID", "SSID", "APISID", "SAPISID", "LOGIN_INFO"].includes(name) || name.includes("PSID"))
  const quality = cookies.cookies_look_valid ? `${cookies.cookie_count || 0} 条` : "格式未识别"
  const login = loginNames.length ? `登录项 ${loginNames.join(", ")}` : "未检测到常见登录项"
  const domain = domains.length ? domains.join(", ") : "无域名"
  return `已保存 ${fmtSize(cookies.size)} · ${quality} · ${domain} · ${login} · ${cookies.path}`
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[char]))
}

function bindEvents() {
  $("refreshButton").addEventListener("click", () => {
    refreshDoctor()
    refreshTasks()
  })
  $("settingsButton").addEventListener("click", openSettings)
  document.querySelectorAll("[data-close-settings]").forEach((node) => node.addEventListener("click", closeSettings))
  document.querySelectorAll("[data-create-tab]").forEach((button) => {
    button.addEventListener("click", () => switchCreateTab(button.dataset.createTab))
  })
  $("urlForm").addEventListener("submit", submitUrl)
  $("localForm").addEventListener("submit", submitLocal)
  $("uploadForm").addEventListener("submit", submitUpload)
  $("settingsForm").addEventListener("submit", saveSettings)
  $("runAllButton").addEventListener("click", () => runAll().catch((error) => window.alert(error.message)))
  $("deleteButton").addEventListener("click", () => deleteSelected().catch((error) => window.alert(error.message)))
}

bindEvents()
refreshDoctor()
refreshTasks()
window.setInterval(() => {
  refreshTasks().catch(() => undefined)
}, 2500)
