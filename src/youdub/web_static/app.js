const STEPS = [
  ["extract-audio", "提取音频"],
  ["separate-audio", "人声分离"],
  ["transcribe", "识别"],
  ["translate", "翻译"],
  ["tts", "配音"],
  ["transcribe-tts", "配音识别"],
  ["subtitle", "字幕"],
  ["inspect-tts", "配音质检"],
  ["redub-tts", "局部重配"],
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
      ["cookies_content", "Cookies 内容（可选，仅保存时写入文件）", "textarea", {transient: true}],
      ["proxy", "yt-dlp 代理", "text"],
      ["max_height", "最大下载高度", "integer", {min: 0}],
      ["force_download", "重新下载", "boolean"],
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
      ["extra_prompt", "全局额外提示词", "textarea"],
      ["summary_extra_prompt", "摘要提示词", "textarea"],
      ["context_extra_prompt", "上下文提示词", "textarea"],
      ["segment_extra_prompt", "分段翻译提示词", "textarea"],
      ["correction_prompt", "纠错和术语提示词", "textarea"],
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
    key: "tts_quality",
    label: "配音质检",
    fields: [
      ["hard_similarity_min", "严重相似度阈值", "number", {min: 0, max: 1, step: 0.01}],
      ["review_similarity_min", "复核相似度阈值", "number", {min: 0, max: 1, step: 0.01}],
      ["hard_alignment_confidence_min", "严重对齐置信度", "number", {min: 0, max: 1, step: 0.01}],
      ["review_alignment_confidence_min", "复核对齐置信度", "number", {min: 0, max: 1, step: 0.01}],
      ["hard_drift_seconds", "严重漂移秒数", "number", {min: 0, step: 0.1}],
      ["review_drift_seconds", "复核漂移秒数", "number", {min: 0, step: 0.1}],
      ["extreme_stretch_min", "极限拉伸下限", "number", {step: 0.01}],
      ["extreme_stretch_max", "极限拉伸上限", "number", {step: 0.01}],
      ["min_text_chars_for_empty_asr_hard", "空识别严重最小字数", "integer", {min: 1}],
      ["include_review", "重配包含复核片段", "boolean"],
      ["max_segments_per_round", "每轮最大重配片段", "integer", {min: 0}],
      ["max_task_hard_ratio", "任务复核严重比例", "number", {min: 0, max: 1, step: 0.01}],
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
  {
    key: "workflow",
    label: "流程",
    fields: [
      ["include_bilibili_upload", "完整链路包含 Bilibili", "boolean"],
      ["enable_tts_redub", "完整链路包含局部重配", "boolean"],
    ],
  },
]

const STEP_CONFIG_SECTIONS = {
  transcribe: "whisperx",
  translate: "translation",
  tts: "tts",
  "transcribe-tts": "whisperx",
  subtitle: "synthesis",
  "inspect-tts": "tts_quality",
  "redub-tts": "tts_quality",
  synthesize: "synthesis",
  "prepare-publish": "publish",
  "publish-bilibili": "bilibili",
}

const ACTIVE_POLL_INTERVAL_MS = 5000
const IDLE_POLL_INTERVAL_MS = 15000
const TASK_ROW_HEIGHT_PX = 66
const TASK_PAGE_SIZE_MIN = 4
const TASK_PAGE_SIZE_MAX = 50

const state = {
  tasks: [],
  selectedId: null,
  loading: false,
  tasksRefreshing: false,
  taskPage: {
    offset: 0,
    limit: 10,
    total: 0,
    hasMore: false,
  },
  defaultConfig: null,
  configTab: "download",
  configDirty: false,
  configTransient: {},
  configTaskId: null,
  configDraft: null,
  configDrawerOpen: false,
}

let taskPollTimer = null

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
    queued: "排队",
    running: "运行中",
    success: "成功",
    "pending-upload": "待上传",
    failed: "失败",
    skipped: "跳过",
  }[status] || status || "等待"
}

function statusClass(status) {
  if (status === "success") return "status-success"
  if (status === "failed") return "status-failed"
  if (status === "queued") return "status-queued"
  if (status === "running") return "status-running"
  return "status-pending"
}

