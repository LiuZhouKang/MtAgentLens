---
status: accepted
date: 2026-10-26
---

# 0017 — 证据约束的身份、授权与起源归因

## Context

当前检测链已含 ActionOutcome、候选引用／版本、CapabilityEnvelope、内容provenance、两阶段身份与traceback，但不同层丢失操作结果、内容完整性、任务授权和会话粒度。traceback的单个root与成员集合不足以支持唯一根因或任意有序链主张。详见[最终交接](../archive/engineering/handoffs/FINAL-CODEX-HANDOFF-2026-10-26.md)。

用户已确认仅eBPF观测边界并授权按本方案分阶段实施。本ADR记录实施约束，不修改ADR0015／0016，也不宣称各项能力已经完成。当前行为与已知局限继续以代码和验证产物为准。

## Options

1. 仅追加entry／ranking／ordered_chain字段：变动小，但会将现有证据缺失包装成更完整的解释，不能解决检测输入失真。
2. 替换全部采集／存储／身份与图为新平台：代价与回归面大，而且表示标准不能补出因果证据。
3. **建议**：保留现有管线，在共享类型与边界中保全证据、身份、授权和不确定性；从明确目标反向查询有证据的归因子图；预测／独立参考分开。

## Decision

### 1. 对象与断言的语义

| 对象 | 草案定义与约束 |
|---|---|
| Actor | 执行动作的可区分上下文；物理进程身份与逻辑会话分开，角色名不是认证身份 |
| Agent | 本run命名空间内的逻辑执行主体；同进程可有多个Agent，多个工具子进程也可属于同一Agent；关系必须有证据与粒度 |
| EntryPoint | 特定信任边界上，已观察到的不可信内容进入执行上下文的事件／片段；允许多个与未知 |
| RootSource | 目标动作归因切片中的有证据起源候选，可是Entity而非Agent；不声称已证明反事实必要原因 |
| Introducer | 有证据引入来源的主体；与内容作者、传播中继、受害执行者分别表示 |
| Executor | 被测动作的执行主体；执行不等于恶意、意图或责任 |
| Chain | 有类型的provenance DAG；边方向与偏序必须有独立依据，不强制并行支路总序 |
| RootAgent | 仅当研究问题要求且参考／预测均有可识别主体时，评某种明确角色的Agent候选；不得将资源node强转Agent |

不把“用户要求读取某文件”提升成“允许执行文件内任意指令”，不把私网／loopback／目录前缀／现有delegation视为充分授权。

### 2. 单一证据与身份合同

复用现有Intent／Action类型并补入可核验源引用，不另起不可校验的摘要账本。现有类型的内部ID／时间戳并未完整承载artifact hash、byte范围与原生call ID；历史缺失不得补造。原始来源保持不可变引用：producer/version、artifact hash、byte范围或原生event key、capture completeness、clock domain；派生对象记录转换版本、父引用与损失信息。

保留发生时间与观察时间的区别；历史tool call在较晚TLS请求中重现，不得用观察时间自动否定或证明因果。跨时钟排序需要锚点／误差范围；无法比较则保持无序。

进程键建议包含host/boot/pid namespace/pid/start；连接含进程生代和生命周期。conversation／tool-call ID有producer/run命名空间。字段缺失不是0；ssl_ptr和trace-id都不是全局可信身份。

两阶段身份输出贯穿message/action/graph/persistence/export。未唯一归属的OS事实保留但不硬分配，分别统计message/action覆盖。当前confidence保留heuristic标记，不当概率。

### 3. 授权与事实保全

可信任务／明确约束按actor/session和有效范围提供，带原始来源；模型提取为proposal，无法解析就unknown。完整任务有独立输入预算，不被前120字符的目录前缀替代。拟议信任根为受信调用方／实验控制者提供的明确约束；TLS中的role=user内容不天然认证。纯eBPF/TLS模式缺可验证授权根时保留authorization_unknown，不以解析成功替代授权成立。

ActionOutcome／syscall_ret随graph和candidate保留；initiated、completed、received和敏感内容传输不能互相替代。原始路径截断／字节容量／fd解析状态在规范化后继续存在。未知操作或未知授权不自动变成违规事实。

