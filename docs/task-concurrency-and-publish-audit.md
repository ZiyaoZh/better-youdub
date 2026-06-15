# 任务并发、草稿清理、Bilibili 链路与运行状态核对

核对日期：2026-06-14

## 结论摘要

本次核对确认 4 个问题均有代码层面的依据，但状态不完全相同：

1. 多任务并发问题已确认。当前 Web 后台执行器是全局单 worker，不同任务会被串行排队；同时调度时提前持有任务锁，导致第二个任务在 UI 上显示运行中，但实际下载/处理尚未开始。
2. URL 草稿 `_pending` 清理问题已确认。草稿下载完成后会切换到正式任务目录，但旧 `_pending/<draft>` 目录没有删除。URL 草稿创建也缺少预下载去重逻辑，下载完成后还可能和已有稳定任务形成重复记录。
3. Bilibili 上传单步已接入，但未纳入 Web “运行完整链路”。`run-all` 当前只执行到 `prepare-publish`，不会执行 `publish-bilibili`。
4. 运行中状态不是完全缺失。模型、CSS 和前端标签已经有 `running`，但任务卡片存在展示和持久化缺口：状态主要依赖进程内 Future/目录锁推断，提交任务后不会立即持久化为 `running`；列表卡片的样式 class 仍使用原始 `task.status`，运行中时文字和样式可能不一致。

## 1. 多任务并发与任务锁

### 当前实现

- `src/youdub/web.py` 中 `_EXECUTOR = ThreadPoolExecutor(max_workers=1, ...)`，Web 所有下载、单步运行、完整链路运行共用一个后台 worker。
- 同一任务启动时会进入 `_RUNNING[task.id]`，后台 job 真正开始时才获取该任务目录下的 `.task.lock`。
- `TaskLock` 是目录级文件锁，锁文件位于任务目录 `.task.lock`，因此从锁粒度看它只约束同一任务目录。
- `README.md` 已声明 Web 后台执行器使用单线程 FIFO 队列，不同任务会排队执行，不并发执行。

### 问题判断

用户描述“一个任务在运行全流程时开始另一个任务，在下载阶段会显示运行中但没有结果”符合当前实现：

- 第二个任务被提交到 `_EXECUTOR` 后，`_RUNNING` 中有 Future，所以 API 返回 `running: true`。
- 因为 `max_workers=1`，如果第一个完整链路未结束，第二个任务的 job 不会开始执行。
- 调度阶段已经提前拿到了第二个任务自己的 `.task.lock`，所以 UI 会认为该任务已运行，但下载产物不会出现。

### 计划

1. 将 Web 后台执行器明确固定为单线程 FIFO 队列，所有下载、单步运行和完整链路按提交顺序执行。
2. 保留“同一任务不可重复启动”的现有设计：同一 task id 和同一任务目录仍通过 `_RUNNING[task.id]` 与 `.task.lock` 非阻塞互斥。
3. 调整调度时序，避免排队任务长期占用任务目录锁。建议：
   - 调度阶段只登记 Future，不长期持有 `.task.lock`；
   - job 真正开始执行时再获取任务锁；
   - API 增加 `queued` 状态，避免“排队中”伪装为“下载中”。
4. 为多任务并发补测试：
   - 两个不同 task id 会按提交顺序串行执行；
   - 同一个 task id 第二次启动仍返回 409；
   - 一个任务运行完整链路时，另一个任务下载不会被全局 worker 阻塞。

## 2. 空白草稿 `_pending` 清理与 URL 去重

### 当前实现

- `create_pending_url_task()` 会为 URL 草稿创建 `YOUDUB_ROOT/_pending/<task_id>_URL draft`。
- Web 的 `/api/tasks/url-draft` 每次都会直接创建新草稿并 `_store().add(task)`，没有查询已有任务。
- 草稿下载完成后，`_downloaded_task_payload()` 会把任务 folder 替换为下载信息对应的稳定目录，但没有删除旧的 `_pending` 目录。
- `create_task_from_download_artifacts()` 对已下载元信息使用 `stable_task_id(source_key)`，CLI 的 `create-url-task` 和 `create-download-task` 通过 `TaskStore.upsert()` 能复用稳定 task id。

### 问题判断

已确认 `_pending` 不清理。草稿下载成功后，旧草稿目录不会再被任务引用，也没有清理逻辑。

