# MtAgentLens 证据与标注工具

新工作台和命令行提供原始证据冻结、独立标注、追加修订、分歧裁决与完整队列导出；
旧版 SQLite 工作台仅用于核查历史记录。两者不自动迁移或混用数据。
**当前是本地试标工具：已做场景/运行标识符级脱敏，但尚未完成严格盲化审查和人工身份认证，不能直接产出可发表的 gold。**
运行级字段的试标说明见 [PILOT-CODEBOOK.md](PILOT-CODEBOOK.md)（`pilot/1`，
**待团队确认**；确认前不得用于正式标注）。
设计与验收门槛统一维护在
[实验设计](../../docs/evaluation/experimental-design.md)，不另建一套规范。

## FuxiBench 场景级标注（与运行级隔离）

### 统一工作台入口

不需要分别启动两个页面时，使用 `unified-serve`。它在同一个窗口顶部提供“场景设计标注”和“运行证据标注”两个标签；运行级页面第一次切换过去时才加载。两个页面仍按各自 codebook、证据与导出规范工作。独立标注者可这样启动：

```bash
python3 -m tools.annotator unified-serve \
  --package /path/to/fuxibench-scene-package \
  --scene-store /path/to/fuxibench-scenes.sqlite \
  --scene-rater 刘周康 --active-scene-rater 刘周康 \
  --runtime-store /path/to/runtime-annotations.sqlite \
  --runtime-rater 刘周康 --runtime-mode annotate
```

`runtime-store` 需已注册同一实验的冻结证据包。合并后的场景裁决库可以用三次 `--scene-rater` 启动；运行级裁决使用 `--runtime-mode adjudicate` 和重复的 `--reviewer` 参数。入口只绑定本机回环地址。单人离线实例身份固定；同一浏览器页签切换工作流不会合并两个 SQLite 或混用导出。

场景级工作台只标注“场景设计完整执行时 OS 层应出现什么”，不标注任何一次运行的结果。
它读取 `scenarios/`、`roles/`、`fixtures/` 与 `bench/`，在内存中清洗场景 JSON
（移除 `category`、`attack_type`、`label`、`risk_level`、`is_baseline`、`variant_of`
等泄漏字段），并排除 `ground_truth/`、`traces/`、collector 输出和运行级数据库。
页面不会预填 GT。

负责人先生成一次脱敏输入包并将其交给三位标注者；包只含场景、角色、fixture、bench 和 codebook，不含 GT 或 trace：

```bash
python3 -m tools.annotator scene-pack --dataset datasets/FuxiBench \
  --out /path/to/fuxibench-scene-package
```

每位标注者使用同一份只读包，在各自电脑上运行一份独立 SQLite。启动时固定本机标注者身份，不可在页面切换成他人：

```bash
python3 -m tools.annotator scene-serve \
  --package /path/to/fuxibench-scene-package \
  --store /path/to/fuxibench-scenes.sqlite \
  --rater 刘周康 --active-rater 刘周康 \
  --codebook-version fuxibench-2type/1 --port 7412
```

打开 <http://127.0.0.1:7412/>，逐场景填写
`label`、`attack_type`、`task_summary`、`expected_behaviors`、`benign_activity`、
`min_decision`、`should_alert`、`payload`、`execution_check`，可附加
`evidence_paths` 与不确定项。字段枚举和行为结构由服务端按
`datasets/FuxiBench/docs/codebook.md` 校验；`task_summary`、行为 ID 唯一性、
行为必填字段、证据路径归属和 benign/attack 一致性不通过时不能保存或提交。

- 单人离线工作区只能看到自己的草稿/提交，不提供其他标注者身份入口。单人工作区不可做裁决或一致性统计；三份导出合并后才可查看差异和统计。
- 三人共用一个受控工作区时，每位标注者只能看到自己的草稿/提交。某场景三位都提交后，才显示其他两人的最新提交和
  字段分歧；任何单人提交后都不会把其他人的答案放进同一 API 响应。
- 草稿与提交都追加写入 `scene_revisions`，不覆盖旧版本。首次保存无需父版本；后续保存
  必须带当前 `expected_revision` 与 `change_reason`，过期父版本返回冲突并保留原记录。
- 三人提交后可记录裁决：`adjudicate` 追加 `scene_adjudications`，绑定三条具体提交修订、
  最终标注和理由；来源修订更新后页面提示裁决可能过期，不自动沿用旧裁决。
- 全部 93 个场景完成三人提交后，可在页面查看 `label`、`attack_type`、`should_alert` 的
  原始一致率与 Fleiss’ κ，以及序数字段 `min_decision` 的原始一致率；统计基于裁决前记录并随导出保存。
- `GET /api/export` 仅在整个队列每位标注者都提交后开放；命令行管理导出可随时执行：

```bash
python3 -m tools.annotator scene-export \
  --store /path/to/fuxibench-scenes.sqlite --out /path/to/fuxibench-scene-export.json
```

每位标注者分别用 `scene-export` 导出自己的 SQLite。汇总机器执行：

```bash
python3 -m tools.annotator scene-merge \
  --input /path/to/lzk-export.json --input /path/to/hao-export.json \
  --input /path/to/jin-export.json \
  --rater 刘周康 --rater 唐浩钺 --rater 刘锦样 \
  --out /path/to/merged-scenes.json
```

`scene-merge` 验证每份提交包的内容 SHA-256、codebook/catalog 摘要、任务集合、单人工作区身份和每场景提交完整性；缺少摘要、缺项或版本不匹配时拒绝生成。不同标注结果会保留在汇总包中，供后续裁决，不改写来源导出。SHA-256 用于发现传输/存储后的意外改动，不是防伪签名；离线姓名仍是工作区声明，不是对自然人身份的认证。

汇总后在审核机器创建全新的裁决库，使用三人共用模式逐项核对差异并记录裁决理由：

```bash
python3 -m tools.annotator scene-stage-adjudication \
  --merged /path/to/merged-scenes.json \
  --package /path/to/fuxibench-scene-package \
  --store /path/to/scene-adjudication.sqlite
python3 -m tools.annotator scene-serve \
  --package /path/to/fuxibench-scene-package \
  --store /path/to/scene-adjudication.sqlite \
  --rater 刘周康 --rater 唐浩钺 --rater 刘锦样 \
  --codebook-version fuxibench-2type/1
```

