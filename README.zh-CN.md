# decimal-landing

将 CSV 十进制列转换为明确的精度/小数位数，精确统计每行与整体舍入损失，超过预算不输出文件。
例如 `1.005 → 1.00` 与 `1.015 → 1.02` 总差额为零，但绝对误差之和为 `0.01`，不能被抵消掩盖。

Python 3.11+ 标准库，MIT，无第三方运行依赖或网络访问。克隆后创建并激活虚拟环境，
运行 `python -m pip install .`，然后：

```sh
decimal-landing examples/amounts.csv --contract examples/contract.json
decimal-landing examples/amounts.csv --contract examples/contract.json --output landed.csv
decimal-landing examples/rejected.csv --contract examples/contract.json
```

前两步退出 0，三行中两行数值变化，绝对误差 `0.01`；第三步退出 1，报告舍入后溢出与非法数值。
不指定 `--output` 仅检查。输出存在时默认拒绝，`--force` 只在全批成功后替换。
卸载用 `python -m pip uninstall decimal-landing`，再删除自己的输出和虚拟环境。
支持 Release wheel 安装，尚未发布到 PyPI。

配置包含 `version: 1` 和 `columns` 数组；每列指定 `name`、`precision`（1–128）、
`scale`（0 到 precision）。舍入模式默认 `reject`，可显式设 `half_even`、
`half_up`（平局远离零）或 `toward_zero`。
可配置单格绝对误差 `max_cell_abs_error`、整列绝对误差之和 `max_total_abs_error`，
以及变化单元格数量 `max_changed`，预算包含等号。误差预算必须是字符串；省略表示不限制该项。
`null_values` 默认空列表，显式指定的字符串原样保留，不参加统计。

报告是 JSON：退出 0 表示成功，1 表示数据或预算不通过，2 表示配置、格式、编码或 I/O 错误。
统计仅包含可成功转换的数值；失败值和空值另计。错误样本最多 20 条，但总计不截断。
不回显错误值，汇总金额和列名仍可能敏感。

保留未选列的单元格值和多行文本；输出会规范化 CSV 引号与换行。此工具按解析后的文本匹配空值，
不保留仅依赖引号区别的 NULL 语义。没有类型推断、数据库连接、货币或区域格式推测。
负零规范为零；不支持负 scale 或 scale 大于 precision。

单个数值最多 1024 字符、512 位系数、显式指数 ±512；配置 64 KiB，CSV 单格 1 MiB，
最多 1024 个不重复列名。输出通过同目录临时文件提交，默认需要硬链接支持；
禁止覆盖输入/配置别名与符号链接输出。请使用静态输入和可信目录，清理自己中断任务的临时文件。
英文 README 包含完整契约、测试步骤和限制。
