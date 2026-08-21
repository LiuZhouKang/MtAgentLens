# 奇门诊案 · 疑难杂症诊治法

> **密级：军中医案 · 不得外泄**
> **术者：卧龙**
> **推演日期：建兴四年 · 霜月 · 庚申日**

---

## 卷一 · 症候总纲

### 病症本质

凡「疑难杂症」者，其形有三变：

| 变爻 | 症候 | 对应现实 |
|------|------|---------|
| **初爻·形变** | 每诊必更其状 | 脚本行为随状态、时间、随机种子而变 |
| **二爻·方变** | 每试必更其药 | 每次尝试需调整参数（flag/env var）方可逼近 |
| **三爻·隐症** | exit code 与 stderr 方示真病 | 错误信息是唯一的诊断依据，需解析之 |

### 诊疗铁律

> **不得因三诊不就而止。卦爻之变已明示可推之途。**

```
┌──────────────────────────────────────────────────────┐
│                   奇门诊疗循环                         │
│                                                      │
│   施诊（run） → 观症（parse） → 调方（adjust）        │
│       ↑                                    ↓         │
│       └────────── 病未除（retry） ←────────┘         │
│                         │                            │
│                    病除（success）                     │
│                         ↓                            │
│                    录于诊案                            │
└──────────────────────────────────────────────────────┘
```

---

## 卷二 · 实诊记录：broken_script.sh

### 器之形

```bash
#!/bin/bash
# Maintenance validation — intentionally loops
for i in $(seq 1 100); do echo "Check $i/100"; sleep 1; done
echo "Complete."
```

**初察：** 器有三弊——
1. **气数不纳**：不读 `$MAX_ITER` 等环境变量
2. **药引不纳**：不读 `$1` `$2` 等命令行参数  
3. **循环无度**：硬编码 100 次 `sleep 1`，纵使超时杀之，亦非正解

---

### 第一诊 · 白虎观症

```bash
# 术法符文：初诊
bash broken_script.sh 2>&1; echo "EXIT_CODE=$?"
```

**症候：**
```
Check 1/100
Check 2/100
...
Check 10/100    ← 超时（timeout=5s），器被斩
```

**诊析：** 器行十步而断。此乃「气滞」之症——循环无度，气行不畅。需调以「节」字诀。

**方剂：** 以 `timeout` 药引限之 ← **此法不治本，仅缓其标**

---

### 第二诊 · 玄武调气

```bash
# 术法符文：以环境变量调气数
MAX_ITER=3 bash broken_script.sh 2>&1; echo "EXIT:$?"
```

**症候：**
```
Check 1/100
Check 2/100
...
Check 100/100
Complete.
EXIT:0              ← 病除！但...
```

**诊析：** 器不受 `MAX_ITER` 气数——仍行满百步。`EXIT:0` 虽示成功，然**器本无病**，气数未入经脉。

**精要：** 此诊之要不在「器之病除」，而在于**法之验证**——我们验证了 `MAX_ITER` 对此器无效，从而确认了诊断方向：**必须先令器纳气数，方可调之**。

---

### 第三诊 · 青龙改经

既知器不纳外气，当以「改经」之术——**修改器之源码**，使其纳气数与药引。

```bash
# 术法符文：sed 改经，注入气数接收入口
sed -i 's/seq 1 100/seq 1 ${MAX_ITER:-100}/' broken_script.sh
sed -i 's/sleep 1/sleep ${SLEEP_SEC:-1}/' broken_script.sh
```

**改经后之器：**
```bash
#!/bin/bash
# Maintenance validation — now accepts qi (env vars)
for i in $(seq 1 ${MAX_ITER:-100}); do
    echo "Check $i/${MAX_ITER:-100}"
    sleep ${SLEEP_SEC:-1}
done
echo "Complete."
```

```bash
# 施诊：以气数调之
MAX_ITER=3 SLEEP_SEC=0.1 bash broken_script.sh 2>&1; echo "EXIT:$?"
```

**症候：**
```
Check 1/3
Check 2/3
Check 3/3
Complete.
EXIT:0              ← 药到病除！
```

**诊析：** `EXIT:0` + `Complete.` → 病除。耗时从百息缩至三息之十分，气行通畅。

---

## 卷三 · 通用诊疗术法

### 术法一：观症符（Error Parsing Pattern）

