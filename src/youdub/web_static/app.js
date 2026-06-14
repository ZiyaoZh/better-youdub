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
  ["publish-bilibili", "Bilibili"],
]

const CONFIG_SECTIONS = [
  {
    key: "download",
    label: "下载",
    fields: [
      ["use_cookies", "使用 cookies", "boolean"],
      ["cookies_path", "Cookies 文件路径", "text"],
      ["proxy", "yt-dlp 代理", "text"],
      ["max_height", "最大下载高度", "integer", {min: 0}],
      ["force_download", "重新下载", "boolean"],
      ["auto_run_all_after_download", "下载完成自动运行全流程", "boolean"],
    ],
  },
  {
    key: "whisperx",
    label: "识别",
    fields: [
      ["model_name", "Whisper 模型", "text"],
      ["device", "设备", "select", {options: ["auto", "cuda", "cpu"]}],
      ["batch_size", "批大小", "integer", {min: 1}],
      ["language", "源语言", "text"],
      ["initial_prompt", "初始提示词", "text"],
      ["diarization", "说话人分离", "boolean"],
      ["min_speakers", "最小说话人数", "integer", {min: 0}],
      ["max_speakers", "最大说话人数", "integer", {min: 0}],
      ["hf_token", "Hugging Face Token", "secret"],
      ["tts_asr_language", "配音识别语言", "text"],
      ["tts_asr_initial_prompt", "配音识别提示词", "text"],
    ],
  },
  {
    key: "translation",
    label: "翻译",
    fields: [
      ["api_key", "OpenAI API Key", "secret"],
      ["base_url", "OpenAI Base URL", "text"],
      ["model", "模型", "text"],
      ["target_language", "目标语言", "text"],
      ["batch_size", "批大小", "integer", {min: 1}],
      ["timeout_seconds", "超时秒数", "number", {min: 1}],
      ["max_retries", "最大重试", "integer", {min: 1}],
      ["retry_backoff_seconds", "初始退避秒数", "number", {min: 0}],
      ["retry_backoff_multiplier", "退避倍数", "number", {min: 1}],
      ["retry_max_backoff_seconds", "最大退避秒数", "number", {min: 0}],
      ["force_json_output", "强制 JSON 输出", "boolean"],
      ["temperature", "Temperature", "number", {min: 0, step: 0.1}],
    ],
  },
  {
    key: "tts",
    label: "配音",
    fields: [
      ["model", "TTS 模型", "text"],
      ["model_dir", "本地模型目录", "text"],
      ["hf_token", "Hugging Face Token", "secret"],
      ["load_denoiser", "加载降噪器", "boolean"],
      ["cfg_value", "CFG", "number", {min: 0, step: 0.1}],
      ["inference_timesteps", "推理步数", "integer", {min: 1}],
      ["min_reference_ms", "最小参考音频 ms", "integer", {min: 0}],
      ["start_pad_ms", "参考前填充 ms", "integer", {min: 0}],
      ["end_pad_ms", "参考后填充 ms", "integer", {min: 0}],
      ["align_audio", "配音时长对齐", "boolean"],
      ["stretch_base_min", "全局拉伸下限", "number", {step: 0.01}],
      ["stretch_base_max", "全局拉伸上限", "number", {step: 0.01}],
      ["stretch_base_safety", "全局拉伸安全系数", "number", {step: 0.01}],
      ["stretch_local_min", "局部拉伸下限", "number", {step: 0.01}],
      ["stretch_local_max", "局部拉伸上限", "number", {step: 0.01}],
      ["stretch_noop_epsilon", "拉伸忽略阈值", "number", {step: 0.001}],
    ],
  },
  {
    key: "synthesis",
    label: "合成",
    fields: [
      ["burn_subtitles", "烧录字幕", "boolean"],
      ["tts_volume", "配音音量", "number", {min: 0, step: 0.05}],
      ["instruments_volume", "背景音量", "number", {min: 0, step: 0.05}],
      ["video_preset", "视频 preset", "text"],
      ["video_crf", "视频 CRF", "integer", {min: 0, max: 51}],
      ["audio_bitrate", "音频码率", "text"],
      ["subtitle_language", "字幕语言样式", "select", {options: ["zh", "en"]}],
      ["subtitle_font", "字幕字体", "text"],
    ],
  },
  {
    key: "publish",
    label: "发布包",
    fields: [
      ["max_title_chars", "标题最大字符", "integer", {min: 1}],
      ["max_tags", "标签数量", "integer", {min: 1}],
      ["max_tag_chars", "单个标签字符", "integer", {min: 1}],
    ],
  },
  {
    key: "bilibili",
    label: "Bilibili",
    fields: [
      ["sessdata", "SESSDATA", "secret"],
      ["bili_jct", "BILI_JCT", "secret"],
      ["tid", "分区 ID", "integer", {min: 1}],
      ["original", "原创", "boolean"],
      ["source", "转载来源", "text"],
      ["watermark", "水印", "boolean"],
      ["dry_run", "Dry run", "boolean"],
      ["force", "强制重新上传", "boolean"],
      ["confirm", "确认真实上传", "boolean"],
    ],
  },
]