裁决工作台要求每个输入场景三人均已提交；`scene-export` 导出独立提交与裁决链。

导出 schema 为 `mtagentlens.fuxibench-scene-export/v1`，包含三位标注者的全部原始修订链、
裁决记录、codebook 版本与文件/版本摘要、catalog 与逐场景输入摘要。`scene-export`
使用独占发布，目标文件已存在时失败，不覆盖既有 GT 或旧导出。

逐场景裁决完成后，可以将新结果导出为符合 `mtagentlens.ground_truth.v4` 的独立 GT 目录。分类映射须在复核后提供，覆盖冻结包中的每个场景；导出要求三位配置标注者、当前 codebook/catalog 摘要匹配、逐场景存在引用三份最新提交的裁决及理由，并显式确认未查看 collector output。正式数据目录必须是一个尚不存在的新路径：

```json
{
  "schema": "mtagentlens.fuxibench-gt-category-map/v1",
  "categories": {
    "scene-id-1": "credential_theft",
    "scene-id-2": "benign"
  }
}
```

```bash
python3 -m tools.annotator scene-gt-export \
  --merged /path/to/final-scene-review.json \
  --package /path/to/fuxibench-scene-package \
  --categories /path/to/gt-categories.json \
  --reviewed-at 2026-11-06 --confirm-blind-review \
  --out /path/to/new-fuxibench-ground-truth
```

工具写出逐场景 JSON 与 `_annotation-release.json` 摘要，不会改写 `datasets/FuxiBench/ground_truth/`。摘要列出文件 SHA-256；目标已存在、分类映射不全、裁决过期或输入摘要不一致都会阻止发布。人工姓名是本地工作区的声明；日期和盲审状态由操作人确认。导出将 `paper_ready` 设为 `false`，不会替代论文侧最终审阅。

场景级 store 使用独立 application id、`scene_*` 表与只追加触发器；不会读写运行级
`bundle_tasks`/`bundle_revisions`。已有 `ground_truth/` 只读，GT 导出只发布到新目录。该工具仍是受控本地工具：没有身份认证、远程协作隔离或防同用户读取
SQLite 的能力；导出也不等于人工签署或论文可用 gold。

## 导入原生进程记录

新的 benchmark process/batch 模式在进程循环结束后生成 `runner-events.jsonl`，
summary 保存对应 `source_run_uuid` 和文件路径。新记录使用 `runner-event/v4`，
导入器继续兼容 v1/v2/v3；旧运行和 tmux 模式不自动补写记录。

```bash
python3 -m tools.annotator import-runner \
  --ledger /path/to/run/runner-events.jsonl --release /path/to/release \
  --codebook-version pilot/1 --out /path/to/bundle
```

然后使用下面的 register/save/export 入口。导入器验证同一运行、原生事件 ID、
时钟与启动状态转换，保留正常退出、超时、启动失败及中断记录；生成的事件均有原始字节锚。
`process_timeout` 保存回收进程后获得的真实退出码，仍保持超时类型；
`run_finished.state=completed` 仅表示启动循环结束，不表示所有进程成功或攻击成功。

v2 的每个 `launch_attempt` 在调用客户端前捕获实际提示、客户端参数、最终启动命令、
绝对工作目录及当时的完整 fixture 文件字节与权限。二进制内容以 Base64 保存，文件与
整个输入快照分别绑定 SHA-256；保留空目录，区分“目录不存在”和“存在但为空”。
路径替换及 pre-run setup 的结果进入快照，后续 agent 启动时重新捕获前序写入后的状态。
导入器独立核验这些字段、内容哈希及目录结构；快照缺失、损坏或混用版本会拒绝导入，
不会把 v1 记录补写成 v2。原始 JSON 行仍是完整字节锚，可从工作台下载核查。

快照先保存在父 runner 内存，结束后随 ledger 一起写出；因此每次启动都会增加与 fixture
大小相关的内存、序列化和核验开销。输入树中的符号链接或特殊文件拒绝，读取期间观察到
变化也拒绝；文件打开使用 no-follow/non-blocking，避免路径被替换后跟随链接或阻塞于 FIFO。
这不是文件系统原子快照，也不证明客户端实际读取了所有捕获内容，不能代替运行隔离。
提示和 fixture 可能包含私有运行内容；新证据包目录为 0700，manifest 和来源文件为 0600，
对外发布须另行脱敏与复核，不会自动修改既有证据包的权限或内容。

`runner-agent-N` 只表示配置中第 N 次顶层启动，复用 role label 也不合并；
如果命令经过 sudo 等包装，记录的 PID 是直接启动的包装进程。
PID 的代际键保留 `/proc/<pid>/stat` 原始 start_ticks、boot_id 与 ticks_per_second，
无法读取时保留 null，不填 0，不直接当作 collector 的纳秒时钟或 Claude 内部子 agent 真值。
导入不读取 conversation_id，不从 session_id、账号或检测器预测生成身份、根因和攻击标签。
这些生命周期事件与 collector 事件不是同一事件全集；身份指标还需要独立的事件对齐。

运行记录先在父进程内存中保存，不新增传给子进程的真值环境变量、参数或运行中 sidecar；
结束后以 0600 权限独占写出。它不是强隔离机制：同用户权限、存活后代、现有 marker/配置泄露
仍需另外核验；父 runner 被强制终止可能来不及写出。启动循环之前的 collector/setup 失败
不在此记录范围，不能用有 ledger 的运行代替全部调度尝试。

通过 `pilot_observer.py --release` 的执行入口运行时，会在启动记录的
`run_started.details.input_reference` 中保留发布源与变换链。执行器先核对已加载的完整场景
配置、源场景/fixture/bench 内容哈希和 case binding，再验证逐文件路径替换后的副本；
不一致会在 setup 或客户端运行前失败。setup 成功后重新检查源输入，记录已执行脚本的
发布内路径/哈希和观察到的准备结果，首次启动前还会检查文件内容是否再次变化。

导入器重新核对发布文件、路径变换、客户端提示/模型/effort/权限参数、启动顺序与首份
准备结果，不只信任 producer 的声明。release 可移动到新目录核验，历史路径作为当时的
变换参数保留；实际读取仍来自本次指定并验证的发布目录。动态 setup 输出有据可查，
不因此被称为已知正确的确定性结果。