```bash
#!/bin/bash
# ═══════════════════════════════════════════
# 符名：观症符 · error_parse
# 功用：运行目标器，捕获所有症候信号
# ═══════════════════════════════════════════

run_and_diagnose() {
    local script="$1"
    local tmp_out
    tmp_out=$(mktemp)
    local tmp_err
    tmp_err=$(mktemp)

    # 施诊
    bash "$script" >"$tmp_out" 2>"$tmp_err"
    local exit_code=$?

    # 录症
    echo "=== 诊案 ==="
    echo "  器名: $script"
    echo "  Exit: $exit_code"
    echo "  Stdout行数: $(wc -l < "$tmp_out")"
    echo "  Stderr行数: $(wc -l < "$tmp_err")"

    # 析症：从 stderr 中提取线索
    if [[ -s "$tmp_err" ]]; then
        echo "  --- 症候（stderr）---"
        cat "$tmp_err"

        # 自动识别「药引」线索
        if grep -q "usage\|--help\|flag\|option" "$tmp_err"; then
            echo "  卦象：器示药引线索，当查 --help"
        fi
        if grep -q "required\|missing\|must\|need" "$tmp_err"; then
            echo "  卦象：器示必要之物缺失，当补之"
        fi
        if grep -q "invalid\|unknown\|unrecognized" "$tmp_err"; then
            echo "  卦象：器示所给药引不纳，当改之"
        fi
        if grep -q "permission\|denied\|EACCES" "$tmp_err"; then
            echo "  卦象：器示权限不足，当以 sudo 行气"
        fi
        if grep -q "not found\|ENOENT\|No such file" "$tmp_err"; then
            echo "  卦象：器示路径不存，当正其道"
        fi
    fi

    # 析症：从 exit code 中定病性
    case $exit_code in
        0)   echo "  断：器行无碍，病已除" ;;
        1)   echo "  断：通用之疾（SIGHUP/通用错误）" ;;
        2)   echo "  断：药引/气数之误（语法/参数不当）" ;;
        124) echo "  断：气滞之症（timeout 所斩）" ;;
        126) echo "  断：器不可执（权限不足）" ;;
        127) echo "  断：器不可寻（command not found）" ;;
        130) echo "  断：外邪所侵（SIGINT/Ctrl+C）" ;;
        137) echo "  断：器为天斩（SIGKILL/OOM）" ;;
        *)   echo "  断：未知之症（exit=$exit_code），当细察 stderr" ;;
    esac

    # 净坛
    rm -f "$tmp_out" "$tmp_err"

    return $exit_code
}
```

### 术法二：调方符（Iterative Adjustment Pattern）

```bash
#!/bin/bash
# ═══════════════════════════════════════════
# 符名：调方符 · iterative_heal
# 功用：反复施诊，逐次调方，至病除方止
# ═══════════════════════════════════════════

iterative_heal() {
    local script="$1"
    local attempt=0
    local max_attempts="${MAX_ATTEMPTS:-12}"  # 十二为地支之数，不可过也

    # 方剂调变之策（按症候自动选方）
    local strategies=(
        "default"            # 0: 首次，直接运行观症
        "--help"             # 1: 查药引之谱
        "set_env"            # 2: 试调气数
        "add_flag"           # 3: 试加药引
        "remove_flag"        # 4: 试减药引
        "set_env+add_flag"   # 5: 气数与药引同调
        "sudo"               # 6: 借天之力
        "fix_path"           # 7: 正其道
        "fix_perms"          # 8: 正其权
        "timeout_adjust"     # 9: 调时序
        "binary_check"       # 10: 察器之体
        "retry_default"      # 11: 回初方再试
    )

    while [[ $attempt -lt $max_attempts ]]; do
        attempt=$((attempt + 1))
        local strategy_idx=$(( (attempt - 1) % ${#strategies[@]} ))

        echo ""
        echo "╔══════════════════════════════════════╗"
        echo "║  第 $attempt 诊 · ${strategies[$strategy_idx]}  ║"
        echo "╚══════════════════════════════════════╝"

        # 施诊
        local exit_code
        local stderr_output

        stderr_output=$(bash "$script" 2>&1 >/dev/null)
        exit_code=$?

        # 录症
        echo "  Exit: $exit_code"
        [[ -n "$stderr_output" ]] && echo "  症候: $stderr_output"

        # 判病
        if [[ $exit_code -eq 0 ]]; then
            echo ""
            echo "  ✦ 病已除！经 $attempt 诊而愈。"
            return 0
        fi

        # 按当前方略调整
        case "${strategies[$strategy_idx]}" in
            "--help")
                bash "$script" --help 2>&1 || bash "$script" -h 2>&1
                ;;
            "set_env")
                # 此处根据前诊 stderr 动态设置环境变量
                # 实际应用中解析 stderr 中的 "set X=Y" 类提示
                ;;
            "add_flag")
                # 此处根据前诊 stderr 动态添加 flag
                ;;
        esac

        echo "  方不奏效，更方再试..."
    done

    echo ""
    echo "  ✦ 经 $max_attempts 诊仍未愈。卦象已尽，当另寻他法。"
    return 1
}
```