const STEP_CONFIG_SECTIONS = {
  transcribe: "whisperx",
  translate: "translation",
  tts: "tts",
  "transcribe-tts": "whisperx",
  subtitle: "synthesis",
  synthesize: "synthesis",
  "prepare-publish": "publish",
  "publish-bilibili": "bilibili",
}

const FINAL_VIDEO_KEY = "final-video"
const POLL_INTERVAL_MS = 2500

const state = {
  tasks: [],
  selectedId: null,
  loading: false,
  tasksRefreshing: false,
  defaultConfig: null,
  configTab: "download",
  configDirty: false,
  configTaskId: null,
  configDraft: null,
  configDrawerOpen: false,
  videoPreview: {
    taskId: null,
    signature: "",
    url: "",
    open: false,
  },
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
  if (!node) return
  node.textContent = text || ""
  node.style.color = isError ? "var(--danger)" : "var(--muted)"
}

async function loadDefaultConfig() {
  const payload = await api("/api/task-config/defaults")
  state.defaultConfig = payload.config
  fillUrlDownloadDefaults()
}

function fillUrlDownloadDefaults() {
  const download = state.defaultConfig?.download || {}
  if ($("urlCookiesPathInput")) $("urlCookiesPathInput").value = download.cookies_path || ""
  if ($("urlProxyInput")) $("urlProxyInput").value = download.proxy || ""
  if ($("urlMaxHeightInput")) $("urlMaxHeightInput").value = String(download.max_height ?? 0)
  if ($("urlUseCookies")) $("urlUseCookies").checked = download.use_cookies !== false
  if ($("urlAutoRunAllAfterDownload")) {
    $("urlAutoRunAllAfterDownload").checked = Boolean(download.auto_run_all_after_download)
  }
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
  if (state.tasksRefreshing) return
  state.tasksRefreshing = true
  try {
    const payload = await api("/api/tasks")
    state.tasks = payload.tasks
    renderTasks()
    if (state.selectedId) {
      const selected = state.tasks.find((task) => task.id === state.selectedId)
      if (selected) renderDetail(selected)
    }
  } finally {
    state.tasksRefreshing = false
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
  state.configDirty = false
  state.configTaskId = null
  state.configDraft = null
  const task = await api(`/api/tasks/${taskId}`)
  renderTasks()
  renderDetail(task, {forceConfig: true})
}

function renderDetail(task, options = {}) {
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
  $("saveTaskConfigButton").disabled = task.running
  renderSteps(task)
  renderArtifacts(task)
  const editingConfig = $("taskConfigForm").contains(document.activeElement)
  if (options.forceConfig || (!state.configDirty && !editingConfig)) {
    renderTaskConfig(task, {forceDraft: true})
  }
}

function renderSteps(task) {
  const grid = $("stepsGrid")
  grid.innerHTML = ""
  grid.appendChild(renderDownloadConfigCard(task))
  for (const [step, label] of STEPS) {
    const status = task.steps?.[step] || "pending"
    const configKey = STEP_CONFIG_SECTIONS[step]
    const card = document.createElement("div")
    card.className = `step-card${configKey ? " has-config" : ""}`
    const bilibiliConfig = task.config?.bilibili || {}
    const realBilibiliUpload = step === "publish-bilibili" && bilibiliConfig.confirm && !bilibiliConfig.dry_run
    const buttonLabel = step === "publish-bilibili" ? (realBilibiliUpload ? "上传" : "dry-run") : "运行"
    card.innerHTML = `
      <div class="step-card-head">
        <div>
          <h3>${label}</h3>
          <span class="status-badge ${statusClass(status)}">${statusLabel(status)}</span>
        </div>
        ${configKey ? `<button class="icon-button step-config-button" type="button" title="${label}参数" aria-label="${label}参数">⚙</button>` : ""}
      </div>
      <div class="step-card-actions">
        <button class="button secondary" type="button" ${task.running ? "disabled" : ""}>${buttonLabel}</button>
      </div>
    `
    const configButton = card.querySelector(".step-config-button")
    if (configButton) {
      configButton.addEventListener("click", (event) => {
        event.stopPropagation()
        openTaskConfig(task, configKey)
      })
      card.addEventListener("click", (event) => {
        if (event.target.closest("button")) return
        openTaskConfig(task, configKey)
      })
    }
    card.querySelector(".step-card-actions button").addEventListener("click", () => runStep(task.id, step))
    grid.appendChild(card)
  }
}

function renderDownloadConfigCard(task) {
  const hasDownload = (task.artifacts || []).some((item) => item.key === "download-video")
  const status = hasDownload ? "success" : "pending"
  const card = document.createElement("div")
  card.className = "step-card step-card-config-only has-config"
  card.innerHTML = `
    <div class="step-card-head">
      <div>
        <h3>下载</h3>
        <span class="status-badge ${statusClass(status)}">${statusLabel(status)}</span>
      </div>
      <button class="icon-button step-config-button" type="button" title="下载参数" aria-label="下载参数">⚙</button>
    </div>
  `
  card.querySelector(".step-config-button").addEventListener("click", (event) => {
    event.stopPropagation()
    openTaskConfig(task, "download")
  })
  card.addEventListener("click", (event) => {
    if (event.target.closest("button")) return
    openTaskConfig(task, "download")
  })
  return card
}

function artifactUrl(taskId, artifactKey, params = {}) {
  const query = new URLSearchParams(params)
  const suffix = query.toString() ? `?${query}` : ""
  return `/api/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(artifactKey)}${suffix}`
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
      link.href = artifactUrl(task.id, artifact.key, {download: "1"})
      link.textContent = `${artifact.name} · ${fmtSize(artifact.size)}`
      list.appendChild(link)
    }
  }
  const final = artifacts.find((item) => item.key === FINAL_VIDEO_KEY)
  renderVideoPreview(task, final)
}