function taskActive(task) {
  return Boolean(task?.queued || task?.running)
}

function effectiveTaskStatus(task) {
  if (task?.queued) return "queued"
  if (task?.running) return "running"
  return task?.display_status || task?.status
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
    updateTaskPageLimit()
    const query = new URLSearchParams({
      offset: String(state.taskPage.offset),
      limit: String(state.taskPage.limit),
    })
    let payload = await api(`/api/tasks?${query}`)
    if (Number(payload.total) > 0 && Number(payload.offset) >= Number(payload.total)) {
      const limit = Number(payload.limit) || state.taskPage.limit
      state.taskPage.offset = Math.floor((Number(payload.total) - 1) / limit) * limit
      const fallbackQuery = new URLSearchParams({
        offset: String(state.taskPage.offset),
        limit: String(limit),
      })
      payload = await api(`/api/tasks?${fallbackQuery}`)
    }
    state.tasks = payload.tasks
    state.taskPage = {
      offset: Number(payload.offset) || 0,
      limit: Number(payload.limit) || state.taskPage.limit,
      total: Number(payload.total) || 0,
      hasMore: Boolean(payload.has_more),
    }
    renderTasks()
    await refreshSelectedTask()
  } finally {
    state.tasksRefreshing = false
    renderTaskPager()
  }
}

async function refreshSelectedTask() {
  if (!state.selectedId) return
  try {
    const task = await api(`/api/tasks/${state.selectedId}`)
    renderDetail(task)
  } catch (error) {
    if (!/not found/i.test(error.message)) return
    state.selectedId = null
    state.configDirty = false
    state.configTransient = {}
    state.configTaskId = null
    state.configDraft = null
    closeTaskConfig()
    $("taskDetail").classList.add("hidden")
    $("emptyDetail").classList.remove("hidden")
  }
}

function updateTaskPageLimit() {
  const nextLimit = calculateTaskPageLimit()
  if (!nextLimit || nextLimit === state.taskPage.limit) return
  const currentIndex = state.taskPage.offset
  state.taskPage.limit = nextLimit
  state.taskPage.offset = Math.floor(currentIndex / nextLimit) * nextLimit
}

function calculateTaskPageLimit() {
  const panel = $("taskList")?.closest(".tasks-panel")
  const taskList = $("taskList")
  if (!panel || !taskList) return state.taskPage.limit
  const panelHeight = panel.getBoundingClientRect().height
  const headHeight = panel.querySelector(".panel-head")?.getBoundingClientRect().height || 0
  const pagerHeight = $("taskPager")?.getBoundingClientRect().height || 48
  const available = panelHeight - headHeight - pagerHeight
  if (!Number.isFinite(available) || available <= 0) return state.taskPage.limit
  return Math.min(TASK_PAGE_SIZE_MAX, Math.max(TASK_PAGE_SIZE_MIN, Math.floor(available / TASK_ROW_HEIGHT_PX)))
}

function renderTasks() {
  $("taskCount").textContent = String(state.taskPage.total)
  const list = $("taskList")
  list.innerHTML = ""
  if (!state.tasks.length) {
    const empty = document.createElement("div")
    empty.className = "empty-state"
    empty.innerHTML = state.taskPage.total ? "<p>当前页暂无任务</p>" : "<p>暂无任务</p>"
    list.appendChild(empty)
    renderTaskPager()
    return
  }
  for (const task of state.tasks) {
    const effectiveStatus = effectiveTaskStatus(task)
    const item = document.createElement("button")
    item.type = "button"
    item.className = `task-item ${task.id === state.selectedId ? "active" : ""}`
    item.innerHTML = `
      <span class="task-title">${escapeHtml(task.title || task.source || task.id)}</span>
      <span class="task-meta">
        <span class="status-badge ${statusClass(effectiveStatus)}">${statusLabel(effectiveStatus)}</span>
        <span>${fmtTime(task.updated_at)}</span>
      </span>
    `
    item.addEventListener("click", () => selectTask(task.id))
    list.appendChild(item)
  }
  renderTaskPager()
}