已提交candidate的事实、授权上下文或身份发生变化时必须改变version；旧Safe不能覆盖新候选。新增专门claim类别须有共享违规证据，不借任意邻近委派／taint边升级结论。

### 4. 归因查询与弃权

从具体action／claim目标反向切片，按边的语义和证据选择来源候选；有界遍历并暴露cut frontier。区分直接观测、来源声明、推断支持与竞争解释。时间邻近、词面复现、进程亲缘分别只是对应层次的支持，不自动组合成已证唯一安全根因。

输出每字段 `known/unknown/not_applicable`，可携带候选集合、alternatives、missing_evidence、support类型。无明确排序时不伪造概率或名次。一个真实候选可评@1；@3/MRR需另有排序规则。边插入次序不得决定根候选集合。

路径序由调用配对、对象版本／内容传递、明确依赖等约束产生；时间显示排序不用于因果得分。原始循环或冲突不抹除，以不一致诊断保留。Depth只能是定义后的路径深度，不能以子图边数冒充。

### 5. 互操作与存储边界

| 标准 | 映射建议 | 不推导的结论 |
|---|---|---|
| W3C PROV-O | 内容／产物版本→Entity，调用／OS操作→Activity，主体→Agent；映射used/generated/derived/associated/delegation | provenance不等于授权或反事实因果；不要求RDF数据库 |
| W3C Trace Context | 仅记录已观察到的traceparent/tracestate及来源；本地派生trace ID与原生ID明确区分 | 不注入被测应用，不把header当身份认证 |
| OTel GenAI | 固定版本映射Agent／model／tool spans；有完整起止／ID合同后才称OTel兼容，多来源用links | 当前Development，不冒称稳定；Span Links不等于攻击因果 |
| OpenInference | span kind、tool_call.id、graph node表示；无法表示的DAG／支持信息保留mtl扩展 | parent节点或UI树不证明恶意传播 |

DB沿用[ADR0005](0005-single-schema.md)的单schema／单writer／事务迁移，变更前先只读分类。旧冻结DB缺事实时输出missing而非补造；原始源字节不重写。导出默认content-free／脱敏，新增外部数据出口须同步安全文档。

### 6. 评估独立性

预测和reference各自绑定run／输入／来源与schema版本。3个fresh AI上下文与裁决记 `method=model_proposal`，不是人工gold，也不保证统计独立；同传感器原始捕获只可称共享传感器独立判读。

参考不得读取detector身份／claims／root／分数来补未知。独立原生会话／runner锚点足够时可机械对齐；继续遵守[ADR0016](0016-no-event-level-alignment-without-shared-anchor.md)，未共享锚点不进行事件级匹配。DSH日志只能说明其自身被测执行，不替代Claude日志。

RootSource与RootAgent、身份归属与检测准确率分别计分；已知参考上的预测弃权记miss并报coverage，参考未知报unavailable。部分参考的未标边不能当负例；闭世界完整DAG子集与部分证据评估分开报告。Root@1无需先构造多候选排名。

## Consequences and open choices

用户已确认：产品检测、身份与归因只用eBPF采集通路（包括现有TLS采集），不引入原生日志或框架插桩，不设原生日志增强模式。外部项目仅借鉴可迁移的方法。实验侧独立参考资料与产品预测隔离，不得补填预测缺失字段；eBPF不可识别的事实明确unknown。此边界已确认，不代表本ADR其余设计已整体接受。

九个excluded依据可信task／实现／oracle重审，不以新旧标签冲突或检测难度排除。困难良性应计分。用户已确认优先修复，30分钟仅作进展检查参考，不硬限时或超时删除；有明确进展继续，无进展换路或暂缓，避免死磕。合理投入仍不能定义一致授权／实现／oracle时才提出移除，逐项建议见主文。

后续按W1—W7小包实施；不把格式转换当方法有效性，也不以特定smoke必须得到某裁决验收。

## Confirmation（按阶段实现和验收，未全部完成）

- 规范化前后的路径截断事实、pending／failed结果在Observer及export保持一致；多字节、尾斜杠、dirfd与邻接前缀反例。
- 同ssl_ptr跨进程、PID重用、同进程多会话、跨producer相同conversation、消息级持久化回放与unassigned保留测试。
- 长路径不挤掉task；actor A授权不得清除actor B来源；读文件不自动授权内嵌指令；candidate version覆盖任务与事实变化。
- 无关delegation不改变claim／root；插入次序不改变候选集合；并行无序、多来源tie、证据缺失、截断frontier与循环可见。
- 未配对anchor拒绝评分；reference不含detector派生结论；partial／unknown分母与单候选@1测试。
- 原有18门禁维持；独立真实run、实际mode／overlap与全系统成本尚需验证，不由本ADR宣称通过。