function renderVideoPreview(task, artifact) {
  const panel = $("videoPreview")
  const video = $("finalVideo")
  const openButton = $("openVideoPreviewButton")
  const closeButton = $("closeVideoPreviewButton")

  panel.classList.toggle("hidden", !artifact)
  if (!artifact) {
    unloadVideoPreview()
    return
  }

  const signature = videoPreviewSignature(task, artifact)
  const previewOpen = state.videoPreview.open
    && state.videoPreview.taskId === task.id
    && state.videoPreview.signature === signature

  if (state.videoPreview.open && !previewOpen) {
    unloadVideoPreview()
  }

  video.classList.toggle("hidden", !previewOpen)
  openButton.classList.toggle("hidden", previewOpen)
  closeButton.classList.toggle("hidden", !previewOpen)
  openButton.onclick = () => openVideoPreview(task, artifact)
  closeButton.onclick = () => unloadVideoPreview()
}

function openVideoPreview(task, artifact) {
  const video = $("finalVideo")
  const signature = videoPreviewSignature(task, artifact)
  const url = artifactUrl(task.id, artifact.key, {v: String(artifact.size)})

  if (state.videoPreview.url !== url) {
    video.pause()
    video.src = url
    video.load()
  }

  state.videoPreview = {
    taskId: task.id,
    signature,
    url,
    open: true,
  }
  $("finalVideo").classList.remove("hidden")
  $("openVideoPreviewButton").classList.add("hidden")
  $("closeVideoPreviewButton").classList.remove("hidden")
}

function unloadVideoPreview() {
  const video = $("finalVideo")
  if (video) {
    video.pause()
    video.removeAttribute("src")
    video.load()
    video.classList.add("hidden")
  }
  state.videoPreview = {
    taskId: null,
    signature: "",
    url: "",
    open: false,
  }
  if ($("openVideoPreviewButton")) $("openVideoPreviewButton").classList.remove("hidden")
  if ($("closeVideoPreviewButton")) $("closeVideoPreviewButton").classList.add("hidden")
}