已确认 URL 草稿缺少去重。由于草稿阶段没有 `download.info.json`，当前无法得到 extractor/id 级别的 `source_key`，但至少可以按规范化 URL 做预去重，避免同一个 URL 多次创建空白草稿。

还存在一个更高风险的重复记录场景：

- 如果已有稳定任务 `id = stable_task_id(source_key)`；
- 用户又为同一 URL 创建草稿，草稿 id 是随机 UUID；
- 草稿下载完成后 `_downloaded_task_payload()` 保留草稿 id，但 folder/source_key 指向稳定下载目录；
- 结果可能出现两个 tasks.json 记录指向同一个正式目录和同一个 source_key。

### 计划

1. 为 URL 草稿添加去重查询：
   - 首先按规范化 URL 查找 `task.source`；
   - 对已有 pending 草稿直接返回已有任务；
   - 对已有稳定任务也直接返回已有任务，而不是再创建草稿。
2. 下载完成合并时处理稳定任务冲突：
   - 如果 `incoming.source_key` 已存在于 store，并且不是当前草稿 id，合并当前草稿配置到已有稳定任务；
   - 从 `tasks.json` 删除当前草稿记录；
   - 返回已有稳定任务。
3. 草稿下载成功后清理旧 `_pending/<draft>` 目录：
   - 只删除位于 `config.root / "_pending"` 下的当前草稿目录；
   - 确认不删除正式目录；
   - 使用 `shutil.rmtree(..., ignore_errors=True)` 或受控错误处理。
4. 补测试：
   - 重复 URL 草稿创建返回同一 task id；
   - 草稿下载成功后 `_pending` 草稿目录被删除；
   - 草稿下载命中已有 source_key 时不生成重复 tasks.json 记录；
   - 已有正式任务再次输入同 URL 时复用已有任务。

## 3. Bilibili 上传是否纳入完整链路

### 当前实现

- `PipelineRunner.run_step()` 已支持 `PipelineStep.PUBLISH_BILIBILI`，调用 `publish_to_bilibili()`。
- Web 单步运行也支持 `publish-bilibili`，并且未确认真实上传时会回退 dry-run；任务配置里 `dry_run=false` 且 `confirm=true` 时会使用真实上传配置。
- 前端 `STEPS` 包含 `publish-bilibili`，单步按钮会根据配置显示 `上传` 或 `dry-run`。
- 但 `_run_all_job()` 的步骤列表只到 `PipelineStep.PREPARE_PUBLISH`，没有 `PipelineStep.PUBLISH_BILIBILI`。

### 问题判断

用户描述成立：真实上传 Bilibili 没有纳入 Web “运行完整链路”。当前完整链路只是生成发布包，不会自动执行 Bilibili 上传。

这可能是有意的安全设计，因为真实上传需要凭证和显式确认。但目前 UI/链路没有清晰表达“完整链路是否包含上传”，容易让用户误以为一键完整链路会上传。

### 计划

1. 明确产品规则：
   - 默认 `run-all` 不做真实上传；
   - 只有任务级 Bilibili 配置满足 `dry_run=false` 且 `confirm=true` 时，完整链路才允许追加 `publish-bilibili`；
   - 或新增独立选项 `include_publish_bilibili`，避免仅凭 `confirm` 自动上传。
2. 推荐实现：
   - 在任务配置中增加 `workflow.include_bilibili_upload` 或复用现有配置但在 UI 明示；
   - `_run_all_job()` 在 `prepare-publish` 后按配置追加 `publish-bilibili`；
   - 若未确认真实上传，则可选择追加 dry-run 或保持只到发布包，规则需写入 UI 与文档。
3. 补测试：
   - 默认 run-all 不调用 `publish-bilibili`；
   - 开启包含上传且 dry-run 时生成 `bilibili.dry-run.json`；
   - 开启包含上传且 confirm=true/dry_run=false 时传入真实上传配置；
   - 未确认真实上传时不得误传真实凭证上传。

## 4. 任务卡片运行中状态

### 当前实现

- 数据模型已有 `TaskStatus.RUNNING` 和 `StepStatus.RUNNING`。
- 前端 `statusLabel()` 已包含 `running: "运行中"`。
- CSS 已定义 `.status-running`。
- API `_task_payload()` 会追加 `running` 字段，来源是 `_RUNNING` 中未完成 Future 或 `.task.lock`。
- 前端任务列表和详情页使用有效状态，优先显示 `queued`，其次显示 `running`。