## 实施补充：pre-resolver 输入（2026-10-26，包2a）

采用单一 `RawIdentityInput` 作为 resolver 前的冻结输入：含 SignalSnapshot 的原始进程、亲缘、TLS、conversation、直接消息/线程关联和 capture metrics，以及当时已有的 process delegation 候选对；不含 provisional/authoritative AgentId、reference 或 detector 最终图。delegation 是从观测构造的边界信号，不是原始 syscall 本身，也不代表认证授权。

DTO 用排序后的行列表表示复合键，避免 JSON map 的结构键限制和 HashMap 迭代随机性；转换必须保留未知值、校验冲突键并能重建同一信号快照。它在 authoritative resolver 调用前从冻结信号生成，放入现有 checkpoint JSON 的可选字段，不另起数据库 schema。历史 checkpoint 没有此字段时，身份消融标 unavailable，禁止从 checkpoint.identities 逆造输入。

消息绑定由实际解析事件产生，必须进入 SignalGraph 和既有消息持久化通路。进程携带多条 TLS 连接时，不可取 HashSet 的第一个 session 当该消息来源；没有唯一连接来源时，仅保留已观察的物理进程关联。物理进程关联不提供同进程多会话的唯一逻辑归属；message→conversation 锚点尚未闭合前，不以该修复宣称会话级身份已实现。

五条件 resolver/per-unit IdentityView 属于包2后续切片；本补充不改变现有 full 的合并策略，不使用 launch/role 参考作为预测输入，也不把输入可回放等同于身份有效性。

## 实施补充：五条件的可见性与配置（包2b）

C0 使用已观察 ProcessId（PID+已记录生代）的物理分组；C1 使用纯进程信息和预先配置的 agent comm 边界，工具后代归最近边界祖先，无边界时保留自己的物理分组并披露。两者是过程基线，不宣称与 full 的种子发现完全相同。C2/C3/C4 复用同一个生产 authoritative resolver，依次允许 TLS、conversation、显式 process-delegation 约束；C2 屏蔽双方 conversation 索引及对应 veto，C3 不读显式 delegation，C4 保留全部。TLS 自身能支持的独立进程边界不伪称 C4 独有。

实际生效的 comm 边界以 `IdentityBoundaryPolicy` 冻结，与 RawIdentityInput 同置 checkpoint 并用于本次生产解析；离线不得读取当前 ambient 环境替代。此切片不改 exe-boundary 语义。各条件在有区分作用的反例上验证，相同简单输入允许得到相同结果。

逐单元预测使用固定的 message/intent/action cohort：消息来自原始信号关联，intent/action来自已记录事实，禁止读取 checkpoint.identities、最终图、claims 或参考角色。该 cohort 条件于当前捕获/解析通路，不是全体原始OS事件；上游未捕获或未分类的事件不能被伪称已覆盖。输出未知与原因，不将所有unknown组成一个虚假共享cluster。物理过程标签不冒充已认证的逻辑Agent，同PID多会话无唯一锚点时仍弃权。

## 实施补充：消息到会话的捕获关联（包2c）

只接受当前已捕获、已判定为 LLM request 的正文中唯一的 conversation/session 声明，并将该声明关联到同次解析产生的 message 与实际 ProcessId。该字段是未认证的来源声明，不能从角色文件、runner 标签、相邻时间、最近请求或共享连接反推；response 没有自己的锚点时保持未知。冲突声明或一个 message 的冲突关联不得经 process fallback 消除。

关联进入 SignalGraph、RawIdentityInput 和 checkpoint；C2 必须屏蔽该关联，C3/C4 才可消费。同 PID 多会话可以据此分离有明确锚点的 message/intent，OS action 仍保持未归属。无锚点的 conversation candidate 不能伪装成已归属消息。旧 checkpoint 缺字段按缺证据处理，不补造。

