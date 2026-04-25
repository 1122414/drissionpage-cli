# dp_cli Snapshot 分层索引设计方案

**文档版本**: v0.2  
**日期**: 2026-04-25  
**状态**: 设计确认阶段

---

## 一、核心原则

### 1.1 两个铁律

**铁律一：不给 Agent 看值为空的 JSON 字段**

空字符串 `""`、空数组 `[]`、空对象 `{}`、null 值——这些对 Agent 都是纯噪声，直接浪费 token。输出前必须逐层过滤，只保留有实际意义的字段。

示例：
```json
// 错误 —— 大量空字段浪费 token
{
  "ref": "e1",
  "tag": "div",
  "role": "",
  "name": "",
  "text": "",
  "value": "",
  "placeholder": "",
  "href": "",
  "title": "",
  "aria_label": "",
  "alt": "",
  "label": ""
}

// 正确 —— 只保留有值的字段
{
  "ref": "e1",
  "tag": "div"
}
```

**铁律二：不要替 Agent 决定什么重要**

当前架构最大的错误不是"token 压缩策略不好"，而是**从源头就过滤了节点**，然后又在后端用"智能算法"决定哪些该给 Agent 看。这相当于餐厅不给你菜单，只给你推荐菜。

正确做法：**采集所有可见元素，按语义分层压缩表示，让 Agent 自己决定关注什么。**

---

## 二、问题诊断

### 2.1 当前三层过滤漏斗

```
Layer 1: adapter.py JS 采集
  └─ 只采集 isSemanticContainer || isInteractiveNode
  └─ table 行、div、span → 不采集 ❌
  
Layer 2: service.py planner_view
  └─ viewport_nodes 只保留 in_viewport = true
  └─ 视口外节点 → 被 omitted ❌
  
Layer 3: compact_state
  └─ pinned 取前8 + viewport 取前12
  └─ LLM 最终看到约 20 个 ref ❌
```

### 2.2 根因

SNAPSHOT_SCRIPT 的过滤逻辑导致**普通元素根本不会进入 nodes 数组**。后端 planner_view 做得再好也是无米之炊。

---

## 三、设计方案：分层语义索引 (HSI)

### 3.1 核心思想

不要过滤节点，而是压缩表示。给 Agent 完整目录，让它自己挑。

### 3.2 采集层：所有可见元素，分层标记

- **采集范围**：所有 `visible = true` 的元素
- **不采集**：`display:none`、`visibility:hidden`、`opacity:0`、零尺寸元素
- **标记**：每个节点标记 `semantic_level`（`surface` 或 `deep`）

`surface` 标记规则（满足任一）：
1. `interactable_now = true`（按钮、链接、输入框等）
2. `in_viewport = true` 且属于语义容器（banner、navigation、search、main 等）
3. `in_viewport = true` 且 `depth <= 3`（靠近根节点的结构层）
4. `role` 为 table、list、grid 等数据容器（即使不在视口内，也标记 surface 以便 Agent 展开）

其余可见节点标记为 `deep`。

**分层互斥原则**：
- `surface_index` 和 `deep_index` **互斥**，合起来构成完整可见节点全集
- surface 节点（约 30%）出现在 surface_index（完整字段）
- deep 节点（约 70%）出现在 deep_index（精简字段）
- 不存在某个节点同时出现在两个索引中

### 3.3 输出结构

`agent_summary` 模式输出：

```json
{
  "ok": true,
  "session": "demo",
  "action": "snapshot",
  "data": {
    "schema_version": "0.6",
    "mode": "agent_summary",
    "page": {"url": "...", "title": "..."},
    "page_identity": {"runtime_id": "...", "page_id": "..."},
    "index": {
      "interactable_elements": [
        {"ref": "e12", "role": "textbox", "name": "搜索"},
        {"ref": "e13", "role": "button", "name": "搜索按钮"}
      ],
      "surface_index": [
        {
          "ref": "e12",
          "tag": "input",
          "role": "textbox",
          "name": "搜索",
          "parent_ref": "r5",
          "in_viewport": true,
          "interactable_now": true,
          "child_count": 0
        }
      ],
      "deep_index": [
        {"ref": "e1", "tag": "div", "in_viewport": true},
        {"ref": "e130", "tag": "td", "text": "某行数据", "parent_ref": "r3", "in_viewport": false}
      ],
      "tree": {
        "roots": ["r1", "r2", "r3"],
        "parent_map": {"e1": "r1", "e130": "r3"},
        "children_map": {"r1": ["e1"], "r3": ["e130", "e131"]}
      },
      "stats": {
        "total_nodes": 180,
        "surface_count": 45,
        "in_viewport": 45,
        "offscreen": 135,
        "interactable_now": 12
      }
    }
  }
}
```