function renderTaskPager() {
  const total = state.taskPage.total
  const start = total ? state.taskPage.offset + 1 : 0
  const end = Math.min(total, state.taskPage.offset + state.tasks.length)
  $("taskPageInfo").textContent = total ? `${start}-${end} / ${total}` : "0 / 0"
  $("prevTaskPageButton").disabled = state.taskPage.offset <= 0 || state.tasksRefreshing
  $("nextTaskPageButton").disabled = !state.taskPage.hasMore || state.tasksRefreshing
}

function changeTaskPage(direction) {
  const nextOffset = state.taskPage.offset + direction * state.taskPage.limit
  state.taskPage.offset = Math.max(0, nextOffset)
  refreshTasks().catch((error) => {
    $("runtimeLine").textContent = error.message
  })
}

async function selectTask(taskId) {
  state.selectedId = taskId
  state.configDirty = false
  state.configTransient = {}
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
  const effectiveStatus = effectiveTaskStatus(task)
  $("detailStatus").className = `status-badge ${statusClass(effectiveStatus)}`
  $("detailStatus").textContent = statusLabel(effectiveStatus)
  $("detailId").textContent = task.id
  $("detailAuthor").textContent = task.author || "-"
  $("detailFolder").textContent = task.folder
  $("detailUpdated").textContent = fmtTime(task.updated_at)
  $("detailError").classList.toggle("hidden", !task.error)
  $("detailError").textContent = task.error || ""
  const hasDownload = taskHasArtifact(task, "download-video")
  $("runAllButton").disabled = taskActive(task) || (!hasDownload && !isUrlSource(task.source))
  $("workflowConfigButton").disabled = taskActive(task)
  $("workflowConfigButton").onclick = () => openTaskConfig(task, "workflow")
  $("deleteButton").disabled = taskActive(task)
  $("saveTaskConfigButton").disabled = taskActive(task)
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
  const hasDownload = taskHasArtifact(task, "download-video")
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
          ${renderStepProgress(task, step)}
        </div>
        ${configKey ? `<button class="icon-button step-config-button" type="button" title="${label}参数" aria-label="${label}参数">⚙</button>` : ""}
      </div>
      <div class="step-card-actions">
        <button class="button secondary" type="button" ${taskActive(task) || !hasDownload ? "disabled" : ""}>${buttonLabel}</button>
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
    card.querySelector(".step-card-actions button").addEventListener("click", () => runStep(task, step))
    grid.appendChild(card)
  }
}