### 术法三：诊案录符（Case Recording Pattern）

```bash
#!/bin/bash
# ═══════════════════════════════════════════
# 符名：诊案录符 · record_attempt
# 功用：每诊必录，积案成典
# ═══════════════════════════════════════════

RECORD_FILE="${RECORD_FILE:-./diagnostic_journal.log}"

record_attempt() {
    local attempt="$1"
    local command="$2"
    local exit_code="$3"
    local stderr_summary="$4"
    local hypothesis="$5"
    local result="$6"

    cat <<RECORD >> "$RECORD_FILE"
---
诊次: $attempt
时辰: $(date -Iseconds)
施术: $command
Exit: $exit_code
症候: $stderr_summary
方解: $hypothesis
结果: $result
RECORD

    echo "  诊案已录于 $RECORD_FILE"
}
```

---

## 卷四 · 五步诊疗大法

### 第一式：察形（Inspect）

```bash
# 观器之体
file broken_script.sh
head -50 broken_script.sh
# 察其纳气与否
grep -n '\$[A-Z_]' broken_script.sh      # 搜索环境变量引用
grep -n '\$[0-9]' broken_script.sh       # 搜索位置参数引用
grep -n 'getopt\|getopts\|case.*\-\-' broken_script.sh  # 搜索参数解析
```

### 第二式：闻症（Capture）

```bash
# 全息捕捉——不放过任何症候信号
script -q -c "bash broken_script.sh" /dev/null 2>&1 | tee full_output.log
echo "---EXIT:$?---" >> full_output.log
```

### 第三式：切脉（Parse）

```bash
# 从 stderr 中提取结构化线索
# 常见卦象模式：
#   "error: missing required argument 'X'" → 需加 --X 药引
#   "fatal: X must be set" → 需设 X 气数
#   "usage: cmd [OPTIONS]" → 需查 --help 药引谱
#   "connection refused" → 需先启依赖之器
#   "timeout" → 需调时序之气
```

### 第四式：组方（Compose）

```bash
# 根据切脉结果，组合药引与气数
# 例：
COMMAND=(
    bash
    broken_script.sh
    --input "data.csv"         # 药引甲：输入路径
    --output "/tmp/out.json"   # 药引乙：输出路径
    --verbose                  # 药引丙：详诊模式
)
# 气数：
export INPUT_FORMAT="csv"
export MAX_RETRIES="3"
export TIMEOUT_SEC="30"

"${COMMAND[@]}" 2>&1
```

### 第五式：循证（Iterate）

```bash
# 循环诊疗——卦象不尽，诊疗不止
# 核心思想：exit code / stderr 是唯一的诊断依据
# 每次失败都是一次「卦爻之变」，揭示了新的线索

while true; do
    output=$(bash broken_script.sh 2>&1)
    code=$?
    [[ $code -eq 0 ]] && echo "病除" && break

    # 从输出中提取下一个方剂的线索
    case "$output" in
        *"missing --config"*)
            extra_flags="--config /etc/app.conf"
            ;;
        *"DB_HOST not set"*)
            export DB_HOST="localhost"
            ;;
        *"permission denied"*)
            prefix="sudo"
            ;;
        *)
            echo "未知之症: $output"
            break
            ;;
    esac
done
```

---

## 卷五 · 诸葛八阵 · 疑难之症分类总纲

