# astrbot_plugin_repeat_delete

把 repeat-detect 里「关键词剥离 + 重复折叠」那套脚本搬进 AstrBot，
用一条 `/think tidy` 直接改写**当前会话**对话记录里的 think 片段。

- 不改回复、不改用户消息、不动 think 以外的任何字段
- 写回前会自动备份原始对话记录
- 清理力度、关键词、相似度阈值全部在插件配置面板里调

## 原理

AstrBot 会把模型的思考内容按
`{"type": "think", "think": "...", "encrypted": "..."}` 的形式存进对话历史。
本插件读取当前会话的对话记录，只针对这些 think part 依次执行：

1. **关键词剥离**
   - 一整行/一整段只有关键词（如 `好`、`好了`、`输出`）→ 整段消失
   - 关键词夹在句子里 → 只删词，并把断句后多余的逗号、空格整理掉
   - 英文关键词要求单词边界（`ok` 不会误伤 `okay`）
   - 单字中文关键词要求后接标点（`好` 不会误伤 `好像`）
2. **重复折叠**
   - 同一段落在一个 think 块内最多保留 N 次（默认 2）
   - 完全相同的段落一律判重
   - 长度不超过阈值（默认 40 字）的段落用归一化编辑距离比较，
     相似度 ≥ 阈值（默认 0.80）且长度相差不超过比例（默认 0.35）时判为同一段的不同写法
3. **行级折叠**（本插件新增，默认开启）
   - think 里常见的「一行一句、反复刷屏」按行折叠，同一行默认只留 1 次
4. **句子级折叠**（可选，默认关闭）
   - 处理单个长段落里被反复粘贴的同一句话

处理逻辑与 `repeat-detect/clean.py` 一致：在参考数据集上 108 个 think 块从
57616 字压到 17935 字（-68.9%），耗时约 0.04 秒。

## 使用

| 命令 | 说明 |
| --- | --- |
| `/think tidy` | 清理本会话历史里的 think 并写回 |
| `/think tidy 30` | 只处理最近 30 条消息 |
| `/think preview` | 只统计不写入，先看看会删掉多少 |
| `/think preview 30` | 同上，限定最近 30 条 |
| `/think` | 查看子命令列表 |

中文别名同样可用：`/think 整理`、`/think 预览`；指令组别名 `THINK`。

返回示例：

```
🧹 think 整理完成
对话 1a2b3c4d ｜ 消息 120 条（扫描 118）
think 块 108 个 ｜ 改写 106 个
字数 57616 → 17935（-39681，68.9%）
关键词 6823 处 ｜ 删段 4156 ｜ 删行 11
删除最多的块：
  #47 3335 → 296（-3039）…
备份：…/plugin_data/astrbot_plugin_repeat_delete/backup/xxx/20260101-120000_1a2b3c4d.json
耗时 0.42s
```

## 配置

配置面板按「运行开关 / 关键词 / 判重参数 / 范围与安全」分组。

### 运行开关

| 项 | 说明 |
| --- | --- |
| `enabled` | 总开关 |
| `admin_only` | 仅管理员可用（群聊建议开启） |
| `session_mode` / `sessions` | 全部会话 / 仅白名单 / 排除黑名单，配合 UMO 选择器 |
| `dry_run` | 开启后 `/think tidy` 也只预览不写回 |
| `backup_before_write` / `backup_limit` | 写回前备份原始历史，并按份数轮转 |
| `report_top_blocks` | 报告里展示几条「删除最多」的块 |

### 关键词

| 项 | 默认值 | 说明 |
| --- | --- | --- |
| `keywords` | 见下 | 关键词列表 |
| `keyword_regex` | `[]` | 额外正则，用于列表覆盖不到的模式，非法正则自动忽略 |
| `ignore_case` | `true` | 英文关键词忽略大小写 |
| `anchor_single_char` | `true` | 单字关键词必须后接标点才算噪声 |
| `drop_keyword_only_paragraphs` | `true` | 整段只有关键词时删除整段 |
| `strip_inline_keywords` | `true` | 删除句中夹带的关键词 |

默认关键词（与 `clean.py` 的 `KW` 一致）：
`避免句号结尾`、`避免整齐结构`、`避免句号`、`避免整齐`、`用上面那句`、`用这个`、
`就这样`、`好了`、`够了`、`完全`、`输出`、`ok`、`end`、`好`、`用`

> 「好」「用」是单字关键词，默认只在 `好，` `用。` 这类位置命中；
> 想更激进可把 `anchor_single_char` 关掉，但要小心「好像」「使用」被误伤。

### 判重参数

| 项 | 默认值 | 对应 `clean.py` |
| --- | --- | --- |
| `max_paragraph_repeats` | `2` | `KEEP` |
| `near_duplicate.enabled` | `true` | 使用近似判重 |
| `near_duplicate.max_chars` | `40` | `NEAR_MAXLEN` |
| `near_duplicate.threshold` | `0.80` | `NEAR_THRESH` |
| `near_duplicate.length_ratio` | `0.35` | `LEN_RATIO` |
| `dedupe_lines` | `true` | 新增：行级折叠 |
| `max_line_repeats` | `1` | 新增：同一行保留次数 |
| `dedupe_sentences` | `false` | 新增：句子级折叠 |
| `max_sentence_repeats` | `1` | 新增：同一句保留次数 |

### 范围与安全

| 项 | 默认值 | 说明 |
| --- | --- | --- |
| `roles` | `["assistant"]` | 处理哪些角色的消息（think 只出现在 assistant） |
| `max_messages` | `0` | 默认只处理最近 N 条，0 表示全部 |
| `max_block_chars` | `0` | 单块字数上限，超长块跳过，0 表示不限 |
| `drop_empty_think` | `true` | 清洗后为空的 think 块从消息里摘掉（至少保留一个 part） |
| `clear_thinking_signature` | `true` | 内容改动后清除 `encrypted` 思考签名 |

> Anthropic 等供应商的思考签名与原文绑定，内容一改签名就失效，所以默认清除。
> 若你的模型要求必须回传签名，请关闭 `clear_thinking_signature`。

## 注意

- 只动**当前会话当前对话**的历史；其它会话、其它对话不受影响。
- 命令通过 AstrBot 的会话锁与 LLM 请求串行，避免写回被随后的历史保存覆盖；
  等待 20 秒仍拿不到锁时会记录告警并在未加锁的情况下写入。
- 写回后 AstrBot 会把清理后的 think 一并作为上下文发给模型，
  因此清理只能减少噪音，不能「让模型忘记」已经发生的对话内容。
- 建议先用 `/think preview` 观察一次再正式执行。

## 目录结构

```
astrbot_plugin_repeat_delete/
├── main.py              # 插件入口：/think 指令组
├── core/cleaner.py      # 纯文本清洗引擎（关键词 + 判重）
├── core/config.py       # 配置面板 -> 强类型设置
├── core/history.py      # 对话历史读取、think 改写、备份
├── _conf_schema.json    # 配置面板 schema
└── tests/               # 无需 AstrBot 运行时即可运行的测试
```

## 测试

```bash
python tests/test_cleaner.py   # 清洗规则
python tests/test_plugin.py    # 指令流程、备份、配置解析（使用 astrbot 替身）
```

## 兼容性

AstrBot `>=4.22.0, <5`。思考内容以 `think` content part 形式落盘的版本均可使用。