**注意**：所有空值字段已被过滤。例如 `role: ""` 不会出现在输出中。

### 3.4 索引分层说明

#### interactable_elements（可交互元素快捷列表）

Agent 想点击/输入时的第一选择。只包含当前可交互的元素（`interactable_now = true`）。

字段：`ref`, `role`, `name`。极简，3-4 个字段。

#### surface_index（表层索引）

Agent 快速浏览页面结构时使用。包含所有 `semantic_level = surface` 的节点。

字段：`ref`, `tag`, `role`, `name`, `text`, `parent_ref`, `in_viewport`, `interactable_now`, `child_count`。

`child_count` 提示容器规模，Agent 看到 `child_count: 150` 就知道这是一个大数据表。

#### deep_index（深层索引）

Agent 搜索/追溯时使用。包含**所有非 surface 的可见节点**，与 surface_index 互斥。

字段：`ref`, `tag`, `role`, `name`, `text`, `parent_ref`, `in_viewport`。

`name` 和 `text` 截断到 40/60 字符，避免超长文本浪费 token。

#### tree（层级关系）

Agent 决定"展开哪个容器"时使用。

- `roots`: 顶层容器 ref 列表
- `parent_map`: {child_ref: parent_ref}
- `children_map`: {parent_ref: [child_ref, ...]}

### 3.5 空值过滤规则

在序列化为 JSON 之前，对每一个节点字典执行过滤：

```python
def filter_empty_fields(obj: dict) -> dict:
    result = {}
    for key, value in obj.items():
        if value is None:
            continue
        if value == "":
            continue
        if value == []:
            continue
        if value == {}:
            continue
        if isinstance(value, dict):
            filtered = filter_empty_fields(value)
            if filtered:
                result[key] = filtered
        elif isinstance(value, list):
            filtered_list = [filter_empty_fields(item) if isinstance(item, dict) else item for item in value if item not in (None, "", [], {})]
            if filtered_list:
                result[key] = filtered_list
        else:
            result[key] = value
    return result
```

**效果**：
- `role: ""` → 不出现
- `name: ""` → 不出现
- `parent_ref: null` → 不出现
- `text: ""` → 不出现
- `value: "", placeholder: "", href: "", title: "", aria_label: "", alt: "", label: ""` → 全部不出现

---

## 四、移除的字段

以下字段从 `agent_summary` 输出中**直接移除**：

| 字段 | 移除原因 |
|------|---------|
| `summary` | 替 Agent 决定"global_actions"、"visible_focus"，违背"不给 Agent 做决定"原则 |
| `recovery` | "truncated_regions"、"expand_candidates" 是后端推测的 Agent 需求 |
| `planner_view` | pinned_controls、viewport_nodes、condensed_groups 是后端定义的"重要性"分区 |
| `omitted_summary` | 既然不再 omit 节点，此字段无意义 |

`full` 模式保留 `nodes` 数组（完整字段），用于调试和存档。

---

## 五、Token 估算

以 libvio 首页（约 180 个可见节点）为例：

| 索引 | 节点数 | 单节点大小 | 总字符 | Token |
|------|--------|-----------|--------|-------|
| `interactable_elements` | ~12 | 40 字 | 480 | ~120 |
| `surface_index` | ~45 | 100 字 | 4,500 | ~1,125 |
| `deep_index` | ~135 | 60 字 | 8,100 | ~2,025 |
| `tree` | - | - | ~1,500 | ~375 |
| `stats` | - | - | ~200 | ~50 |
| **总计** | **180** | **-** | **~14,780** | **~3,695** |

对比：
- 当前 `agent_summary`（planner_view）：~4,000 token（但节点不全）
- 当前 `full` 模式：~18,000 token
- **新方案**：~4,370 token（节点全，接近 agent_summary 的 token 量）

---

## 六、Agent 工作流

```text
Step 1: snapshot → 获取 index
Step 2: 检查 interactable_elements
  └─ 有目标？→ click/type by ref
Step 3: 检查 surface_index
  └─ 有目标？→ click/type by ref
Step 4: 目标不在 surface？→ find --text "关键词"
  └─ find 搜索全量 DOM（不依赖 CLI 输出的 index）
Step 5: 看到大容器（child_count > 50）？→ expand <ref>
  └─ 展开后获得该容器的完整子节点
Step 6: 还是找不到？→ eval（最后手段）
```

