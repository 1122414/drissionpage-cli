# dp-cli

面向 Agent 的 DrissionPage CLI 最小实现。当前版本已经完成 MVP 和 v0.1 会话身份机制，提供稳定的基础浏览器动作闭环：

- `open`
- `snapshot`
- `find`
- `click`
- `type`
- `session inspect`

所有命令统一输出 JSON，适合人手动调用，也适合作为 Agent 的执行边界。

## 环境准备

```bash
conda activate dp-cli
```

如果还没有创建环境：

```bash
conda create -n dp-cli python=3.11
conda activate dp-cli
pip install DrissionPage pytest
```

## 项目结构

```text
dp_cli/
  cli.py            # CLI 参数解析与统一 JSON 输出
  service.py        # 命令用例编排
  session.py        # 会话管理与浏览器恢复
  session_store.py  # session 元数据与状态持久化
  runtime.py        # runtime/page/ref 生命周期
  adapter.py        # DrissionPage 适配层
  models.py         # 数据模型与常量
  errors.py         # 结构化错误
tests/
  support.py        # 测试与脚本共享工作流
  test_cli_local.py
  test_public_smoke.py
scripts/
  test_local_cli.py
  test_public_smoke.py
```

## JSON 输出约定

所有命令至少返回以下字段：

- `ok`
- `session`
- `action`
- `data`
- `error`

失败时 `error` 会包含：

- `code`
- `message`
- `details`

## Session / Runtime / Page / Ref

当前版本会把 session 状态持久化在：

```text
.dpcli/sessions/<session-name>/
```

其中：

- `session`
  - 用户传入的逻辑会话名，例如 `demo`
- `session_id`
  - session 的稳定内部标识
- `runtime_id`
  - 当前浏览器实例标识，用来判断是不是同一个活会话
- `page_id`
  - 当前页面身份
- `snapshot_id`
  - 当前快照代际
- `ref`
  - 由 `snapshot` / `find` 产出的元素引用，只在对应 runtime/page 上有效

如果页面或 runtime 已经变化，旧 `ref` 会返回 `ref_stale`，而不是静默误用。

## 常用选项

- `--session`
  - 指定会话名，不传时默认使用 `default`
- `--headless`
  - 使用无头浏览器执行

通用调用形式：

```bash
python -m dp_cli <command> [args] [options]
```

## 命令示例

### open

```bash
python -m dp_cli open https://example.com
python -m dp_cli open https://example.com --session demo
python -m dp_cli open https://example.com --session demo --headless
```

### snapshot

```bash
python -m dp_cli snapshot --session demo
python -m dp_cli snapshot --session demo --headless
```

### find

按 locator 查找：

```bash
python -m dp_cli find --session demo --locator "#name-input"
python -m dp_cli find --session demo --locator "tag:a"
```

按文本查找：

```bash
python -m dp_cli find --session demo --text "Primary Action"
python -m dp_cli find --session demo --headless --text "Learn more"
```

### click

先查找再点击：

```bash
python -m dp_cli find --session demo --headless --text "Primary Action"
python -m dp_cli click --session demo --headless --ref e1
```

直接按 locator 点击：

```bash
python -m dp_cli click --session demo --locator "#primary-action"
python -m dp_cli click --session demo --headless --locator "tag:a"
```

### type

先查找再输入：

```bash
python -m dp_cli find --session demo --headless --locator "#name-input"
python -m dp_cli type --session demo --headless --ref e1 --text "Agentic CLI"
```

直接按 locator 输入：

```bash
python -m dp_cli type --session demo --locator "#name-input" --text "hello"
python -m dp_cli type --session demo --headless --locator "#name-input" --text "typed in headless mode"
```

### session inspect

```bash
python -m dp_cli session inspect --session demo
python -m dp_cli session inspect --session demo --headless
```

## 手工 smoke 入口

`scripts/` 保留为人工执行入口，但真实流程只维护一份，复用的是 `tests/support.py` 里的共享 workflow。

本地闭环：

```bash
python scripts/test_local_cli.py
```

公网 smoke：

```bash
python scripts/test_public_smoke.py
```

## 测试

本地回归：

```bash
pytest -q tests/test_cli_local.py
```

全部测试：

```bash
pytest -q tests
```

启用公网 smoke：

```bash
set DPCLI_RUN_PUBLIC_SMOKE=1
pytest -q tests/test_public_smoke.py
```
