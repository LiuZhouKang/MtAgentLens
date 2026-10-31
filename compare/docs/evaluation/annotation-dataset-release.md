# Annotation dataset release 规范（`mtagentlens.annotation-dataset/1`）

> 状态：annotation 试点数据的 release builder。原有试点只有 2 个良性受控运行、
> 0 条人工标签、0 条复核声明，因此 `paper_metrics_available=false`，
> `publication_blockers` 非空。本文描述数据契约、不可变量与消费方式。
>
> 2026-10-25 新准备了 15 项混合运行试点（5 良性、10 攻击场景），证据审计通过，
> 人工标注和声明仍为 0；它不替换旧试点，也不是正式代表性测试集。
> 当前三层数据与准入状态见 [数据卡](../../datasets/FuxiBench/DATASET-CARD.md)。

## 1. 目标与非目标

**目标**

- 把 append-only 标注 store 冻结成**可复现、可外部消费**的研究数据包；
- 让外部工具能**离线**校验「证据字节 + 标注修订 + 复核责任 + 复核声明」的完整性；
- 把「AI 草稿 / 声明方法 / 人工复核」之间的边界写成机器可读字段，而不是口头约定。

**非目标**

- 不生产人工标签，不提升任何 `model_proposal` 为 `human`；
- 不计算或伪造 κ、TPR/FPR、通过率；
- 不认证标注者身份、独立性或复核完成状态；
- 不替代 workbench 的盲化标注视图（本 release 是**非盲化**的科研复现包）。

## 2. 交付物

| 路径 | 作用 |
|------|------|
| `tools/annotator/release.py` | builder + 全量校验器 + 外部消费契约 |
| `tools/annotator/tests/test_release.py` | 使用纯合成 fixture 的回归测试（不读 `local/`） |
| `docs/evaluation/annotation-dataset-release.md` | 本规范 |

核心 API：

```python
SCHEMA = "mtagentlens.annotation-dataset/1"

build_annotation_release(
    store_path: Path,
    out: Path,
    *,
    splits: dict[str, str] | None = None,   # case_id -> dev|test，同scenario共组
    reviewers: Path | None = None,          # 复核责任 sidecar 输入
    note: str | None = None,
) -> dict                                   # 返回与 release.json 相同的内容

verify_annotation_release(root: Path) -> dict   # 全量校验，任何违规抛 ValueError
consume_annotation_release(root: Path) -> list[dict]  # 先校验，再返回外部消费视图
```

## 3. 目录结构

```text
out/
├── release.json              # 清单：schema / release_id / cases / splits / files / counts
├── README.md                 # 中文使用说明与诚实边界（不含 release_id，见 §7）
├── LICENSE                   # SPDX: Apache-2.0 OR MIT + 指向仓库 LICENSE-*
├── annotations.jsonl         # 每行一条修订；扁平化，保留 parent 链与 method
├── reviewers.json            # 复核责任 sidecar（内嵌 release_id）
├── attestations.json         # 如实导出的 typed 复核声明（可为空数组）
├── schema/
│   ├── annotation.schema.json       # draft 2020-12
│   └── evidence-bundle.schema.json  # draft 2020-12
└── cases/
    ├── case-0001/
    │   ├── case.json
    │   └── bundle/           # 完整复制证据包（bundle.json + sources/）
    └── case-0002/...
```

case ID 从 `case-0001` 起，按 `task_id` 字典序连续编号；`task_id == evidence_bundle_hash`。

## 4. `release.json` 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `schema` | string | 固定 `mtagentlens.annotation-dataset/1` |
| `release_id` | string | 内容地址，算法见 §5 |
| `codebook_version` | string | 本 release 绑定的**唯一** codebook 版本，来自证据包 `binding` |
| `cases` | list | case 元数据，与每个 `case.json` 逐字段相等 |
| `splits` | object | `case_id -> "dev" | "test"`，覆盖恰好全部 case |
| `files` | object | 相对路径 -> sha256；覆盖除 `release.json`、`reviewers.json` 外全部文件 |
| `counts` | object | `cases/annotations/annotators/methods/model_proposals/human_annotations/attestations/splits` |
| `paper_metrics_available` | bool | 恒为 `false`；builder 不认证可发表标签 |
| `publication_blockers` | list[string] | 非空，逐条声明为何不能当论文指标 |
| `note` | string \| null | 调用方说明 |

`case.json` / `cases[]` 字段固定为：`case_id`、`task_id`、`binding`、`split`、
`evidence_bundle_hash`、`evidence_counts`、`actors_count`、`events_count`。
`binding` 原样保留 `data_release_id`、`scenario_id`、`run_id`、`source_run_uuid`、
`codebook_version`。v1 证据保留历史发布身份；v2 普通目录证据明确记录
`data_release_id=null`，可参与标注和导出，不要求当前数据集具有发布清单。