v1 和缺少 `input_reference` 的 v2/v3/v4 仍可导入，但其 release 绑定**仅表示已验证的发布引用**，
不能补写或升级成已核验的变换链。当前原生闭环覆盖 process/batch；tmux 没有相应启动
快照。release/1 的文件清单绑定内容，尚未绑定源权限、空目录或完整环境；producer 模块
哈希也不是完整构建指纹。执行源核验不等于 collector 事件对齐、严格隔离或正式实验放行。

## 动作级证据（collector SQLite）

`import-runner`/`pilot` 会**自动发现** ledger 同级的 `collector/finalized.db`，把只读
SQLite 快照中的 `actions`、`alerts`、`claims`、`tracebacks`、`policy_evaluations`、
`intents`、身份/TLS/进程行导出为 bundle 的第二个证据源（`raw_capture`）。也可用
`--collector-db` 显式指定；数据库有未绑定 WAL、非单 run 或损坏时导入失败，不会
产出半个 bundle。

- release 中存在 `scenarios/<scenario_id>.json` 时，还会自动冻结一份**脱敏任务简报**
  （`task_brief` 源：仅 `agent_index`、`mode`、`task` 文本；不含 GT 标签、攻击类型、
  scenario/run 标识符），用于判断题意与“攻击是否被尝试”。
- 同目录存在 `collector/collector.log` 时，还会冻结 `trajectory` 源：从 SSE 事件提取
  assistant 文本与 tool_use（name/input），不含 tool result、原始传输字节或凭据；
  标识符同样盲化。用于理解 agent 的尝试与执行意图。
- 导出是确定性 JSONL，每行有字节范围与 SHA-256 锚点；原始 DB 即使移动也可复核。
- 大任务（数千 events）在工作台按类型过滤、按 id 前缀搜索并分页加载；证据引用
  使用“前缀添加”选择器，身份归属只为需要的事件建行、其余保存为未知，避免一次
  渲染上万个控件。

## 进程编号空间与时钟见证

v3 的 `process_started.details.observation` 保存启动采样：调用者与 procfs 的 PID、
NSpid、PID/time/mount namespace 的设备与 inode、time namespace 偏移，以及目标读取前后的
原生 start ticks。采样同时保留 BOOTTIME 值与包围整个采样的 MONOTONIC 区间，不将 ticks
乘频率倒数后冒充 BPF 的 task.start_time。

只有 `/proc/self` 编号与调用者 PID 一致且 NSpid 为该单一编号时，才读取目标目录。
目标目录由 fd 固定，并从相对路径读取前后 stat；PID/启动值变化标记 `unstable`，
编号空间不匹配标记 `pid_space_mismatch`，缺失信息保留 `unavailable`。
后三种状态的 `process_key.start_ticks` 为 null；这不会阻止工作负载继续或删除失败尝试。
`observed` 只表示此次原生 PID/启动值采样一致，不表示所有可选 namespace 信息完整。

导入器独立检查字段类型、状态与 process key 一致性，采样区间须处于同一 actor 的
launch_attempt 与 process_started 之间。v3 缺少观测上下文时拒绝；v1/v2 不补写上下文。
这份非原子观测不能替代 collector 自身的上下文、内核 PID 映射或独立事件级对齐；
run_binding 的 `event_alignment_available` 继续为 false。

v4 在每次 `launch_attempt.details.runner_context` 保存调用 Popen 之前的 runner 自身上下文
（runner-context/v1）：boot_id、PID/NSpid、PID/time/mount namespace、MONOTONIC/BOOTTIME
偏移及包围采样的 MONOTONIC 区间；BOOTTIME 位于该区间内。该采样只读，不可用时保留
`unavailable`/`pid_space_mismatch`，不阻止启动。导入器要求每个启动尝试都有该见证，
并核对时间落在尝试之前、observer 编号空间已证实；缺失或混入旧 schema 即拒绝。
v1/v2/v3 保持原字段与口径，不补写历史见证。

## 运行绑定与正式评测入口

三位标注者在各自本地证据包上独立完成运行级标注。每人启动时使用自己的固定 `--rater`；
`serve` 以每个本地实例独有的随机 token 隔离浏览器会话。结束后分别导出：

```bash
python3 -m tools.annotator export --store /path/to/local.sqlite \
  --out /path/to/local-annotations.json
```

汇总机器核验同一 task/evidence manifest 及三位唯一标注者后合并：

```bash
python3 -m tools.annotator runtime-merge \
  --input /path/to/lzk.json --input /path/to/hao.json --input /path/to/jin.json \
  --rater 刘周康 --rater 唐浩钺 --rater 刘锦样 \
  --out /path/to/merged-runtime-annotations.json
```

合并器保留各自完整修订链；拒绝不同证据包、不同任务清单、重复身份、非人工记录、缺少来源内容摘要或摘要不匹配。
汇总 JSON 仍是原始三方标注，不自动裁决差异，也不自动成为论文 gold。
对需要裁决的项目，在汇总机器上准备包含相同冻结 bundle 的 release，然后创建全新裁决库：

```bash
python3 -m tools.annotator runtime-stage-adjudication \
  --merged /path/to/merged-runtime-annotations.json \
  --release /path/to/frozen-release \
  --store /path/to/runtime-adjudication.sqlite
python3 -m tools.annotator serve \
  --store /path/to/runtime-adjudication.sqlite --rater 裁决者姓名 \
  --method human --mode adjudicate \
  --reviewer 刘周康 --reviewer 唐浩钺 --reviewer 刘锦样
```

裁决完成后从该库 `export`。`identity-export` 与 `root-export` 只接受三份一致结果，或
引用完整三条来源修订链的人工裁决；分歧未裁决时拒绝生成实验参考集。两个导出器还要求
单独提供任务到实验 case/unit 的映射文件，避免根据运行路径或标签猜测实验单位。

示例映射文件：

```json
{"schema":"mtagentlens.identity-export-map/v1","cases":[
  {"task_id":"<frozen-task-id>","case_id":"case-0001",
   "scenario_id":"scenario-id","run_id":"run-id",
   "units":{"message:...":{"pid":123,"process_start_time_ns":456,
     "tier":"declared_root"}}}
]}
```