复用当前 authoritative identity 持久化通路保存有消息支持的逻辑会话主体，其 process_ids 为空，禁止把共享 PID 写成独占的 authoritative process mapping。输出标明推断性质；这仅是 eBPF 捕获内容中的会话关联，不是身份认证，也不是准确率证明。

## 实施补充：身份实验导出（包2d）

身份实验复用现有 `replay` 可执行程序，新增可选 `--identity-output` 模式，输入仍为单一只读 responsibility DB 或 run directory。每个 run 必须有当前合同与版本的 checkpoint、PRE-resolver 输入和冻结 policy；实现指纹不符或输入缺失时失败，不静默丢 run，也不从 authoritative identities 补齐。输出包含源与当前实现指纹、run_id、固定 cohort、五条件预测及各类 known/unknown 计数，不读参考、不计算“准确率”。历史无锚点的数据不伪称已具备完整身份实验输入。

该模式不执行 Observer 或修改源库。所有 run 校验和预测成功后才以 create_new 写出 JSON，拒绝覆盖已有文件（包括源 DB）；普通 replay 行为保持原样。预测固定使用同一个 `identity_view_for_checkpoint`，避免 CLI 再实现一套解析器。

## 实施补充：原生调用引用与意图观测去重（包3a前置）

同一进程内“工具名+参数相同”不足以证明同一次调用；不得据此吞掉原生 call ID 不同或声明会话不同的工具调用。Intent 增加可选、带格式命名空间的原生调用引用与独立的声明会话上下文，只有捕获正文中的字段可填入。Anthropic tool_use.id 与 OpenAI tool_calls[].id 分别保留；SSE 在完成累积时保留其 content_block_start 中的原生 ID。声明的会话依旧不代表认证身份。

去重区分 process instance、声明会话、原生调用引用以及内容版本。相同引用的重现可以去重；不同原生调用、不同会话或已改变的参数版本须保留。响应没有会话锚点时不借最近请求补齐，因此响应与后来带会话锚点的历史重现可能保留为不同观测；它们不是已证明的两次执行。无原生调用引用的旧内容去重只能标为有限观测，不提供完整执行次数或因果顺序保证。框架内生成的 UUID 不充当原生引用。

引用通过当前单一 schema 的版本迁移保存到 intents，checkpoint 同时保留；旧行为空，不从 detector/GT 推回。下游事件 DAG 使用 IntentId/ActionId 观测发生项，另携带原生引用用于核对，禁止把资源节点或 actor 重复出现强压成有序链。以上先解决观测丢失与可核对性，不能单凭原生 ID 或文本重现声称执行、授权违规或因果关系。

## 实施补充：来源选择与动作授权分离（包3b前置）

默认检测应保留用户点名读取文件中的额外命令作为 untrusted 来源匹配候选。用户点名文件仅是来源选择，不能自动豁免该文件内所有行为。相反，捕获任务已明确包含同一命令时，不因命令在 tool result 中重现就推断新的注入；该规则仍只是未认证任务文本下的检测判据，不是授权认证。

否定／引用反例进一步限定该规则：严格来源匹配保留 `task_text_overlap` 竞争解释，不因任务中出现相同文本而删除候选。词面包含无法区分“执行此命令”与“不要执行此命令”；因此同命令候选也不等于新注入或违规。后续判定必须读取原始任务上下文，授权保持 Unknown。显式旧策略消融仍保留原词面豁免，不能当作 full 默认方法。

来源匹配不证明动作未授权：由文本重现产生的 derived tag 使用显式 `authorization=unknown`；图中可保留待判定的来源边界候选，但不得单独据此产生 hard violation 或最终 Unsafe。保留现有严格/旧策略对照接口用于明确标记的消融；正常默认采取上述保守来源判据，参数缺失时不回到“点名文件即全部豁免”。参考仍独立，不能以 detector 候选反写标签。

来源、任务文本和已缓存匹配均以 run_id 与 actor 为边界；相同 actor 字符串不跨 run 提供授权或污染内容。匹配 API 显式接收 run_id，不能仅在 ContentSpan 上记录而查询时忽略。SpanMatch 保留源标签，派生 confidentiality 按已有 lattice 传播，禁止把 Secret 来源硬编码降成 Public。

## 实施补充：身份判定前的动作保全（包2补充）

