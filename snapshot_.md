# Snapshot 产物与 Agent 视图报告

**生成时间**: 2026-04-25  
**版本**: dp_cli v0.5  
**场景**: 分析 `snapshot` 命令产生的数据结构，以及 Agent/LLM 实际看到的内容

---

## 1. Snapshot 命令的两种产物

当你运行 `python -m dp_cli snapshot --session <name>` 时，会产生**两份**产物：

### 1.1 CLI 标准输出（STDOUT）—— Agent 直接消费

这是实时返回的 JSON，被 Agent 程序直接读取：

```json
{
  "ok": true,
  "session": "test-hybrid",
  "action": "snapshot",
  "data": {
    "schema_version": "0.5",
    "mode": "agent_summary",
    "page": {
      "url": "https://www.libvio.mov/",
      "title": "LIBVIO",
      "tab_id": "F364F669AA44BA6FBF4B75F113DA5A1E"
    },
    "page_identity": {
      "runtime_id": "rt_9c1c0ba3ef7c",
      "page_id": "page_92003c64b00e",
      "snapshot_id": "snap_6d6ecda7bbb9",
      "snapshot_seq": 1
    },
    "scope": "page",
    "root_ref": null,
    "depth": null,
    "summary": {
      "global_actions": [...],
      "visible_focus": [...],
      "repeated_regions": [...]
    },
    "recovery": {...},
    "planner_view": {
      "pinned_controls": [...],
      "viewport_nodes": [...],
      "condensed_groups": [...],
      "stats": {...},
      "omitted_summary": {...}
    }
  }
}
```

**特点**:
- 不含完整 DOM 节点列表（nodes 被省略以节省 token）
- 核心内容在 `planner_view` 中
- 这是 Agent 做决策的唯一信息来源

### 1.2 磁盘产物 —— `.dpcli/snapshots/<session>/<snapshot_id>.json`

这是**完整快照**，包含所有原始节点数据，用于调试和复盘：

```
.dpcli/snapshots/test-hybrid/snap_6d6ecda7bbb9.json
```

**特点**:
- 包含 `nodes` 数组（178 个完整节点记录）
- 包含 `planner_view`（与 CLI 输出一致）
- 包含 `groups`（压缩分组元数据）
- 包含 `recovery`（截断恢复信息）
- 文件大小通常是 CLI 输出的 10~50 倍

---

## 2. 单个节点的完整字段（nodes 数组中的元素）

以 libvio 首页的一个 header 容器为例：

```json
{
  "ref": "r1",
  "ref_type": "container",
  "id": "",
  "tag": "header",
  "role": "banner",
  "name": "播放记录",
  "text": "关于 首页 电影 剧集 动漫 日韩剧 欧美剧",
  "value": "",
  "placeholder": "",
  "href": "",
  "input_type": "",
  "title": "",
  "aria_label": "",
  "alt": "",
  "label": "",
  "locator": "xpath:/html/body[1]/div[1]/div[1]/div[1]/div[1]/header[1]",
  "depth": 5,
  "bounds": {
    "x": 77,
    "y": 30,
    "width": 1090,
    "height": 50
  },
  "visibility": {
    "visible": true,
    "in_viewport": true,
    "interactable_now": false
  },
  "context": {
    "landmark": "播放记录",
    "heading": "",
    "form": "",
    "list": "",
    "dialog": ""
  },
  "states": {
    "disabled": false,
    "checked": false,
    "selected": false,
    "expanded": false
  },
  "xpath": "/html/body[1]/div[1]/div[1]/div[1]/div[1]/header[1]",
  "parent_ref": null,
  "parent_xpath": null,
  "session_id": "sess_d90070fe07ce",
  "runtime_id": "rt_9c1c0ba3ef7c",
  "page_id": "page_92003c64b00e",
  "snapshot_id": "snap_6d6ecda7bbb9",
  "url": "https://www.libvio.mov/",
  "fingerprint": "3aacd6ea6dabc288",
  "locator_candidates": [
    "role=banner[name='播放记录']",
    "text='关于 首页 电影 剧集 动漫 日韩剧 欧美剧'"
  ]
}
```