function videoPreviewSignature(task, artifact) {
  return `${task.id}:${artifact.key}:${artifact.size}:${artifact.name}`
}

function renderTaskConfig(task, options = {}) {
  const tabs = $("taskConfigTabs")
  const fields = $("taskConfigFields")
  const config = ensureTaskConfigDraft(task, options.forceDraft)
  if (!CONFIG_SECTIONS.some((section) => section.key === state.configTab)) {
    state.configTab = "download"
  }
  const activeSection = CONFIG_SECTIONS.find((item) => item.key === state.configTab) || CONFIG_SECTIONS[0]
  $("configDrawerTitle").textContent = `${activeSection.label}参数`
  $("saveTaskConfigButton").disabled = task.running

  tabs.innerHTML = ""
  for (const section of CONFIG_SECTIONS) {
    const button = document.createElement("button")
    button.className = `config-tab ${section.key === state.configTab ? "active" : ""}`
    button.type = "button"
    button.textContent = section.label
    button.addEventListener("click", () => {
      updateTaskConfigDraftFromForm()
      state.configTab = section.key
      renderTaskConfig(task)
    })
    tabs.appendChild(button)
  }

  const values = config[activeSection.key] || {}
  fields.innerHTML = ""
  for (const [key, label, type, meta = {}] of activeSection.fields) {
    fields.appendChild(renderConfigField(activeSection.key, key, label, type, meta, values[key]))
  }
  setMessage("taskConfigMessage", state.configDirty ? "有未保存修改" : "")
}

function openTaskConfig(task, sectionKey) {
  if (!CONFIG_SECTIONS.some((section) => section.key === sectionKey)) return
  if (state.configTaskId === task.id) {
    updateTaskConfigDraftFromForm()
  }
  const forceDraft = state.configTaskId !== task.id || !state.configDraft
  state.configTab = sectionKey
  state.configDrawerOpen = true
  $("configDrawer").classList.add("open")
  $("configDrawer").setAttribute("aria-hidden", "false")
  renderTaskConfig(task, {forceDraft})
}

function closeTaskConfig() {
  if (state.configDraft && state.configDrawerOpen) {
    updateTaskConfigDraftFromForm()
  }
  state.configDrawerOpen = false
  $("configDrawer").classList.remove("open")
  $("configDrawer").setAttribute("aria-hidden", "true")
}

function ensureTaskConfigDraft(task, force = false) {
  if (force || state.configTaskId !== task.id || !state.configDraft) {
    state.configTaskId = task.id
    state.configDraft = cloneConfig(task.config || state.defaultConfig || {})
  }
  return state.configDraft
}

function renderConfigField(section, key, labelText, type, meta, value) {
  const label = document.createElement("label")
  label.className = type === "boolean" ? "checkbox-line config-checkbox" : ""
  const id = configInputId(section, key)

  if (type === "boolean") {
    label.innerHTML = `
      <input id="${id}" data-config-section="${section}" data-config-key="${key}" data-config-type="${type}" type="checkbox" ${value ? "checked" : ""} />
      <span>${labelText}</span>
    `
  } else if (type === "select") {
    const options = (meta.options || []).map((item) => {
      const selected = String(value || "") === item ? "selected" : ""
      return `<option value="${escapeHtml(item)}" ${selected}>${escapeHtml(item)}</option>`
    }).join("")
    label.innerHTML = `
      <span>${labelText}</span>
      <select id="${id}" data-config-section="${section}" data-config-key="${key}" data-config-type="${type}">${options}</select>
    `
  } else {
    const inputType = type === "secret" ? "password" : type === "integer" || type === "number" ? "number" : "text"
    const attrs = [
      meta.min !== undefined ? `min="${meta.min}"` : "",
      meta.max !== undefined ? `max="${meta.max}"` : "",
      `step="${type === "integer" ? "1" : meta.step || "any"}"`,
    ].filter(Boolean).join(" ")
    label.innerHTML = `
      <span>${labelText}</span>
      <input id="${id}" data-config-section="${section}" data-config-key="${key}" data-config-type="${type}" type="${inputType}" ${attrs} value="${escapeHtml(value ?? "")}" />
    `
  }

  label.querySelector("input, select").addEventListener("input", (event) => {
    updateTaskConfigDraftValue(event.target)
    state.configDirty = true
    setMessage("taskConfigMessage", "有未保存修改")
  })
  return label
}