`annotations.jsonl` 每行字段固定为：`case_id`、`task_id`、`annotator`、`method`、
`revision_id`、`parent_revision_id`、`saved_at`、`codebook_version`、`annotation`、
`is_model_proposal`；若该修订是一次裁决，额外保留 `review`（被复核 revision 与
rationale）。`method` 原样保留，绝不改名；`is_model_proposal` 必须与
`method == "model_proposal"` 一致。

## 5. `release_id` 与不可变性

与 `compare/tools/dataset_release.py` 同一惯例：

```python
digest = hashlib.sha256(
    json.dumps({k: v for k, v in release.items() if k != "release_id"}, sort_keys=True).encode()
).hexdigest()
```

（`ensure_ascii` 使用默认 `True`，与既有实现一致；写盘时可使用 `ensure_ascii=False`。）

因此 `release_id` 同时绑定 `files` 清单、`cases`、`splits`、`counts`、
`publication_blockers`、`codebook_version` 与 `note`。构建过程：

1. 校验输入（store 存在、非空、codebook 唯一、bundle 可校验、annotation 合法）；
2. 在 `out` 同级的临时目录 `.annotation-dataset-*` 中写完全部内容；
3. 计算 `files` 与 `release_id`，写 `release.json` 与 `reviewers.json`；
4. **自校验**（调用与外部消费者相同的 `verify_annotation_release`）；
5. 归一化权限后 `os.rename` 原子发布。

目标已存在（文件、目录或 symlink）立即报错；失败清理临时目录，不留下半成品；
同一 store + 同一参数构建两次得到相同 `release_id` 与 `files`（构建时间、主机名不写入）。

## 6. 校验不变量（`verify_annotation_release`）

1. release 根为普通目录，manifest 与所有文件均不允许 symlink；
2. `release.json` 键集合精确、schema 正确；
3. `files` 与实际字节清单逐项相等（检测**改字节 / 删文件 / 多出未登记文件**）；
4. `release_id` 可重算且相等；
5. 随包 `schema/*.json` 与校验器内置 schema **逐字节一致**，且 schema 自合法；
6. case 序号连续、`case.json` 与 `release.json.cases` 逐字段一致；
7. 每个 `bundle/` 用 `load_bundle` 全量校验（source sha256、record anchor、
   `evidence_bundle_hash`），且其 `binding` / hash / counts 与 `case.json` 一致；
8. `splits` 恰好覆盖全部 case，且与 `case.split` 一致；默认按 scenario_id 稳定哈希分组，显式同场景冲突拒绝。尚无变体 family_id，不能据此证明跨场景家族无泄漏；release 级 codebook 与每个
   binding 一致；
9. 每行 annotation：字段集合精确、method 合法、`is_model_proposal` 一致、
   codebook 一致、revision id 唯一、parent 链完整（append-only）、
   `_validate_annotation(payload, bundle)` 语义校验、随包 JSON Schema 结构校验；
10. `attestations.json` 精确键集；每条声明引用真实存在的 **model proposal** 修订、
    不得自审、codebook/证据 hash 一致、`paper_metrics_available=false`；
11. `counts` 与文件内容重算一致；`paper_metrics_available` 必须为 `false`；
    `publication_blockers` 必须等于 builder 的诚实声明（不能手工删空）；
12. `reviewers.json` 存在、结构合法、`release_id` 与本 release 一致、
    `paper_metrics_available=false`；
13. `README.md`、`LICENSE` 存在。

## 7. 为什么 `reviewers.json` 不在 `files` 里

`reviewers.json` 需要内嵌本 release 的 `release_id`（责任分配必须绑定到具体快照），
而 `release_id` 又覆盖 `files`。若把 `reviewers.json` 的字节算进 `files`，
就会形成「内容哈希依赖自身」的自引用，无法构造固定点。

因此采用最小代价的例外：

- `release.json` 与 `reviewers.json` 不进入字节清单；
- 校验器**强制要求** `reviewers.json` 存在，且其 `release_id` 与本 release 完全一致；
- 其余所有文件（含全部 case bundle、annotations、attestations、README、schema）
  全部由 sha256 清单覆盖。

副作用：`reviewers.json` 的字节级篡改只能通过其 `release_id`/结构检查兜底，
而不像其他文件那样被字节清单直接发现。这是有意的取舍，写在这里避免被误解。

## 8. splits 规则

- 未显式指定：`sha256(case_id)` 转为整数后按奇偶分配（偶数 `dev`，奇数 `test`），
  完全确定性；
- 显式 `splits`：键必须属于本 release 的 case，值必须是 `dev` 或 `test`；
  未显式给出的 case 仍按确定性规则分配；
- 不提供随机种子，因为没有随机性。

## 9. JSON Schema 与权威校验器的分工

`annotation.schema.json` 与 `records.py::_validate_annotation` 的结构性规则严格一致
（draft 2020-12）：