**关键变化**：Agent 不再被 planner_view 的"智能分区"误导，而是有完整的目录可用。

---

## 七、设计决策记录

### 7.1 为什么不用 planner_view？

planner_view 的问题：
1. **替 Agent 决定重要性**：pinned_controls、viewport_nodes、condensed_groups 都是后端算法定义的"重要"，Agent 被动接受
2. **遗漏节点**：omitted_summary 告诉 Agent"有 100 个节点被省略了"，但不告诉它具体是什么
3. **误导性强**：Agent 看到 20 个 ref 就以为页面只有这些元素

### 7.2 为什么保留分层索引而不是直接给全量 nodes？

全量 nodes（full 模式）的问题：
1. **字段太多**：每个节点 20+ 字段，token 爆炸
2. **信息密度低**：大量空字段和冗余信息
3. **Agent 阅读困难**：200 个完整节点让 LLM 难以聚焦

分层索引的折中：
- `interactable_elements`：极简，Agent 第一眼看到可操作项
- `surface_index`：精简字段，Agent 快速理解页面结构
- `deep_index`：极度精简，Agent 搜索时用到
- `tree`：层级关系，Agent 决定展开策略

### 7.3 为什么空值过滤如此重要？

假设一个页面有 200 个节点，每个节点平均有 8 个空字段：
- 空字段总字符：`"field_name": "",` 约 15 字符 × 8 字段 × 200 节点 = 24,000 字符
- 换算 token：~6,000 token
- **这些 token 完全是浪费**

过滤后节省的 token 足以让 Agent 多看 50% 的节点。

---

## 八、外部 Agent 系统集成

### 8.1 Skill 注册示例（LangChain / OpenAI Function Calling）

```yaml
name: dp_cli_snapshot
description: |
  Capture a structured snapshot of the current web page.
  Returns a hierarchical index with three layers:
  - interactable_elements: clickable/typeable elements (check first)
  - surface_index: high-priority visible elements
  - deep_index: non-surface visible elements (condensed, for search)
  
  Empty-valued fields are filtered out to save tokens.
parameters:
  session:
    type: string
    default: "default"
  mode:
    type: string
    enum: ["agent_summary", "full", "extract"]
    default: "agent_summary"
  ref:
    type: string
    description: "Container ref to expand"
  depth:
    type: integer
    description: "Discovery depth for subtree"
```

### 8.2 Agent System Prompt 模板

```text
You are a browser automation agent using dp_cli v0.6.

## Page Index Structure

When you call `snapshot`, you get `data.index`:

### interactable_elements
Elements you can directly interact with. ALWAYS check this first.

### surface_index
High-priority elements: interactive, semantic containers, structural layers.
Browse this to understand the page layout.

### deep_index
Non-surface visible elements (condensed). Use `find --text` or `find --locator` 
to search when the target is not in surface_index.

### tree
- roots: top-level containers
- children_map: {parent_ref: [child_refs]}
Use this to decide which container to expand.

### stats
- total_nodes: total visible elements
- surface_count: elements in surface_index
- offscreen: elements below current viewport

## Rules
1. NEVER use eval for basic clicking/typing
2. ALWAYS prefer ref over locator
3. After navigation, take a new snapshot (refs become stale)
4. Large containers (child_count > 50): expand or use find
```

---

## 九、验证标准

- [ ] snapshot 采集到 table/tr/td 等非语义元素
- [ ] surface_index 和 deep_index 互斥，合起来覆盖所有 visible=true 的节点
- [ ] 输出 JSON 中不出现空字符串、空数组、空对象、null 值
- [ ] surface_index 节点数 <= 总节点数的 30%
- [ ] Agent 能在不 scroll 的情况下找到视口内所有可交互元素
- [ ] Agent 能通过 expand 展开深层容器
- [ ] Token 消耗 <= 当前 agent_summary 的 110%

---

## 十、总结

**核心原则**：
1. 不替 Agent 决定重要性 → 给完整目录
2. 不浪费 token → 过滤空值字段
3. 分层按需 → 表层快速浏览 + 深层搜索追溯

**输出结构**：
```
data.index
  ├─ interactable_elements  (极简，可交互项)
  ├─ surface_index          (精简，表层节点)
  ├─ deep_index             (极简，深层节点)
  ├─ tree                   (层级关系)
  └─ stats                  (统计信息)
```

**移除的字段**：`planner_view`、`summary`、`recovery`、`omitted_summary`

---

*本文档为设计确认文档，不含具体代码实现。*