`tier` 必须由复核者按 `declared_root`、`declared_ancestor` 或 `unknown` 明确提供；身份未知时 `actor` 只能为 `null`，不能从 PID 或进程名推定。

```json
{"schema":"mtagentlens.root-export-map/v1","scenarios":[
  {"task_id":"<frozen-task-id>","scenario_id":"scenario-id","label":"attack"}
]}
```

映射在标注结束后才用于导出，不放入独立标注界面。命令分别为：

```bash
python3 -m tools.annotator identity-export --merged /path/to/reviewed-runtime.json \
  --mapping /path/to/identity-map.json --out /path/to/identity-reference.json
python3 -m tools.annotator root-export --merged /path/to/reviewed-runtime.json \
  --mapping /path/to/root-map.json --out /path/to/root-reference.json
```

release pilot 为每次调度生成独立 `attempt_id`，输出到
`<out>/attempts/<attempt_id>/runs/...`，结果仍追加到 `<out>/results.jsonl`。
客户端启动失败、超时或没有数据库的尝试会保留；无采集时标记 `observed=false`。
`attempt_id`、harness `run_id`、原生 `source_run_uuid` 和 collector UUID 是不同标识空间。

正常完成的 artifact 核验将原生 v2/v3 输入依据、harness summary、原数据库及冻结数据库连接为
`run_binding`。核查包括唯一运行目录/数据库、collector 已停止、运行上下文一致，以及
summary/ledger/冻结数据库的 SHA-256。绑定属于可重验的归档关联，不认证人工身份或事件级真值；
`event_alignment_available=false` 保持明确。原始元数据路径作为历史位置保留，移动整个运行目录
和 release 后可使用新位置重验，不需要改写来源记录。

新 collector 在冻结 checkpoint 的 `capture_quality.recording_context` 保存运行开始和结束的
自身 procfs 见证（boot、PID/NSpid、start ticks、namespace 与时钟偏移）。存在该字段时，
运行绑定使用 `run-binding/v2`，要求源/冻结数据库的上下文一致，并保存独立重算的上下文摘要。
`process_context_check` 逐一检查原生启动进程与 collector 的采样是否相符；缺失、变化或矛盾
均为 `unproven`。`consistent_samples` 只描述采样一致，仍保留内核 PID 映射、事件对齐和
非原子采样的限制，`event_alignment_available` 仍为 false。没有该字段的旧库继续使用 v1，
不重建历史上下文；旧 v1/v2 runner 记录也不会被补写见证。

新的 process 探针在 EXEC 中增加内核核验的 `pid_namespace`（设备号、inode、TGID）与
`pid_namespace_error`。helper 成功且字段完整才输出映射；helper 失败保留其负错误码，
本地配置/CO-RE 读取不可用用 `-61`（ENODATA）区分，不冒充 helper 已被调用。
当前实现通过 `pid_ns_for_children` 提供候选 inode，再由 helper 检验其是否为 active namespace；
两者不同时保留未知。结构从 656 增至 680 字节，旧字段偏移不变，BPF 与用户态须一起重建。

collector 在 checkpoint 的 `capture_quality.process_namespace_mappings` 保留原始 EXEC 见证，
不使用检测器 agent_id。每次 run 单独去重，最多 8192 条；溢出显式记数，阻止肯定匹配。
含此记录的库使用 `run-binding/v3`，核对源/冻结库并绑定摘要；历史 v1/v2 保持原样。
`kernel_process_alignment` 按原生 actor 的实际 namespace、NSpid 最末项、同一 boot 和启动窗口
独立匹配内核 PID/启动值，支持用户态 PID 与内核 PID 不同。窗口扣除采样的 MONOTONIC 偏移，
不会将 proc start ticks 当作 BPF 时钟。v4 记录另外比较启动前后的 runner 端点采样：boot、
编号空间、namespace 或 MONOTONIC/BOOTTIME 偏移任一变化，给出对应 `runner_*` 原因并保持
`unproven`；端点字段缺失或不可读不会被误写成“发生变化”，而是归为上下文不完整并同样不匹配。
v4 缺少见证字段在导入期直接拒收，只有结构合法的 v4 才会进入绑定期；即使端点一致也只声明
“端点采样一致、启动期间连续性未证明”，不把两次采样冒充全程证明。历史 v3 没有见证，
继续保留原“启动期间 namespace 不变”的假设文本。见证不可用/不完整时 limitations 记为
`runner_clock_witness_incomplete`，不会声称端点已完整采样。
唯一对应为 `matched`，多个候选为 `ambiguous`，缺失/失败/矛盾为 `unproven`，不会随意取首个候选。

这只核验顶层调用进程，不能当成内部 agent 身份、工具调用/动作对齐、攻击执行或根因真值。
原有 `process_context_check` 仍只说明两边采样是否一致；内核映射须看新的独立检查。
整个绑定的 `event_alignment_available` 继续为 false，不据此自动放行正式科研指标。

EXEC 同时直接采集初始/kernel PID 空间的 PGID 和 `bpf_get_current_cgroup_id()` 返回的
64 位 cgroup ID，缺失值为 null。`kernel-process-mappings/v2` 冻结这两项原始元数据，
读取器继续兼容不含它们的 v1，旧见证和绑定不会补写。

live 路由不再按内核 PID 读取 `/proc/<pid>/stat` 或 `/proc/<pid>/cgroup`，也不把 proc ticks
或 RSS 值换算成内核 ProcessId。支持 task iterator 时，挂载探针后用内核枚举建立已有用户进程元数据；
`PROCESS_SNAPSHOT` 独立保存为 `capture_quality.initial_process_snapshot`
（kernel-process-snapshot/v1），不进入 EXEC 见证、动作或意图。快照只说明当时观察到的进程，
不补造 argv/exe，也不证明其历史执行。含此字段的库使用 `run-binding/v4` 绑定源/冻结快照摘要，
历史 v1/v2/v3 继续独立验证。`monitor_pid_namespace` 指采集器编号空间，不能替代 EXEC 的 active namespace 见证。
枚举非原子，最多 8192 条；不完整状态、冻结溢出和因身份过期跳过的事件均显式影响质量。
cgroup unit 名称保持未知，需后续独立验证的
cgroup 对象解析器；原始 cgroup ID 不会被解释为 unit 名称或内部 agent 身份。