- 字段集合精确；三个 judgment 的 `value` 只能是 `yes/no/unknown`；
- 已知判断必须至少引用一条证据；引用不得重复；
- 单调链 `attempted >= executed >= succeeded`（`yes > unknown > no`）：
  `attempted=no` 强制下游均为 `no`；`unknown` 只能原样声明、不得被推断；
- `attempted=no` 必须给出**空** roots actor 集且引用证据；`attempted=yes`
  禁止空 actor 集（必须是 `null` 或非空列表）；
- roots 非 null 时必须引用证据；`notes` 必须是字符串。

JSON Schema 无法表达「引用必须属于该 case 的证据包」这类跨文档约束，因此：

- `evidence_refs ⊆ bundle.events`、`identities` 必须恰好覆盖 bundle 的全部 event、
  identity/root actor 必须已注册 —— 由 `verify_annotation_release` 结合证据包强制；
- `evidence-bundle.schema.json` 只镜像 `load_bundle` 的结构规则；字节级校验仍由
  `load_bundle` 执行。

## 10. 外部消费契约

`consume_annotation_release(root)` 先执行全量校验再返回：

```python
[
  {
    **case,                       # case.json 的 8 个字段
    "release_id": "...",
    "codebook_version": "...",
    "bundle_dir": "cases/case-0001/bundle",   # 相对 release 根
    "annotations": [ {revision row}, ... ],   # 该 case 的全部修订，按 store seq 顺序
  },
  ...
]
```

消费方也可以只依赖 `release.json` + `annotations.jsonl`（纯标准库），
逐文件 sha256 与 `release_id` 的校验片段见随包 `README.md` §4 / §5。

## 11. 构建、校验与真实数据

```bash
cd /home/lzk/MtAgentLens

# 构建（若 CLI 已接线，建议子命令 `release`；当前可直接调用 Python API）
env/.venv/bin/python - <<'PY'
from pathlib import Path
from tools.annotator.release import build_annotation_release, verify_annotation_release
out = Path("local/workpackages/annotation-dataset-release")
release = build_annotation_release(
    Path("local/workpackages/<new-store>/records.sqlite"), out,
    reviewers=Path("local/workpackages/2026-10-24-agent-annotations/reviewers.json"),
    note="agent annotation pilot dataset release",
)
verify_annotation_release(out)
print(release["release_id"], release["counts"])
PY
```

builder 对 `codebook_version` 完全中立：只从证据包 `binding` 读取，不做任何版本
白名单；同一 store 若混用多个 codebook 版本，会显式报错（一个 release 只绑定一个
codebook），不会静默挑选或改写。

## 12. 诚实边界（必须保留）

- `model_proposal` 是 AI/模型草稿，**永远不是 human 标签**，也不因多次修订变成标签；
- 当前试点 0 条人工标注、0 条 κ；`paper_metrics_available=false`；
- 2 个良性任务不足以做 TPR/FPR，也不含攻击样本结论；
- `reviewers.json` 是责任分配，不是复核完成证明；`attestations.json` 才是 typed
  复核声明，且不是独立人工标注、不是密码学签名；
- release **非盲化**（binding 含 scenario/run uuid），标注时另用 workbench 盲化视图；
- `publication_blockers` 由 builder 生成且校验器要求与生成结果一致，不能手工删空。

## 13. 测试

`tools/annotator/tests/test_release.py`（42 个用例，全部使用合成 fixture）：

- release_id 可复现、`files` 清单完整；
- case / annotations / reviewers / attestations / schema 契约；
- 字节篡改、删文件、加文件、release_id、schema、symlink、case 伪造、
  counts/blockers 篡改；
- 即使重算 `files` 与 `release_id`（reseal），annotation 引用完整性、
  provenance 字段、parent 链仍被拒绝；
- `consume_annotation_release` 契约与篡改拒绝；
- splits 确定性与显式覆盖；
- 不可变目标、混合 codebook、空 store、缺 store、缺 bundle 的无半成品失败；
- `annotation.schema.json` 与 `records._validate_annotation` 的合法/非法样本交叉验证；
- `evidence-bundle.schema.json` 对冻结 bundle 的合法/非法样本验证；
- codebook 参数化（非 `pilot/1` 版本、README 不含硬编码版本）；
- 声明为 human 时仍不抬升为论文 gold；adjudication `review` provenance 保留。

## 14. CLI 接线建议（给 Lead）

`tools/annotator/__main__.py` 增加子命令（与现有风格一致）：

```text
annotator release --store <records.sqlite> --out <dir> [--splits <json-path>]
                  [--reviewers <reviewers.json>] [--note TEXT]
annotator release-verify --root <dir>
annotator release-consume --root <dir>      # 可选，打印 consume JSON
```

实现只需 import `build_annotation_release` / `verify_annotation_release` /
`consume_annotation_release`，把 `--splits` 的 JSON 映射读入后传入；错误沿用
`main()` 现有的 `ValueError` 捕获（退出码 1）。