### 字段分类

| 类别 | 字段 | 说明 |
|------|------|------|
| **身份** | `ref`, `ref_type` | `r*` = 容器, `e*` = 元素；由 RuntimeContext 分配 |
| **DOM** | `tag`, `id`, `role` | HTML 标签、id 属性、ARIA/隐式角色 |
| **文本** | `name`, `text`, `value`, `placeholder`, `label`, `aria_label`, `alt`, `title` | 可访问名称和可见文本 |
| **链接** | `href` | 仅对 `<a>` 元素 |
| **表单** | `input_type` | input 的 type 属性（text/password/submit...） |
| **定位** | `locator`, `xpath`, `locator_candidates` | 可用于 DrissionPage 定位元素 |
| **布局** | `bounds` | `{x, y, width, height}` 视口坐标 |
| **可见性** | `visibility` | `visible`, `in_viewport`, `interactable_now` |
| **上下文** | `context` | `landmark`, `heading`, `form`, `list`, `dialog` |
| **状态** | `states` | `disabled`, `checked`, `selected`, `expanded` |
| **层级** | `depth`, `parent_ref`, `parent_xpath` | DOM 深度和父子关系 |
| **指纹** | `fingerprint` | 节点内容指纹，用于跨快照识别同一元素 |

---

## 3. Planner View 结构（Agent 看到的页面摘要）

`planner_view` 是专门为 LLM 设计的**低 Token 页面摘要**，包含四个核心区域：

### 3.1 pinned_controls（置顶控制项）

**什么会被标记为 pinned**:
- 分页控件（下一页、上一页、页码）
- 表单主操作（搜索按钮、提交按钮、登录按钮）
- 导航控件（顶部导航链接）
- 处于选中/展开状态的元素

**示例**:
```json
{
  "ref": "e7",
  "ref_type": "element",
  "role": "link",
  "name": "首页",
  "text": "首页",
  "id": "",
  "depth": 10,
  "visibility": {"visible": true, "in_viewport": true, "interactable_now": true},
  "context": {"landmark": "播放记录", "heading": "", "form": "", "list": "list", "dialog": ""},
  "states": {"disabled": false, "checked": false, "selected": false, "expanded": false}
}
```

**Agent 应该**: 优先检查 pinned_controls 中是否有目标元素，直接用 ref 交互。

### 3.2 viewport_nodes（视口内节点）

当前可见在视口内的节点（排除了 pinned 和 condensed 成员）：

```json
{
  "ref": "r1",
  "ref_type": "container",
  "role": "banner",
  "name": "播放记录",
  "text": "关于 首页 电影...",
  "depth": 5,
  "visibility": {...},
  "context": {...},
  "states": {...}
}
```

**Agent 应该**: 浏览这些节点来理解页面结构，寻找交互目标。

### 3.3 condensed_groups（压缩分组）

对重复结构的压缩表示，大幅减少 token：

```json
{
  "ref": "e1",
  "ref_type": "container",
  "role": "link",
  "compressed": true,
  "count": 5,
  "member_refs": ["e1", "e2", "e3"],
  "xpath_template": "/html/body[1]/div[1]/.../a[{i}]"
}
```

**Agent 应该**: 用 `expand` 命令展开分组以查看内部细节。

### 3.4 stats + omitted_summary（统计与省略信息）

```json
{
  "stats": {
    "total_nodes": 178,
    "total_elements": 169,
    "total_containers": 9,
    "pinned_control_count": 1,
    "viewport_node_count": 70,
    "condensed_group_count": 7
  },
  "omitted_summary": {
    "omitted_node_count": 100,
    "omitted_element_count": 95,
    "omitted_container_count": 5
  }
}
```

---

## 4. Agent（LLM）实际看到的内容

在 `test_agent_computor.py` 中，`compact_state()` 方法将 snapshot 数据压缩成 LLM Prompt 中的 `Current state`：