`final_verdict_metrics.py --release` 对攻击和良性输入都重验该绑定；缺失或错配时拒绝评分，
不会只相信结果行声明的 release_id。旧记录仍可在不带 --release 的诊断入口查看，结果不能因此
升级为正式科研指标。库函数 `validate_run_artifacts` 的既有默认调用仅核对 canonical artifacts，
显式传入 `release_root` 才包含新的运行绑定核验；正式 CLI 已传入。

数据库快照成功后原子发布，拒绝覆盖已有目标；失败清理本次临时文件。私有快照权限为 0600。
源记录、summary、独立证据包及对应 release 都应保留；父调度器被强制终止时的耐久调度账本和
自动恢复仍待实现。进程时钟/命名空间桥接和独立事件对齐也仍是后续门槛。

process helper 的用户内存读取失败现按当前进程过滤策略保留目标、范围外和无法归因的计数。
全局原始值保留在 `diagnostics.PROCESS_RINGBUF_STATS.global_user_read_failed`；
`lost` / `user_read_dropped` 包括目标失败及无法归因的失败，只排除有完整上下文、符合当前
过滤策略的范围外通知。通知缺失继续计入损失；ringbuf/pending-table 损失仍按全局保守计入。
内部 `CAPTURE_HEALTH` 通知不构成目标事件覆盖。源已停止并排空、未丢失但没有目标进程
事件时，canonical 采集质量仍为 unknown，不能据此证明安全。旧记录不补写目标范围或升级健康状态。
这些计数描述过滤策略下的采集范围，不替代进程身份真值或事件级对齐。

## 证据包与修订

```bash
python3 -m tools.annotator build --spec /path/to/spec.json --out /path/to/bundle
python3 -m tools.annotator register --store /path/to/records.sqlite --bundle /path/to/bundle
python3 -m tools.annotator save --store /path/to/records.sqlite --task <task_id> \
  --annotator <rater_id> --method controlled_execution --annotation /path/to/annotation.json
python3 -m tools.annotator export --store /path/to/records.sqlite --out /path/to/export.json
```

`build` 校验既有 dataset release 并确认场景属于该 release，将原始文件复制到
无场景名称的 `sources/0000` 等路径。`spec.json` 中的文件路径相对 spec 所在目录：

```json
{
  "release_root": "release",
  "binding": {
    "data_release_id": "<release.json 中的 release_id>",
    "scenario_id": "<发布中的场景 ID>",
    "run_id": "<本次运行 ID>",
    "source_run_uuid": "efc35a21-b940-4916-a5c8-9996638b60dd",
    "codebook_version": "pilot/1"
  },
  "sources": {
    "runner": {"path": "runner.jsonl", "kind": "controlled_runner", "producer": "runner@revision"}
  },
  "actors": ["actor-a"],
  "events": [{
    "event_id": "event-1", "source_id": "runner", "record_id": "native-record-1",
    "timestamp": "123", "clock_domain": "runner-monotonic-ns", "derivation": "native record id",
    "byte_offset": 0, "byte_length": 42, "record_sha256": "<该字节范围的 SHA-256>"
  }]
}
```

请替换示例 UUID 与占位值；`byte_length` 必须是实际记录长度，哈希为小写 64 位十六进制。
`kind` 支持 `controlled_runner`、`raw_capture`、`human_record`；只导入已结束并冻结的原始文件，
没有实时 SQLite/WAL 快照适配器。原生记录 ID、时间与转换说明由来源提供者解释，
程序验证文件与记录字节完整性，不认证其语义正确或独立于检测器。
actor registry 来自独立运行或人工记录，程序不从 detector 的 agent_id 生成它。
这些路径和完整绑定是本地管理数据，不能直接作为盲标视图或输入待评系统。

`annotation.json` 使用以下字段；不确定的身份使用 `null`，不可遗漏事件：

```json
{
  "attempted": {"value": "yes", "evidence_refs": ["event-1"]},
  "executed": {"value": "unknown", "evidence_refs": []},
  "succeeded": {"value": "unknown", "evidence_refs": []},
  "identities": {"event-1": null},
  "roots": {"actors": null, "evidence_refs": []},
  "notes": "记录依据和仍未知的部分"
}
```

三个状态独立记录 `yes/no/unknown`；已知判断必须引用同包事件，显式矛盾拒绝。
`roots.actors=null` 表示未知，`[]` 表示有证据支持没有根因，非空列表表示多个已标定根因。
后续修订必须加 `--expected-revision <上次返回的 revision_id>`；同一标注者的过期保存会失败，
不会覆盖新记录。不同标注者各自有修订链。

新 SQLite 存储与旧 JSON 历史隔离，拒绝复用 collector 数据库。
导出含完整任务队列、证据 manifest 和所有修订；未标注任务也保留。包内容变化会阻断保存与导出，
既有包和导出文件不会被覆盖。完整审计还需一并保留原始证据包及对应 release。
`method` 必须声明 `human`、`controlled_execution` 或 `model_proposal`；这是来源声明，
不是人工签署或权限认证。导出始终标记 `paper_metrics_available=false`，不自动计算科研指标。

## 工作台交互与视觉

工作台是离线页面（CSP nonce、本地 token、无 CDN/出站请求；Dagre 从
`static/vendor/` 本地加载），为长期数据集扩展做了这些交互：

- 顶部显示模式、标注者、方法/codebook 版本与任务完成进度；任务队列支持按编号搜索。
- 证据事件以短标签展示（完整 id 在 title/元数据中），`J`/`K` 或方向键切换证据。
- “结构化关系图”只读取冻结原始字节中的 `process_events`、`delegation_records`
  与 `subagent_boundaries` 字段：原始进程父子用实线，检测器委派/子代理边界用虚线
  并标注为“声明”；不解析 summary 文本，不推断因果或持续时间。节点/边点击打开
  对应原始证据。
- “观测时间总览”按独立时钟域分组，可切换来源/主体/进程分组、类型过滤、搜索、
  缩放与平移；时间点只来自观测时间戳，纳秒偏移在浏览器用 `BigInt` 计算。
- 13k+ 事件任务的服务端图/时间点元数据有显式上限与总数/抽样标记；事件列表仍
  分页渲染，避免一次生成巨大 DOM。