已经由当前捕获/解析路径产生的 Action，不能因当下缺少 agent ancestry 而从 action buffer、持久化和 checkpoint 中消失。保全与流式关联分开：未归属动作仍不送入需要主体的流式关联，也不凭空创建 Agent/PBC/claim；在最终冻结快照下允许重算身份与关联。无后续证据时必须留为未知，并进入身份实验 action cohort。

这仍不是所有原始 syscall 的覆盖保证：捕获过滤与 ActionClassifier 未支持的事件仍在该 cohort 之外。原有 `DIAG_ACTIONS_DROPPED_NO_AGENT` 名称作为历史诊断兼容，含义限于“未进入流式关联”；新产物必须明确 raw action 已保留，不再称这些 Action 丢失。禁止用这一来源保全扩大授权或自动降低检测阈值。

## 实施补充：claim 与 traceback 的单一接线（包3c）

先关闭现有 `claim.traceback_ref` 从未填入的缺口。共享构建入口同时产生 traceback 并回填对应 claim；无可解析或有歧义边界时清空引用，不猜 ID。Phase1 使用该入口，最终 checkpoint.evaluate 一次构建并将结果同时交给 live finalize 与 replay，禁止各自再生成另一组 UUID。

持久化仍先 claim 后 traceback，遵守当前外键；SQL 验收检查非空引用都有同 run/claim 的实际记录。此切片复用现有表，不新建存储。现有 legacy chain 字段仍只是子图成员、root 仍是启发式起源候选；连接成功不等于已提供 ordered DAG、RootAgent 排序或运行参考，不能用于虚构根因指标。

## 实施补充：解析的证据等级

结构化 request 必须按 message role 提取调用，tools/schema 声明及 user/tool data 不是 assistant 调用。可识别的 response/SSE 格式优先于通用树扫描；未知包装、raw-offset 和 regex 恢复只提供 TextFallback 假说，不保留为已验证的原生调用引用，不标 primary matching eligible。该标志不禁止现有 secondary correlation，消费方仍须区分来源等级，不把它解释成调用或执行证明。OpenAI 调用缺 arguments 时不可用合成 `{}` 冒充完整输入。

来源上下文与 conversation hint 也必须来自实际 TLS 捕获或其 HTTP/SSE 投影；stdio/file 中长得像 POST 请求的文本不是网络请求。该检查复用当前三类 TLS producer 的身份与方向判据，保留原始事件，不是新增目标豁免规则。应用侧断言／未知 producer 不提供该类物理捕获证明。

## 实施补充：原生调用的会话共指（包2f）

原生引用是协议内共指标识，不是认证身份。将真实结构化调用的观测 `(process instance, message_id, protocol format, call_id)` 在去重前保存为 PRE-resolver 信号；TextFallback／应用侧断言不进入该信号。只有同一进程实例内，带直接捕获会话锚点的 request history 才能提供该原生引用的会话候选。不能从最终身份、参考角色或最近请求生成候选。

冻结后，无直接锚点的 message 若全部正向引用只支持一个会话，可产生带推断性质的 message→conversation 共指；引用碰撞或该 message 的候选不唯一时弃权。直接捕获锚点不被派生共指覆盖。OS action 仍不因消息共指得到逻辑会话归属。

在 shared resolver 统一计算该关系。C2 同时屏蔽直接会话信息与原生共指观测，C3/C4 可消费；C0/C1 不读这些信号。原始 DTO 保留观测而非派生答案，旧 DTO 缺失按空处理。输出须能区分此推断关联，不将其称为认证或独立执行次数证据。

## 一手规范核对（2026-10-27）

以下为有限的一手文档核对，不是方法效果或因果正确性的证明。

