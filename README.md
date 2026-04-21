# drissionpage-cli

对 DrissionPage 做 Agent-first CLI 化的最小 v0 实现。

当前提供的首批命令：

- `open`
- `snapshot`
- `find`
- `click`
- `type`

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

## 通用规则

所有命令都统一输出 JSON，至少包含：

- `ok`
- `session`
- `action`
- `data`
- `error`

当前最常用的公共选项：

- `--session`
  - 指定会话名
  - 不传时默认使用 `default`
- `--headless`
  - 使用无头浏览器运行
  - 适合自动化测试和 Agent 调用

最常见的调用形式：

```bash
python -m drissionpage_cli <command> [args] [options]
```

## 命令说明

### 1. `open`

打开一个页面，并返回页面基本信息。

基本用法：

```bash
python -m drissionpage_cli open https://example.com
```

指定 session：

```bash
python -m drissionpage_cli open https://example.com --session demo
```

无头模式：

```bash
python -m drissionpage_cli open https://example.com --session demo --headless
```

本地夹具页示例：

```bash
python -m drissionpage_cli open http://127.0.0.1:8000/index.html --session local-demo --headless
```

### 2. `snapshot`

获取当前页面的结构化快照，返回页面信息和可交互元素列表，并为元素分配 `ref`。

基本用法：

```bash
python -m drissionpage_cli snapshot
```

指定 session：

```bash
python -m drissionpage_cli snapshot --session demo
```

无头模式：

```bash
python -m drissionpage_cli snapshot --session demo --headless
```

典型用途：

```bash
python -m drissionpage_cli open https://example.com --session demo --headless
python -m drissionpage_cli snapshot --session demo --headless
```

### 3. `find`

按 locator 或文本查找元素，返回匹配元素和对应 `ref`。

#### 选项

- `--locator`
  - 用 DrissionPage 原生定位语法查找
- `--text`
  - 按文本内容模糊查找

#### 用 `--locator`

按 id 查找：

```bash
python -m drissionpage_cli find --session demo --locator "#name-input"
```

按标签查找：

```bash
python -m drissionpage_cli find --session demo --locator "tag:a"
```

按文本 locator 查找：

```bash
python -m drissionpage_cli find --session demo --locator "text:Primary Action"
```

带无头模式：

```bash
python -m drissionpage_cli find --session demo --headless --locator "#name-input"
```

#### 用 `--text`

按按钮文案查找：

```bash
python -m drissionpage_cli find --session demo --text "Primary Action"
```

按链接文本查找：

```bash
python -m drissionpage_cli find --session demo --headless --text "More information"
```

### 4. `click`

点击元素。支持 `--ref` 和 `--locator` 两种目标指定方式，其中 `--ref` 优先级更高。

#### 选项

- `--ref`
  - 使用前面 `snapshot` 或 `find` 返回的元素引用
- `--locator`
  - 直接传 DrissionPage locator

#### 用 `--ref`

先找元素，再点击：

```bash
python -m drissionpage_cli find --session demo --headless --text "Primary Action"
python -m drissionpage_cli click --session demo --headless --ref e1
```

#### 用 `--locator`

直接按 locator 点击：

```bash
python -m drissionpage_cli click --session demo --locator "#primary-action"
```

点击链接：

```bash
python -m drissionpage_cli click --session demo --headless --locator "tag:a"
```

### 5. `type`

向输入框或可输入元素写入文本。支持 `--ref` 和 `--locator` 两种目标指定方式。

#### 选项

- `--ref`
  - 使用已返回的元素引用
- `--locator`
  - 直接传 DrissionPage locator
- `--text`
  - 要输入的文本，必填

#### 用 `--ref`

先查输入框，再输入：

```bash
python -m drissionpage_cli find --session demo --headless --locator "#name-input"
python -m drissionpage_cli type --session demo --headless --ref e2 --text "Agentic CLI"
```

#### 用 `--locator`

直接按 locator 输入：

```bash
python -m drissionpage_cli type --session demo --locator "#name-input" --text "hello"
```

无头模式下输入：

```bash
python -m drissionpage_cli type --session demo --headless --locator "#name-input" --text "typed in headless mode"
```

## 常见工作流示例

### 本地页面完整流程

```bash
python -m drissionpage_cli open http://127.0.0.1:8000/index.html --session local-demo --headless
python -m drissionpage_cli find --session local-demo --headless --locator "#name-input"
python -m drissionpage_cli type --session local-demo --headless --ref e1 --text "Agentic CLI"
python -m drissionpage_cli find --session local-demo --headless --text "Primary Action"
python -m drissionpage_cli click --session local-demo --headless --ref e2
python -m drissionpage_cli snapshot --session local-demo --headless
```

### 公网页面简单流程

```bash
python -m drissionpage_cli open https://example.com --session smoke --headless
python -m drissionpage_cli find --session smoke --headless --locator "tag:a"
python -m drissionpage_cli click --session smoke --headless --ref e1
python -m drissionpage_cli snapshot --session smoke --headless
```

## 测试

### 本地回归测试

运行 pytest：

```bash
pytest -q tests/test_cli_local.py
```

运行本地脚本：

```bash
python scripts/test_local_cli.py
```

### 公网 smoke test

启用公网 smoke：

```bash
set DPCLI_RUN_PUBLIC_SMOKE=1
```

运行 pytest：

```bash
pytest -q tests/test_public_smoke.py
```

运行脚本：

```bash
python scripts/test_public_smoke.py
```

### 全量测试

```bash
set DPCLI_RUN_PUBLIC_SMOKE=1
pytest -q tests
```