function renderDownloadConfigCard(task) {
  const hasDownload = taskHasArtifact(task, "download-video")
  const canDownload = isUrlSource(task.source)
  const status = task.queued && !hasDownload ? "queued" : task.running && !hasDownload ? "running" : hasDownload ? "success" : "pending"
  const buttonLabel = hasDownload ? "重新下载" : "下载"
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
    ${canDownload ? `
      <div class="step-card-actions">
        <button class="button secondary download-task-button" type="button" ${taskActive(task) ? "disabled" : ""}>${buttonLabel}</button>
      </div>
    ` : ""}
  `
  card.querySelector(".step-config-button").addEventListener("click", (event) => {
    event.stopPropagation()
    openTaskConfig(task, "download")
  })
  card.addEventListener("click", (event) => {
    if (event.target.closest("button")) return
    openTaskConfig(task, "download")
  })
  const downloadButton = card.querySelector(".download-task-button")
  if (downloadButton) {
    downloadButton.addEventListener("click", (event) => {
      event.stopPropagation()
      downloadTask(task).catch((error) => window.alert(error.message))
    })
  }
  return card
}

function renderStepProgress(task, step) {
  const completion = task.step_completion?.[step]
  if (!completion?.show_progress) return ""
  const completed = Number(completion.completed)
  const total = Number(completion.total)
  const percent = clampPercent(Number(completion.percent))
  if (!Number.isFinite(completed) || !Number.isFinite(total) || total <= 1) return ""
  const label = `${Math.max(0, completed)}/${total} ${progressUnitLabel(completion.unit)}`
  return `
    <div class="step-progress" aria-label="完成进度 ${percent}%">
      <span class="step-progress-fill" style="width: ${percent}%"></span>
    </div>
    <div class="step-progress-meta">
      <span>${percent}%</span>
      <span>${label}</span>
    </div>
  `
}

function progressUnitLabel(unit) {
  return {
    artifact: "产物",
    segment: "片段",
  }[unit] || escapeHtml(String(unit || "产物"))
}

function clampPercent(value) {
  if (!Number.isFinite(value)) return 0
  return Math.min(100, Math.max(0, Math.round(value)))
}

function taskHasArtifact(task, artifactKey) {
  return (task.artifacts || []).some((item) => item.key === artifactKey)
}

function isUrlSource(value) {
  return /^https?:\/\//i.test(String(value || ""))
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
  $("saveTaskConfigButton").disabled = taskActive(task)

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
    const value = meta.transient ? transientConfigValue(activeSection.key, key) : values[key]
    fields.appendChild(renderConfigField(activeSection.key, key, label, type, meta, value))
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
    state.configTransient = {}
  }
  return state.configDraft
}

function renderConfigField(section, key, labelText, type, meta, value) {
  const label = document.createElement("label")
  label.className = type === "boolean"
    ? "checkbox-line config-checkbox"
    : type === "textarea"
      ? "config-textarea-field"
      : ""
  const id = configInputId(section, key)

  if (type === "boolean") {
    label.innerHTML = `
      <input id="${id}" data-config-section="${section}" data-config-key="${key}" data-config-type="${type}"${transientDataAttribute(meta)} type="checkbox" ${value ? "checked" : ""} />
      <span>${labelText}</span>
    `
  } else if (type === "select") {
    const options = (meta.options || []).map((item) => {
      const selected = String(value || "") === item ? "selected" : ""
      return `<option value="${escapeHtml(item)}" ${selected}>${escapeHtml(item)}</option>`
    }).join("")
    label.innerHTML = `
      <span>${labelText}</span>
      <select id="${id}" data-config-section="${section}" data-config-key="${key}" data-config-type="${type}"${transientDataAttribute(meta)}>${options}</select>
    `
  } else if (type === "textarea") {
    label.innerHTML = `
      <span>${labelText}</span>
      <textarea id="${id}" data-config-section="${section}" data-config-key="${key}" data-config-type="${type}"${transientDataAttribute(meta)} spellcheck="false">${escapeHtml(value ?? "")}</textarea>
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
      <input id="${id}" data-config-section="${section}" data-config-key="${key}" data-config-type="${type}"${transientDataAttribute(meta)} type="${inputType}" ${attrs} value="${escapeHtml(value ?? "")}" />
    `
  }

  label.querySelector("input, select, textarea").addEventListener("input", (event) => {
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
  const transient = input.dataset.configTransient === "true"
  if (!section || !key || !type) return
  if (transient) {
    state.configTransient[section] = state.configTransient[section] || {}
    state.configTransient[section][key] = input.value
    return
  }
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

function transientConfigValue(section, key) {
  return state.configTransient?.[section]?.[key] || ""
}

function transientDataAttribute(meta) {
  return meta.transient ? ' data-config-transient="true"' : ""
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
    const cookiesContent = transientConfigValue("download", "cookies_content").trim()
    if (cookiesContent) {
      await api(`/api/tasks/${state.selectedId}/download-cookies`, {
        method: "POST",
        body: JSON.stringify({content: cookiesContent}),
      })
    }
    state.configDirty = false
    state.configTransient = {}
    state.configDraft = cloneConfig(payload.config)
    state.configTaskId = state.selectedId
    const task = await api(`/api/tasks/${state.selectedId}`)
    renderDetail(task, {forceConfig: true})
    setMessage("taskConfigMessage", "已保存")
  } catch (error) {
    setMessage("taskConfigMessage", error.message, true)
  }
}

async function runStep(task, step) {
  const force = await confirmRerunIfCompleted(task, step)
  if (force === null) return
  await api(`/api/tasks/${task.id}/run`, {
    method: "POST",
    body: JSON.stringify({step, force}),
  })
  await refreshTasks()
}

async function runAll() {
  if (!state.selectedId) return
  await api(`/api/tasks/${state.selectedId}/run-all`, {method: "POST"})
  await refreshTasks()
}

async function downloadTask(task) {
  const force = await confirmRerunIfCompleted(task, "ingest")
  if (force === null) return
  await api(`/api/tasks/${task.id}/download`, {
    method: "POST",
    body: JSON.stringify({force}),
  })
  await refreshTasks()
}

async function confirmRerunIfCompleted(task, step) {
  const completion = task.step_completion?.[step]
  if (!completion?.complete) return false
  const label = stepLabel(step)
  const ok = window.confirm(`${label}已完成。重新运行会清理该步骤及后续步骤产物，是否继续？`)
  return ok ? true : null
}

function stepLabel(step) {
  if (step === "ingest") return "下载"
  const item = STEPS.find(([key]) => key === step)
  return item ? item[1] : step
}

async function deleteSelected() {
  if (!state.selectedId) return
  if (!window.confirm("删除任务记录？任务目录不会被删除。")) return
  await api(`/api/tasks/${state.selectedId}`, {method: "DELETE"})
  state.selectedId = null
  state.configDirty = false
  state.configTransient = {}
  state.configTaskId = null
  state.configDraft = null
  closeTaskConfig()
  $("taskDetail").classList.add("hidden")
  $("emptyDetail").classList.remove("hidden")
  await refreshTasks()
}

async function submitUrl(event) {
  event.preventDefault()
  setMessage("createMessage", "")
  try {
    const task = await api("/api/tasks/url-draft", {
      method: "POST",
      body: JSON.stringify({
        url: $("urlInput").value,
      }),
    })
    $("urlInput").value = ""
    setMessage("createMessage", "任务已创建")
    closeCreateDialog()
    state.taskPage.offset = 0
    await refreshTasks()
    selectTask(task.id)
  } catch (error) {
    setMessage("createMessage", error.message, true)
  }
}

function openCreateDialog() {
  setMessage("createMessage", "")
  $("createDialog").classList.add("open")
  $("createDialog").setAttribute("aria-hidden", "false")
  window.setTimeout(() => $("urlInput").focus(), 0)
}

function closeCreateDialog() {
  $("createDialog").classList.remove("open")
  $("createDialog").setAttribute("aria-hidden", "true")
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
  $("prevTaskPageButton").addEventListener("click", () => changeTaskPage(-1))
  $("nextTaskPageButton").addEventListener("click", () => changeTaskPage(1))
  $("newTaskButton").addEventListener("click", openCreateDialog)
  document.querySelectorAll("[data-close-create]").forEach((node) => node.addEventListener("click", closeCreateDialog))
  $("urlForm").addEventListener("submit", submitUrl)
  $("taskConfigForm").addEventListener("submit", saveTaskConfig)
  document.querySelectorAll("[data-close-config]").forEach((node) => node.addEventListener("click", closeTaskConfig))
  $("runAllButton").addEventListener("click", () => runAll().catch((error) => window.alert(error.message)))
  $("deleteButton").addEventListener("click", () => deleteSelected().catch((error) => window.alert(error.message)))
  window.addEventListener("resize", debounce(() => {
    const previousLimit = state.taskPage.limit
    updateTaskPageLimit()
    if (state.taskPage.limit !== previousLimit) refreshTasks().catch(() => undefined)
  }, 150))
}

function debounce(callback, delay) {
  let timeout = null
  return (...args) => {
    if (timeout !== null) window.clearTimeout(timeout)
    timeout = window.setTimeout(() => callback(...args), delay)
  }
}

function scheduleTaskPolling() {
  if (taskPollTimer !== null) window.clearTimeout(taskPollTimer)
  const delay = state.tasks.some(taskActive) ? ACTIVE_POLL_INTERVAL_MS : IDLE_POLL_INTERVAL_MS
  taskPollTimer = window.setTimeout(async () => {
    if (!document.hidden) {
      await refreshTasks().catch(() => undefined)
    }
    scheduleTaskPolling()
  }, delay)
}

bindEvents()
Promise.all([loadDefaultConfig(), refreshDoctor(), refreshTasks()]).catch((error) => {
  $("runtimeLine").textContent = error.message
}).finally(() => {
  scheduleTaskPolling()
})

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refreshTasks().catch(() => undefined)
})