### 问题判断

运行中状态已有部分实现，但仍有缺陷：

1. 任务提交到后台后，API 返回的原始 `task.status` 可能仍是 `pending` 或上一次 `success`，因为调度函数没有立即持久化 `TaskStatus.RUNNING`。
2. 前端任务列表 badge 的文字和 class 需要使用同一有效状态，避免排队或运行状态样式不一致。
3. 单步卡片只看 `task.steps[step]`。后台 job 只有开始执行后才会写 `StepStatus.RUNNING`，如果任务还在单 worker 队列中，卡片无法可靠区分等待、排队和真正运行。
4. `_RUNNING` 是进程内状态，进程重启后只能靠 `.task.lock` 推断运行中；如果锁文件存在但没有活锁，或任务曾写入 `running` 后异常退出，状态恢复策略需要更明确。

### 计划

1. 修复前端列表 class：
   - 先计算 `effectiveTaskStatus(task)`；
   - 文字和 class 都使用 `effectiveStatus`。
2. 后端调度时立即持久化任务状态：
   - 单步运行、下载、完整链路进入队列时标记为 `running` 或新增 `queued`；
   - job 开始具体步骤时标记当前 step 为 `running`；
   - job 结束后写回 `success`/`failed`。
3. 如果增加多 worker，建议区分：
   - `queued`：已提交但未开始；
   - `running`：已获取任务锁并正在执行；
   - `pending/success/failed`：现有语义保持。
4. 补测试：
   - POST run 后 `/api/tasks` 立即显示有效运行状态；
   - 前端渲染运行中 badge 的文字和 class 一致；
   - 任务失败后运行中状态消失，错误信息保留。

## 建议实施顺序

1. 先修 Web 调度器队列与锁持有时序。这是导致“显示运行中但无结果”的直接原因。
2. 再修 URL 草稿去重、稳定任务合并和 `_pending` 清理。该问题会污染任务列表和产物目录，且可能导致同目录多记录。
3. 然后修任务状态展示和状态持久化。并发修复后，状态语义需要同步变清晰。
4. 最后把 Bilibili 上传纳入完整链路，并保持显式确认。上传是外部副作用，必须放在默认安全规则之后实施。

## 验收清单

- 同时启动两个不同任务，一个运行完整链路、一个下载，第二个任务进入排队状态，前一个任务结束后再实际推进产物。
- 同一个任务重复点击运行或下载仍返回 409，不会并发写同一任务目录。
- URL 草稿下载完成后，旧 `_pending/<draft>` 目录不存在。
- 同一 URL 多次创建草稿不会产生多个空白任务。
- 同一视频已存在稳定任务时，草稿下载不会新增第二条同源任务记录。
- 完整链路默认不会真实上传 Bilibili。
- 显式开启完整链路上传并确认后，`publish-bilibili` 被执行；未确认时只 dry-run 或跳过，按最终产品规则验收。
- 任务列表和详情页都能显示运行中，文字与样式一致。

## 实施结果

实施日期：2026-06-14

- Web executor 改为单线程 FIFO 队列；不同任务按提交顺序串行执行。
- 调度阶段只登记 Future 并持久化 `queued`，目录锁在后台 job 真正开始时获取；同一任务
  的重复启动仍返回 409。
- `TaskStore` 的读取-修改-写入增加进程内可重入锁，避免多 Web worker 同时更新
  `tasks.json` 时丢失任务状态。当前仍限定单 Web 实例，不作为多进程数据库方案。
- URL 草稿按规范化 URL 复用已有任务；下载完成后清理旧 `_pending` 目录。命中已有
  `source_key` 时合并任务配置和下载状态到稳定任务，删除草稿记录。
- 新增任务配置 `workflow.include_bilibili_upload`。Web `run-all` 默认只运行到
  `prepare-publish`；开启该选项后追加 `publish-bilibili`，未满足真实上传确认时自动
  使用 dry-run。
- 调度后立即持久化任务/首步骤 `queued` 状态；后台 job 开始后转为 `running`；
  任务列表 badge 的文字和 CSS class 统一使用有效状态。