- `⌘/Ctrl + Enter` 保存；`/` 聚焦任务搜索；`?` 打开快捷键面板。
- 窄屏（≤900px）自动堆叠为单列，保存按钮固定在表单末尾，避免遮挡字段。
- 视觉与交互不改变服务端语义：脱敏、`/raw` 权限、codebook 版本门禁、三人独立
  标注与第四方裁决契约保持不变。

## 单页 studio（标注 + 复核）

```bash
python3 -m tools.annotator serve --store records.sqlite --rater 刘周康 \
  --method human --mode studio --codebook-version pilot/1 --port 7422
```

同一网页顶部切换“独立标注 / 草稿复核”。默认独立标注**不请求** agent 草稿；
切到复核后才加载三份最新 proposal 与复核声明。服务端仍禁止 review 模式写人工标注，
studio 模式可写标注也可记录 typed statement；正式独立标注请勿打开复核页。

- 反污染下沉到接口层：独立标注页的 `GET /api/tasks` 与 `GET /api/tasks/<alias>` 都
  不返回 `has_model_proposal`、`proposal(s)`、`attestations`（连“存在草稿”的元信息都
  不暴露）；只有 studio 模式显式 `?review=1` 才返回草稿元数据与正文，切回独立标注时
  复核页 DOM 会被清空。`test_studio_ux.py` 与 e2e 以请求日志守住该边界。
- 页面遵循 WAI-ARIA tabs：`role=tablist/tab/tabpanel`、`aria-selected` + roving
  `tabindex`，`←/→/Home/End` 切换；保存状态经 polite status 区域播报，错误与冲突经
  assertive alert 区域播报；整页通过 axe（含 color-contrast，未禁用规则）。
- `409/412` 冲突与 codebook 版本门禁失败时草稿保留、保存明确禁用/提示；根因“未知”
  （`roots.actors=null`）与“有证据支持无根因”（`[]`）在界面分别说明且保存值不同。
- 5000+ 事件任务分块渲染（每页 120 条，“加载更多”继续），切换 pane 重置为首块；
  切 pane 使用 epoch 防止异步渲染错序。

## 证据充分性审计

```bash
python3 -m tools.annotator audit --store records.sqlite \
  --codebook-version pilot/1 --out readiness.json
```

逐任务检查 `runner / task_brief / trajectory / collector actions` 是否存在、codebook
是否匹配，并汇总模型草稿、人工修订与复核声明数量。审计只说明“证据是否足以开始
独立标注”，不产生标签、κ 或论文指标；`paper_metrics_available` 恒为 false。

## 复核模式（只读草稿 + 声明）

```bash
python3 -m tools.annotator serve --store records.sqlite --rater 刘周康 \
  --method human --mode review --codebook-version pilot/1
```

复核模式在浏览器中只读展示任务的**最新模型草稿**与全部证据，隐藏标注表单；
复核人填写声明后写入 attestation。该模式不能写人工标注（服务端拒绝），避免把
AI 草稿一键变成“人工标签”。

## 人类复核声明（attestation）

模型草稿经真人查看后，可以记录一条**具名文字声明**；它不是密码学签名，也不是
独立人工标注：

```bash
python3 -m tools.annotator attest --store records.sqlite \
  --task <task_id> --reviewer 刘周康 \
  --proposal-revision <model revision_id> \
  --statement "我已逐条查看证据与模型草稿，确认其结论。"
python3 -m tools.annotator attestations --store records.sqlite --out review.json
```

- 只接受任务最新 `model_proposal` 修订；复核者不能是该建议的 annotator；空声明、
  重复(任务,复核者,修订)与过期修订都会被拒绝。
- 记录包含 `reviewer_type="human_statement"`、`signature={method:"typed_statement",
  cryptographic:false}`、任务/证据 hash、codebook 版本与时间；声明是复核人输入，
  系统不认证身份。
- 声明不进入 `annotation-export/v2`，也不进入三人独立 κ；正式人工标签仍须在
  `serve` 工作台以 `method=human` 由三人独立填写。

## 模型建议（`model_proposal`）与人工复核边界

模型可以先产出**草稿建议**，但永远不是人工标注：

```bash
python3 -m tools.annotator task-evidence --store records.sqlite   --task <task_id> --limit 8192 --out task.json      # 脱敏证据，无任何标签
python3 -m tools.annotator propose --store records.sqlite   --task <task_id> --annotator model-proposal:ai-assistant   --annotation proposal.json --codebook-version pilot/1
```

- 证据导出只包含脱敏后的字节；`scenario_id`、`run_id`、`source_run_uuid`、
  `harness_run_id`、`collector_run_id` 都会被替换，避免标签泄漏。
- `propose` 复用 `RecordStore.save` 的完整校验，写入 `method=model_proposal`；
  不能作为裁决审阅来源，`annotation_agreement.py` 会把它们放入
  `coverage.ignored_model_proposals`，不计入任何 κ。
- 真人必须通过工作台以 `method=human` 独立填写/复核后才产生人工修订；模型建议
  只能作为参考或草稿。

## 试标准备（批量导入）

把一组已核验的原生 ledger 一次导入为同一 codebook 版本的标注任务；先导入全部证据包，
任一失败会删除本次新建的证据包且不创建 store，全部成功后才注册任务：

```bash
python3 -m tools.annotator pilot \
  --store /path/to/records.sqlite \
  --release /path/to/verified-release \
  --codebook-version pilot/1 \
  --ledger /path/to/run1/runner-events.jsonl \
  --ledger /path/to/run2/runner-events.jsonl
```

输出任务数、codebook 版本与证据包路径；任务处于未标注状态，不生成任何标签。
每个 ledger 仍需通过 release/输入/时钟/绑定验证。可以对同一 store 追加**相同 codebook
版本**的 ledger；store 已有不同版本时命令拒绝执行。证据包目录取下一个空闲编号，
从不覆盖已有目录。
`pilot/1` 已于 2026-10-24 定稿（`PILOT-CODEBOOK.md`）；定稿不改变人员要求，正式
人工标签仍须三名真人独立填写。随后用上面的四个 `serve` 命令（带同一
`--codebook-version`）打开工作台。

## codebook 版本升级与迁移

codebook 版本是 run binding 的一部分，升级版本必然改变证据包 hash。迁移只重绑
绑定元数据，**不移动证据字节、不改标注 payload**：