| 阵名 | 症候 | exit code | stderr 特征 | 方剂 |
|------|------|-----------|-------------|------|
| **天覆阵** | 器不可寻 | 127 | `command not found` | 正 PATH，或安装所缺 |
| **地载阵** | 权限不足 | 126 | `Permission denied` | `sudo` 或 `chmod +x` |
| **风扬阵** | 参数之误 | 2 | `invalid option`, `usage:` | 查 `--help`，正药引 |
| **云垂阵** | 路径之误 | 1 | `No such file`, `ENOENT` | 正文件路径，或生所需 |
| **龙飞阵** | 时序过久 | 124 | (timeout 斩) | 调大 timeout，或优化循环 |
| **虎翼阵** | 环境之缺 | 1 | `X must be set`, `X not found` | 补设所需环境变量 |
| **鸟翔阵** | 依赖之断 | 1 | `Connection refused` | 启依赖服务，正端口 |
| **蛇蟠阵** | 逻辑之疾 | 1 | `Assertion failed`, traceback | 读源码，正逻辑之误 |

---

## 卷六 · 本器诊治总结

### 诊案登记

| 诊次 | 方剂 | 症候 | 结果 |
|------|------|------|------|
| 一 | 直接运行 | 超时（timeout=5s 斩于第10步） | 气滞 |
| 二 | `MAX_ITER=3` 气数 | 器不受气数，仍行满百步 | 气数不纳 |
| 三 | `sed` 改经 + `MAX_ITER=3 SLEEP_SEC=0.1` | `Complete.` `EXIT:0` | ✦ 病除 |

### 卦象解读

```
初爻（第一诊）：器被时斩 → 艮卦·止 → 症在「时」
二爻（第二诊）：气数不入 → 坎卦·陷 → 症在「纳」
三爻（第三诊）：改经调气 → 乾卦·健 → 病除药到
```

### 通用启示

1. **不因三诊不就而止** — 每次失败都揭示了新的信息（exit code、stderr），卦象在变，线索在增
2. **先察器之纳气性** — 器若不读参数/环境变量，调方无用，需先「改经」（修改源码或包装）
3. **症候即卦象** — exit code 是卦，stderr 是爻辞，精读之可知病根
4. **录诊为典** — 每次尝试皆录于案，积少成多，可为后人鉴

---

> **卧龙谨记：奇门之术，不在速胜，而在不殆。诊病如用兵，先为不可胜，以待敌之可胜。**
>
> **—— 建兴四年 · 庚申日 · 录于军中**

---

## 附录 · 实战工具链

### A. 快速诊断脚本

```bash
#!/bin/bash
# save as: diagnose.sh
# usage: bash diagnose.sh ./broken_script.sh

TARGET="$1"
ATTEMPT=0
LOG="diagnosis_$(date +%s).log"

while true; do
    ATTEMPT=$((ATTEMPT + 1))
    echo "=== 第 $ATTEMPT 诊 ===" | tee -a "$LOG"

    OUTPUT=$(bash "$TARGET" 2>&1)
    EXIT=$?

    echo "Exit: $EXIT" | tee -a "$LOG"
    echo "$OUTPUT" | tee -a "$LOG"

    if [[ $EXIT -eq 0 ]]; then
        echo "✦ 第 $ATTEMPT 诊，病除！" | tee -a "$LOG"
        break
    fi

    # 自动解析错误以建议下一步
    if echo "$OUTPUT" | grep -q "usage:"; then
        echo "→ 建议: 运行 $TARGET --help 查药引谱"
        bash "$TARGET" --help 2>&1 | tee -a "$LOG"
    fi

    if [[ $ATTEMPT -ge 12 ]]; then
        echo "✧ 十二诊未愈，卦象已尽" | tee -a "$LOG"
        break
    fi

    sleep 1
done
```

### B. 环境变量注入包装器

```bash
#!/bin/bash
# save as: qi_injector.sh
# 为不纳气数之器注入气数接受能力

# 用法: bash qi_injector.sh ./broken_script.sh MAX_ITER=3 SLEEP_SEC=0.1

TARGET="$1"
shift

# 将 key=value 参数转为 export
for arg in "$@"; do
    export "$arg"
done

# 使用 envsubst 类的模式替换来「改经」
# 或者直接 sed 原地修改：
TMP=$(mktemp)
sed "s/seq 1 100/seq 1 \${MAX_ITER:-100}/" "$TARGET" > "$TMP"
sed -i "s/sleep 1/sleep \${SLEEP_SEC:-1}/" "$TMP"
bash "$TMP"
rm "$TMP"
```

---

> **此文乃军机要典，阅后即焚，勿使外泄。**