### 4.1 原始 compact_state（修改前）

```json
{
  "url": "https://www.libvio.mov/",
  "title": "LIBVIO",
  "node_count": 178,
  "no_groups_hint": "No groups found. Use 'eval' skill with document.querySelectorAll to extract data."
}
```

**问题**: LLM 看不到任何可用的 ref，只能盲目使用 `eval`。

### 4.2 改进后的 compact_state（当前版本）

```json
{
  "url": "https://www.libvio.mov/",
  "title": "LIBVIO",
  "node_count": 178,
  "available_refs": [
    {"ref": "e7", "type": "element", "role": "link", "name": "首页", "text": "首页"},
    {"ref": "r1", "type": "container", "role": "banner", "name": "播放记录", "text": "关于 首页..."},
    {"ref": "e4", "type": "element", "role": "link", "name": "link", "text": ""}
  ],
  "refs_hint": "Use these refs directly with click/type. DO NOT use eval for basic interactions.",
  "groups": [...]
}
```

**改进**: LLM 现在能直接看到可用的 `e*` / `r*` ref，无需盲猜 CSS 选择器。

---

## 5. Snapshot 三种模式对比

| 模式 | CLI 输出包含 | 用途 | Token 量 |
|------|-------------|------|---------|
| `agent_summary` | planner_view + summary + recovery | Agent 自动化决策 | 低 |
| `full` | 全部 nodes + groups + recovery | 调试、完整分析 | 高 |
| `extract` | planner_view（作为 summary） | 数据提取导向 | 中 |

---

## 6. Ref 系统规则

### 6.1 Ref 前缀含义

| 前缀 | 含义 | 可用命令 |
|------|------|---------|
| `e*` | 元素（可交互） | `click`, `type`, `find`, `snapshot` |
| `r*` | 容器/分组 | `expand`, `list-items`, `extract`, `snapshot` |

### 6.2 Ref 生命周期

- Ref 绑定到 `(runtime_id, page_id)`
- 页面导航后，旧 ref 会变为 `ref_stale`
- 需要重新 `snapshot` 获取新 ref

### 6.3 典型工作流

```
1. snapshot → 获取 available_refs
2. 目标在 available_refs 中？→ 直接用 e* 点击/输入
3. 目标不在？→ find --text "关键词" → 获取新 ref
4. type --ref eXX --text "内容"
5. click --ref eYY
6. 页面变化 → snapshot（获取新 ref）
```

---

## 7. 常见误区与正确做法

### 误区 1: 用 eval 做基础交互

```bash
# 错误 —— 导致 SystemExit: 1
python -m dp_cli eval "document.querySelector('input').value = 'xxx'"
```

**原因**: `eval` 的 JS 被当作单表达式执行，多语句会语法错误。

**正确**:
```bash
python -m dp_cli find --text "搜索"
python -m dp_cli type --ref e12 --text "xxx"
```

### 误区 2: 忽略 available_refs

Agent 不检查 `available_refs` 就直接用 `find` 或 `eval` 找元素，浪费步骤。

**正确**: 先检查 `pinned_controls` 和 `viewport_nodes` 中的可用 ref。

### 误区 3: 页面导航后复用旧 ref

点击导致页面跳转后，旧 `e*` ref 会失效。

**正确**: 导航后必须重新 `snapshot` 获取新 ref。

---

## 8. 总结

| 问题 | 答案 |
|------|------|
| Snapshot 产物在哪里？ | CLI STDOUT（Agent 消费）+ `.dpcli/snapshots/`（磁盘存档） |
| Agent 看到什么？ | `planner_view`（pinned + viewport + condensed） |
| LLM Prompt 中展示什么？ | `compact_state`（url, title, available_refs, groups） |
| 完整节点在哪？ | 仅在 `full` 模式 CLI 输出和磁盘产物中 |
| 为什么不要 eval 填表单？ | eval 只支持单表达式，且违背了 ref 驱动设计 |
| 推荐工作流？ | snapshot → find（如需）→ type/click by ref → snapshot（页面变化后） |