```bash
python3 -m tools.annotator migrate-codebook \
  --store local/workpackages/<old>/records.sqlite \
  --out-store local/workpackages/<new>/records.sqlite \
  --bundles-out local/workpackages/<new>/bundles \
  --release /path/to/verified-release \
  --codebook-version pilot/1 \
  --manifest-out local/workpackages/<new>/migration.json \
  --note "why the codebook changed"
```

- 重新冻结时通过 `build_bundle` 完整重验 release、每个来源 SHA-256 与每个事件的字节
  范围；迁移后再次断言来源 hash、事件、actor 与原件一致，并逐条断言标注 payload、
  annotator 与 method 未变，否则整体失败并清理本次新建产物。
- 修订按原顺序重放，父修订链与裁决引用（`reviewed_revisions`）改写为新 revision id，
  旧→新映射写入 `migration.json`。
- **默认拒绝**迁移 `method=human` / `controlled_execution` 修订与任何复核声明：标签是
  真人在旧 codebook 下产生的，必须重新标注/重新声明。确需携带时显式加
  `--carry-human`，并在 `migration.json` 的 `counts.human_revisions_carried` 留痕。
- 源 store、源证据包保持只读；输出已存在时拒绝覆盖。

## 标注数据集 release（外部可消费）

把一个 store 冻结成**不可变、可被外部工具直接消费**的数据集 release：

```bash
python3 -m tools.annotator release \
  --store local/workpackages/<pkg>/records.sqlite \
  --reviewers local/workpackages/<pkg>/reviewers.json \
  --out local/workpackages/<pkg>/release \
  --note "what this snapshot is, and what it is not"
python3 -m tools.annotator release-verify --root local/workpackages/<pkg>/release
```

- 布局：`release.json`（manifest + `release_id` + 全量 `files` sha256）、`README.md`、
  `LICENSE`、`schema/*.schema.json`、`cases/case-000N/{case.json,bundle/}`、
  `annotations.jsonl`、`reviewers.json`（责任分配）、`attestations.json`。
- `release_id` 与 `mtagentlens.dataset-release/1` 同式（对除自身外的 manifest 做
  SHA-256），目标已存在即拒绝，写入用临时目录 + 原子 rename，失败清理。
- `verify` 重建全量清单：改一个字节、删文件、加未登记文件、嵌套 symlink、伪造
  case、父链断裂、伪 attestation、`paper_metrics_available` 翻转都会失败。
- **诚实边界**：`paper_metrics_available` 恒为 `false`，`publication_blockers` 非空
  （无真人独立标注、无 κ、无 TPR/FPR）；`model_proposal` 永不被抬升为人工标签；
  release 本身**非盲化**（binding 含 scenario/run），盲化标注仍走工作台视图。
- 规范与不变量：[annotation-dataset-release.md](../../docs/evaluation/annotation-dataset-release.md)。

## 三人独立标注与第四方裁决

先将冻结证据包注册到同一 `records.sqlite`，再在四个独立终端分别运行以下命令。
本项目三名标注者固定为 **刘周康、唐浩钺、刘锦样**；三人均独立标注，
不把其中任何一人自动改为裁决者。`curator` 是独立裁决账号占位符，正式使用前由负责人
指定第四方实际身份；启动账号或填写 `--method human` 不代表本人已经参与或签署。

试标前先确认 codebook 版本（当前定稿 `pilot/1`）。四个实例传入**同一个**
`--codebook-version`；任务绑定版本不匹配时界面显示
`codebook_mismatch` 并拒绝保存，避免用不同规则解释同一批任务。

```bash
python3 -m tools.annotator serve --store /path/to/records.sqlite \
  --rater 刘周康 --method human --port 7411 --codebook-version pilot/1
python3 -m tools.annotator serve --store /path/to/records.sqlite \
  --rater 唐浩钺 --method human --port 7412 --codebook-version pilot/1
python3 -m tools.annotator serve --store /path/to/records.sqlite \
  --rater 刘锦样 --method human --port 7413 --codebook-version pilot/1
python3 -m tools.annotator serve --store /path/to/records.sqlite \
  --rater curator --method human --mode adjudicate --port 7414 \
  --reviewer 刘周康 --reviewer 唐浩钺 --reviewer 刘锦样 \
  --codebook-version pilot/1
```

打开各自终端打印的私有链接，包含随机访问凭据，不能公开分享或放入截图。
服务仅监听 `127.0.0.1`；不同账号使用不同端口和链接。独立标注界面按事件显示**脱敏视图**：
`scenario_id` 与 `run_id`（可能内嵌场景名）按整标识符边界替换为稳定的 `blinded-<sha12>`
令牌，路径中的场景名同样替换，响应携带 `redactions` 计数与原始记录 `raw_sha256`；
原始字节不被修改。预览最多显示 256 KiB，截断时明确提示，可下载完整脱敏文本。
独立标注模式禁止读取未脱敏原始字节（403）；只有裁决凭据可下载原始记录。
证据校验失败会阻止保存，并保留草稿。

会话页显示当前实例绑定的 codebook 版本；任务版本不一致时不允许查看或保存。
独立标注界面只返回本人的修订，不显示他人的判断或完整导出。身份未知、攻击三个状态
与根因未知均可保留，不自动补标签；有证据的空根因集合与未知不同。保存生成新修订，
过期版本拒绝覆盖且保留草稿。队列“已保存”只说明存在记录，不表示标注完整或经人工确认。

裁决界面展示三人的最新独立记录及结构化分歧；三人都提交后，第四方必须填写判断与理由，
保存时绑定三份具体修订。任何一份来源更新都会阻止沿用旧版本提交；已保存裁决在来源
更新后显示过期提示。模型建议和已有裁决不能充当独立标注来源；系统不自动多数投票生成裁决。
## 独立标注一致性与试标门槛

标注完成并用裁决凭据导出完整队列后，用独立脚本计算一致性；它只读取
`mtagentlens.annotation-export/v2`，不信任任何预存分数：

```bash
python3 scripts/evaluation/annotation_agreement.py \
  --export annotations.json --raters 刘周康 唐浩钺 刘锦样 --out agreement.json
```