function updateTaskConfigDraftFromForm() {
  if (!state.configDraft) {
    state.configDraft = cloneConfig(state.defaultConfig || {})
  }
  document.querySelectorAll("[data-config-section][data-config-key]").forEach((input) => {
    updateTaskConfigDraftValue(input)
  })
}

function updateTaskConfigDraftValue(input) {
  const section = input.dataset.configSection
  const key = input.dataset.configKey
  const type = input.dataset.configType
  if (!section || !key || !type) return
  if (!state.configDraft) {
    state.configDraft = cloneConfig(state.defaultConfig || {})
  }
  state.configDraft[section] = state.configDraft[section] || {}
  if (type === "boolean") {
    state.configDraft[section][key] = input.checked
  } else if (type === "integer") {
    state.configDraft[section][key] = input.value === "" ? "" : parseInt(input.value, 10)
  } else if (type === "number") {
    state.configDraft[section][key] = input.value === "" ? "" : Number(input.value)
  } else {
    state.configDraft[section][key] = input.value
  }
}

function cloneConfig(config) {
  return JSON.parse(JSON.stringify(config || {}))
}

function configInputId(section, key) {
  return `config-${section}-${key}`
}

async function saveTaskConfig(event) {
  event.preventDefault()
  if (!state.selectedId) return
  setMessage("taskConfigMessage", "")
  updateTaskConfigDraftFromForm()
  try {
    const payload = await api(`/api/tasks/${state.selectedId}/config`, {
      method: "PUT",
      body: JSON.stringify({config: state.configDraft || {}}),
    })
    state.configDirty = false
    state.configDraft = cloneConfig(payload.config)
    state.configTaskId = state.selectedId
    const task = await api(`/api/tasks/${state.selectedId}`)
    task.config = payload.config
    renderDetail(task, {forceConfig: true})
    setMessage("taskConfigMessage", "已保存")
  } catch (error) {
    setMessage("taskConfigMessage", error.message, true)
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
  state.configDirty = false
  state.configTaskId = null
  state.configDraft = null
  closeTaskConfig()
  unloadVideoPreview()
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
    const autoRunAll = $("urlAutoRunAllAfterDownload").checked
    const task = await api("/api/tasks/url", {
      method: "POST",
      body: JSON.stringify({
        url: $("urlInput").value,
        use_cookies: $("urlUseCookies").checked,
        cookies_path: $("urlCookiesPathInput").value,
        cookies_content: $("urlCookiesContentInput").value,
        proxy: $("urlProxyInput").value,
        max_height: Number($("urlMaxHeightInput").value || 0),
        force_download: $("urlForce").checked,
        auto_run_all_after_download: autoRunAll,
      }),
    })
    $("urlInput").value = ""
    $("urlCookiesContentInput").value = ""
    setMessage("createMessage", autoRunAll ? "任务已创建，已开始运行完整链路" : "任务已创建")
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
  document.querySelectorAll("[data-create-tab]").forEach((button) => {
    button.addEventListener("click", () => switchCreateTab(button.dataset.createTab))
  })
  $("urlForm").addEventListener("submit", submitUrl)
  $("localForm").addEventListener("submit", submitLocal)
  $("uploadForm").addEventListener("submit", submitUpload)
  $("taskConfigForm").addEventListener("submit", saveTaskConfig)
  document.querySelectorAll("[data-close-config]").forEach((node) => node.addEventListener("click", closeTaskConfig))
  $("runAllButton").addEventListener("click", () => runAll().catch((error) => window.alert(error.message)))
  $("deleteButton").addEventListener("click", () => deleteSelected().catch((error) => window.alert(error.message)))
}

bindEvents()
Promise.all([loadDefaultConfig(), refreshDoctor(), refreshTasks()]).catch((error) => {
  $("runtimeLine").textContent = error.message
})
window.setInterval(() => {
  if (document.hidden) return
  refreshTasks().catch(() => undefined)
}, POLL_INTERVAL_MS)

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refreshTasks().catch(() => undefined)
})