- [PROV-O §3.2–3.3](https://www.w3.org/TR/prov-o/) 将 influence 组织为宽泛关系与可限定的中间对象；本次未见将其定义为反事实必要原因的条款。
- [PROV-CONSTRAINTS §1.2、§6.2](https://www.w3.org/TR/prov-constraints/) 的 validity 是一致性约束。事件顺序不由物理时间戳自动推导；precedes 是可含同时事件的 preorder，strictly precedes 才是严格先后。规范禁止含严格先后边的环，不等于要求所有 provenance 关系构成 DAG。因此我们的事件 DAG 是针对明确方向的候选依赖所作的应用选择，非“完整实现 PROV”的声明；纯相关或时钟不可比时不补造严格时序。
- [Trace Context §3.2.2.4–5](https://www.w3.org/TR/trace-context/#parent-id) 的 parent-id 指调用方对应请求的 span ID；sampled 不能保证完整记录。捕获到该字段只提供声明关联，不认证主体；本系统不主动插入传播头。
- [OTel 原 GenAI 页面](https://opentelemetry.io/docs/specs/semconv/gen-ai/) 已明确迁移至[官方 GenAI 独立仓库](https://github.com/open-telemetry/semantic-conventions-genai)。本次核对仓库 commit `20259355b9631e3f032dcc099d1b5a2666818c42`；导出映射仍需固定实际 schema 并逐字段测试，不根据失效旧链接宣称现行语义兼容。
- [AgentSight](https://arxiv.org/abs/2508.02736) 的标题与作者已核对：Yusheng Zheng、Yanpeng Hu、Tong Yu、Andi Quinn；其摘要描述 eBPF 捕获 TLS/内核事件并作关联与次级 LLM 分析。该来源支持研究方向参照，不能外推为本项目的检出率、开销或根因正确性证据。arXiv 页面同时列有 journal reference 与 ACM DOI；本轮未核验该出版物的评审过程，不由 arXiv 页面推断“未经同行评审”。

## 实施补充：请求历史中的来源候选（包3d）

新增证据是同一实际 TLS 请求中的声明历史关系：tool result 必须在 assistant 原生执行调用之前，且其内容满足现有 SpanStore 文本匹配，才记录来源候选。后出现的结果不能反向解释先前调用；另一次请求或另一个逻辑会话的全局缓存不能提供该见证。记录当前捕获 MessageId、process instance、捕获正文及规范化 JSON 的哈希、源与目标 JSON pointer、原生调用引用、匹配文本与任务重叠。哈希只锚定已捕获表示，不声称完整文件／网络载荷。历史次序是协议声明顺序，不是发生时间或因果证明。

该候选在 live capture 生成并冻结到 checkpoint，旧 checkpoint 缺失保持 unavailable，不从图或 GT 合成。它只补充审计与后续归因输入，不单独改变裁决、声称执行或授权违规，也不把协议共指当成 RootAgent 参考。

## 实施补充：实验与默认检测规则隔离

默认共享产物词表包含 benchmark 场景路径、固定 acceptance marker 与作者角色，不能作为通用检测方法的依据。默认部署不加载这些基准专用条目或默认权威角色；显式配置接口继续存在，使用自定义规则的运行必须披露，不能混入通用方法的主实验。基准样例仅作为测试 fixture 验证可配置解析器。类似地，单独命中 `hog.sh` 文件名不证明资源耗尽，不足以给出 Unsafe。移除此类默认规则不是调阈值或改参考标签，旧结果也不得直接当作当前实现效果。

## 实施补充：发生项归因输出（包3e）

归因从 claim 引用的实际 correlation evidence 出发，节点采用 IntentId、ActionId 与捕获请求内源 pointer，而不是将 actor／文件资源重复出现当成单一路径。文本回用边记录请求历史先后，intent/action 关联边保留原匹配等级，二者均不被改称反事实因果。

RootAgent 候选仅在更强的观测组合支持时产生：同一捕获上下文中的来源结果可唯一关联到同主体的原生 Read；该 Read 有成功且非零字节的同路径内核读；此前另一主体的原生 Write 提案包含所匹配文本，并与成功且非零字节的同路径内核写关联。记录全部候选，不以“最后一个动作／最高告警／委派祖先”充当引入者。这个组合仍只支持写入者来源候选，不能证明确切文件版本或排除未观测写入。

同证据层级的主体保留同组并列。只有候选组均为单元素时导出线性 ranked_agents；没有写入者证据时 root 弃权，执行者另列。source entry 可有多个竞争位置，不能随 UUID 排序选一个。时间戳只用于排除相反时序或建立明确的观测约束；不通过排序给并行分支补边。旧 traceback membership 不直接转成 ordered chain。当前默认不改变检测阈值或最终裁决。

导出引用补充：traceback 引用由 claim、冻结 phase、root candidate 与排序后的证据子图成员生成稳定摘要。同一证据版本的 live/replay 因此引用同一记录，不能通过忽略随机 UUID 来声称完整 parity。它标识该证据修订，不把子图成员的排列变成因果顺序；当前实现指纹依旧单独校验。


## 实施补充：离线消融的输入完整性与缓存边界（2026-10-28）

L0.5 在 GraphBuilder 处理原始事实前关闭 context propagation，阻止指令写入 taint、跨主体读取 taint 与委派上下文先生成 PBC 后再被事后删边；生产默认保持原配置。原始事实的 occurrence ID 必须非空唯一，关联引用必须能解析，不静默跳过。

重建仅接受与当前全部候选匹配的非空 ScopedReview，不接受没有候选绑定的旧 Observer verdict 或缓存 HardViolation；hard rule 应以本层事实重新计算。尚未冻结运行时 envelope/provenance/delegation 输入，故 L0.5–L3 显式 Partial，标记 runtime_graph_context_not_frozen。当前分数仅诊断，不是完整方法消融结果；必须补齐独立输入与回归后才能解除这一限制。


## 最终实验输入闭包（2026-10-28 续作设计）

目标是补齐既有 P1/P2/P3，不改93输入、标签、阈值，不引入应用插桩。沿用已授权实验计划，按以下三个可独立验收切片实施；每片先失败回归再实现。

1. **P1 冻结重建输入**：在现有 checkpoint 加可选 FrozenGraphInputs（单一 schema 的向后兼容字段）。包含按 IntentId 锚定的任务 envelope 原始派生值与来源 provenance，以及已有 delegation 的父/子 ProcessId 锚点。不得保存最终 graph/authoritative actor 作为重建输入。任务 envelope 在原生产点保存，保留派生时的 workspace，避免重放环境变量改变授权；多个来源合并身份时 envelope 取交集，不取并集。provenance 只在包含该功能的层安装，delegation 按当前 IdentityView 的进程实例重绑定，无法唯一绑定即 unknown/不造边。重建顺序与固定关联证据是本离线协议的一部分，不声称逐事件复刻在线到达次序；full 层也是该冻结输入上的重建结果，不冒充原在线判决。历史 None 保持 Partial，完整 Some 校验通过才解除 context 缺失状态。测试必须证明 envelope/provenance/delegation 实际影响图，且改 checkpoint.graph/identities 不影响结果；不得只删 Partial guard。
2. **P2 独立运行参考**：execution 评审原协议不变，增加同一 annotator CLI 的 identity/attribution 参考阶段；原生轨迹/runner 事实作为独立证据，禁止 detector graph/claim/verdict/identity 输出。已知身份须来自带作用域的原生会话、工具调用或实际进程发生项对应，不能以 PID/角色名直接宣称逻辑 actor；根因限定为证据支持的违规来源候选，不声称干预因果。entry/chain 使用原始 occurrence，偏序必须有向无环、来源先于使用，替代解释保留。3个 fresh AI proposal+独立裁决，所有已知字段有可解析证据锚点；unknown不当负例，不从预测补参考。准备/验证/导出默认离线，真正调用模型保留现有付费授权边界。输出复用现有 identity_reference/attribution_reference schema，不另起评估器。
3. **P3 受控操作观测**：以成功完成的唯一文件 open 操作为明确分母，按精确路径+进程实例/可用时间锚点匹配原始 process FILE_OPEN 与持久化 FileOpen，禁止 substring、任意 action 类型与重复计数。分别报告 raw transport missing 与 persisted-operation missing；不是全局 syscall/event 丢包率，ringbuf dropped 单列。C0无采集为N/A，缺原始流/health/完整 receipts 为不可估。adapter 以 schema5/原始事件格式测试与删/重复/错PID/路径前缀反证验收，真实采集校准仍须E0/受控实测；测试不能冒充已校准。移除任意 scalar+布尔开关即可伪称已验证 loss 的路径，metrics 只接收完整可追溯 adapter 记录。

验收次序：P1 聚焦 Rust ablation/runtime/graph；P2 synthetic已知/unknown/争议/篡改/循环/预测污染；P3 controlled-operation匹配与overhead集成。三者经独立源码复核后重建release并跑现有18门禁。真实权限/模型预算仍单列；不会用mock声称真机E0或效果已验证。
