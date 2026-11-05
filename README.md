# 网易叭哥说历史统计导入工具

从闪电说换到网易叭哥说，想接着计算以前的输入字数、使用次数和活跃天数？这个非官方命令行工具把每日统计整理为规范 JSON，再和一份叭哥说每日统计样例合并，预览使用时长和历史积累。

本仓库与网易、叭哥说和闪电说无关联。本次验证的叭哥说 Mac 客户端统计页没有官方导入入口。我们曾在一台 Mac 上通过定制客户端统计展示层，验证了历史汇总与新增用量持续相加；[本机客户端原型记录](docs/LOCAL_CLIENT_PROTOTYPE.md)介绍其做法和限制。这里提供的可运行工具负责离线校验、标准化和合并预览，输出 JSON；它本身不会修改已安装的叭哥说应用，也不会上传统计。

English: **Unofficial NetEase Bageshuo / Shandianshuo dictation history import**. Local CSV/JSON migration and usage-statistics merging, with a documented macOS client prototype. No official cloud import or supported client installer is provided.

## 试跑

需要 Python 3.10+ 和系统 IANA 时区数据库，macOS 和常见 Linux 发行版可直接运行。样例中的日期和数字均为虚构。

```sh
python3 bageshuo_history_import.py normalize examples/shandianshuo.csv
python3 bageshuo_history_import.py merge --native examples/bageshuo-native.json examples/shandianshuo.csv
```

`normalize` 校验 CSV 或 JSON，并输出可保存的规范 JSON。加上 `> history.json` 可把输出保存成文件。`merge` 单独列出每个来源，并给出合计。样例合计 2,600 字符、17 次、3 个活跃日，最长连续 3 天，记录的录音时长是 260 秒。

## 准备自己的数据

按 [CSV 样例](examples/shandianshuo.csv) 或 [JSON 样例](examples/bageshuo-native.json) 整理 UTF-8 文件。每批只放一个工具来源，每天一行。叭哥说样例的 `source` 用 `bageshuo`，闪电说用 `shandianshuo`。同一个来源始终用同一个标识。

- 元数据包含版本 `schema_version`（当前为 1）、来源 `source`、IANA 时区 `time_zone` 和两项统计口径。CSV 在每行重复这些字段，JSON 把每天的数据放入 `days`。
- 每日字段为 `date`（YYYY-MM-DD）、`characters`（字符数）、`sessions`（次数），可选 `recording_seconds`（实际录音秒数）。统计值用非负整数，未知录音时长留空或填 JSON `null`。
- `character_rule` 支持 `final_text_code_points`（最终提交文本的 Unicode 码点数，含标点和空白）和 `final_text_code_points_without_whitespace`（排除 Python `str.isspace()` 认定的空白）。
- `session_rule` 支持 `completed_dictations`（完成的语音输入次数）和 `recordings_started`（开始录音的次数）。按各来源实际口径填写，口径未知时先保留该来源的原始统计。

合并要求时区和两项口径完全相同。同一份规范化批次只计一次，CSV 和 JSON 的行顺序差异也能识别。同一来源的不同批次若日期重叠，工具会报错，请先核对并保留每一天的一份记录。不同来源在同一天的字符数和次数可相加，活跃日只计一天。活跃日指至少有一次使用的日期。

录音时长与预计节省时间分别输出。默认只显示录音统计，缺少活跃日的录音时长时显示已知小计。提供 `--typing-chars-per-minute 60` 等自定义键盘输入基线且录音数据完整时，工具才按「键盘输入估时减录音时长，最低为零」计算预计节省秒数。这是工具的估算，叭哥说的官方估算口径由客户端定义。

## 测试与许可

```sh
python3 -m unittest discover -s tests -v
```

源码采用 [MIT 许可](LICENSE)。