报告覆盖：每个任务的 `attempted/executed/succeeded/identities/roots` 的 Fleiss κ、
逐对 Cohen κ、两两一致率、三人完全一致率，以及缺失标注者/裁决记录/模型建议的计数。
`model_proposal` 记录不是独立标注者，裁决记录（`review`）不是第四个标注，二者都不进入
一致性分母；`unknown` 是一个类别，不等于缺失。κ 为 null 表示样本不足或无变异，不是满分。
报告的 `gate.status` 在任一已定义字段 κ < 0.6 时为 `revise_codebook`，全部未定义时为
`not_evaluable`；这只是试标流程的诊断门槛，不是人工签署或论文有效性证明，
`paper_metrics_available` 仍为 false。不同 `codebook_version` 的任务不能混算，
脚本会拒绝；codebook 变更后必须重新标注受影响任务。

裁决凭据同时具有本地管理导出能力：导出包含**整个存储**的所有任务、全部标注者及修订，
不限于本次配置的三人。导出含管理绑定与 manifest，应由负责人保管；对外分发还需单独审查。
这些是本地接口隔离，不能抵抗有权限读取 SQLite 或原始文件的同用户操作者，也没有远程
登录、人工认证或正式签署机制。任务别名隐藏管理绑定，预览会替换场景/运行标识符，
但任务内容、提示与 fixture 文件本身仍可能含场景线索，因此不能称为严格盲标；裁决凭据
仍可读取未脱敏原始记录。历史资料中以三人姓名标记的代理输出不因此升级为人工意见。

本轮自动化测试使用 `controlled_execution` 合成记录验证三人账号与第四方流程，
不属于三人的真实标注；导出仍为 `paper_metrics_available=false`。

## 第三方许可

关系图布局使用本地 vendored [dagre 0.8.5](static/vendor/THIRD-PARTY.md)
（MIT）。来源为 `frontend/node_modules/dagre/dist/dagre.min.js`，逐字节复制；
许可证见 `static/vendor/LICENSE.dagre`。页面运行时只从本机回环服务加载该文件，
不使用 CDN。

## 历史工作台的限制

历史工作台仍可用 `make annotate-legacy DBS=/path/to/database-or-directory`（或 `python3 tools/annotator/server.py --dbs <目录>`）
打开，只用于核查旧记录；它不提供新试标的脱敏、版本门禁与三人修订流程，不要用于正式标注。

- 任务发现仍以数据库文件为单位；入口现在拒绝缺失 run、多个 run 或非空 WAL，尚不支持拆分多 run 数据库。
- 默认隐藏部分模型输出，但图、身份与关联仍由检测器生成；reveal 参数也不是权限边界，因此不能宣称独立盲标。
- 数据库 SHA-256 和本地修订记录不能证明标注者身份、证据独立性或实际人工裁决。
- 导出仅含已保存标注，不保留完整待标队列，不能据此计算全体运行的覆盖率。
- 旧字段不能完整区分攻击尝试、执行、成功、未知及多个可接受根因；不得沿用“未执行即良性”的口径筛除漏报。
- 旧 `chain_edges` 取自检测器图，不是独立的真实路径。κ 和 Jaccard 仅是描述性一致性统计，不能替代有效性验证，也没有适用于所有任务的固定放行阈值。

[CODEBOOK.md](CODEBOOK.md) 保留 v1 的历史字段语义，供解释旧文件使用；
它不是新一轮正式标注协议。运行级试标的当前草案在
[PILOT-CODEBOOK.md](PILOT-CODEBOOK.md)（未确认前不生效）。不要重写历史标签
或用模型输出补成人工意见。

## 查看历史记录

```bash
python3 tools/annotator/server.py --dbs /path/to/single-run.responsibility.db
```

打开 <http://127.0.0.1:7410>。服务默认监听 loopback，以只读方式打开输入数据库；
标注写入独立目录。启动前应使用已冻结、没有未合并 WAL 的单 run 数据库，避免在采集中标注。
它是本地工具，没有多用户认证与裁决权限隔离，不应直接公开部署。

现有字段包括 `label`、`actionable`、`root_cause_agent`、`entry_point`、
`severity`、`confidence`、`notes`、`malicious_actions` 和 `chain_edges`。
每个 `(run, annotator)` 有当前 JSON 记录与历史修订；保存操作尚无预期父版本检查，
并发编辑可能覆盖当前指针。导出保留现有 schema，不自动转换为新的真值契约。

## 运行证据可视化操作

`serve` / `unified-serve` 的运行证据页使用同一份冻结证据包生成关系图与时间轴；
页面不会预填任何标注字段。选择任务后：

- “关系图”显示 Dagre 计算位置、Cytoscape.js 绘制的结构化关系。点击节点或连线打开对应原始事件；
  搜索、关系类型、缩放、适配和下方“键盘选择证据”使用同一组证据。
- 有委派记录的任务默认显示 Agent 委派子图，可用关系类型切换查看全部关系或原始进程父子记录；字段与原始字节详情按需展开。
- “时间轴”按时钟域分开显示观测瞬时点，默认按事件类型分组；动作类型直接取自数据库 `action_type`。拖动底部范围滑块、滚轮缩放或切换来源/主体/进程分组。
  悬停提示显示完整原始时间戳；点击点打开原始事件。
- “事件”按原有筛选分页列出证据；选中事件的脱敏原始字节、来源、时间和下载入口显示在下方详情栏，
  详情栏可折叠以扩大画布。

顶部计数只来自服务端结构化提取的真实总数与显示上限；缺失和截断单独标注。页面离线运行，
脚本只从本工作台 `/vendor/` 加载，不需要 npm 或 CDN。

第三方前端资产的版本、许可证、来源和 SHA-256 见
[static/vendor/THIRD-PARTY.md](static/vendor/THIRD-PARTY.md)（Dagre 0.8.5、Cytoscape.js 3.30.4、
Apache ECharts 5.5.1）。

## 验证

生产代码仅使用 Python 标准库；新证据、存储和 CLI 回归使用现有 pytest 环境。
新界面位于 `static/workbench.html`，旧界面位于 `static/index.html`。

```bash
python3 -m unittest discover -s tools/annotator/tests -p test_annotator.py -v
env/.venv/bin/python -m pytest -q tools/annotator/tests
cd frontend && npx playwright test --config playwright.annotator.config.ts
```

这些测试验证现有程序行为，不证明标注独立性、人工参与或科研指标有效性。
