# dp_cli v0.5 升级计划：Agent-first 浏览器自动化与 AI 爬虫底座

> **版本**: v0.5-draft  
> **目标**: 融合 DOM 压缩算法与分层观察契约，实现同时支撑 Agent 自动化与 AI 爬虫的命令行工具  
> **基准**: 基于 deep-research-report.md 调研结论 + dom_compressor.py 压缩算法  
> **约束**: 不破坏现有 CLI 命令与 JSON 输出壳，仅升级内部契约与新增 Skills

---

## 1. 执行摘要

### 1.1 为什么要升级

当前 dp_cli 的 `planner_view` 虽然能工作，但存在三个核心问题：

1. **契约不稳定**: `planner_view` 的字段语义（pinned_controls / condensed_groups / viewport_nodes）混合了"摘要"与"结构"两种职责，上层 Prompt 和缓存会绑定到具体启发式，一改就破兼容性。
2. **缺少爬虫级结构**: 当前只有"可交互元素列表"思维，没有显式的 `group/item` 层级，列表页、表格页、分页场景无法生成循环式脚本。
3. **Token 消耗未受控**: 默认给 Agent 看的视图仍包含大量节点，没有基于"重复结构折叠"的压缩机制，长列表页 token 爆炸。

### 1.2 升级后是什么样子

升级后的 dp_cli 将具备以下核心能力：

- **三层观察契约**: `full` 模式（内部权威）→ `agent_summary`（默认给 LLM）→ `extract`（爬虫专用），通过 DOM 压缩算法将重复结构折叠为 `group` 表示
- **高成功率恢复定位**: `ref`（当轮句柄，>90% 同 snapshot 成功率）+ `fingerprint`（跨 snapshot 签名，>70% 重解析成功率）+ `locator_candidates`（代码级复用），三层重解析体系
- **爬虫级结构**: 显式 `region → group → item → control` 层级，支持 `list-items` / `extract` 技能生成循环式抓取脚本
- **渐进展开**: 默认只给低 token summary，需要时再 `expand` / `list-items` / `extract`

### 1.3 关键数据

| 指标 | 当前 v0.4 | 目标 v0.5 |
|---|---|---|
| 默认 snapshot token | 3000-8000（视页面长度） | 500-1500（summary 模式） |
| 列表页 token 节省 | 无压缩 | 55%-75%（DOM 压缩） |
| 跨页面重定位成功率 | ~30%（仅 ref，易失效） | >70%（fingerprint + locator） |
| 支持的 Skills 数量 | 5（open/snapshot/find/click/type） | 10（新增 expand/list-items/extract/resolve-locator/eval） |
| 爬虫脚本生成能力 | 弱（无 group/item 结构） | 强（显式 group + extract schema） |

---

## 2. 现状分析与差距

### 2.1 当前架构回顾

```
CLI (cli.py)
  → Service (service.py)
    → Adapter (adapter.py) [JS 注入提取 DOM]
    → Runtime (runtime.py) [ref 分配与失效]
    → Session (session.py) [browser 生命周期]
    → Store (session_store.py) [持久化]
```

**当前已具备的基础（可直接复用）**:

- DOM 语义提取脚本（`adapter.py` 的 `SNAPSHOT_SCRIPT`）
- `ref` 生命周期管理（`runtime.py` 的 `upsert_nodes`，按 xpath 复用 ref）
- runtime/page identity 校验（防止 stale ref）
- 统一 JSON 输出壳（`{ok, session, action, data, error}`）
- snapshot 归档到 `.dpcli/snapshots/`
- CLI 命令结构（argparse subparsers）

**当前的不足**:

| 组件 | 现状 | 不足 |
|---|---|---|
| `adapter.py` | 提取语义节点 flat list | 无结构 hash、无 group 检测、无 fingerprint |
| `service.py` | 生成 `planner_view` | planner_view 是临时 projection，非稳定契约；无 recovery |
| `models.py` | `SnapshotNodeRecord` | 无 `group_ref/item_ref/fingerprint/locator_candidates` |
| `cli.py` | 5 个命令 | 缺少 expand/list-items/extract/resolve-locator |

### 2.2 当前 planner_view 结构

```json
{
  "planner_view": {
    "pinned_controls": [...],
    "viewport_nodes": [...],
    "condensed_groups": [...],
    "stats": {...},
    "omitted_summary": {...}
  }
}
```

**问题分析**:

- `pinned_controls`: "pinned" 不是稳定语义，应改为 `global_actions`
- `viewport_nodes`: 把视口内所有节点堆给 LLM，token 高且无层次
- `condensed_groups`: 有雏形但缺少 `group_kind/item_ref/entry_action_ref/next_page_ref`
- `omitted_summary`: 只给 count，没有"去哪补"的恢复信息

---

## 3. 核心设计原则

### 3.1 设计原则声明

1. **全量在内部，投影给外部**: Python 侧始终维护内部完整图结构（`full` 模式）；默认只给 Agent 看 `agent_summary`；需要时再 `expand` / `list-items` / `extract`
2. **三层结构必须保留**: `region/block` → `group/item` → `field/control`，少掉 group/item 层爬虫几乎不可用
3. **ref 不是长期定位符**: `ref` 只是当轮句柄；跨 snapshot 用 `fingerprint`；生成代码用 `locator_candidates`
4. **DOM 压缩是投影层，不是替代层**: 压缩结果只用于 summary 展示，原始节点仍进入 ref 体系，确保 `click/type` 始终接受真实 `e*` ref
5. **JS 提特征，Python 做语义建模，LLM 只决策**: 不浪费 LLM token 做基础结构理解

### 3.2 与市面上项目的差异定位

| 项目 | 核心能力 | dp_cli v0.5 的差异 |
|---|---|---|
| Playwright CLI | aria snapshot + ref | dp_cli 增加 DOM 压缩 + 爬虫级 group/item 结构 |
| browser-use | DOM state + element index | dp_cli 保留 aria 语义 + 显式 group schema + locator recovery |
| agent-browser | tools/skills 输出 | dp_cli 把底层契约稳定暴露，不只是工具封装 |
| **dp_cli v0.5** | **DOM 压缩 + 分层契约 + 高成功率恢复定位 + 爬虫/自动化双模** | **同时兼顾 Agent 自动化与 AI 爬虫，更利于 Agent 使用** |

---

## 4. DOM 压缩融合方案（核心）

### 4.1 为什么需要融合两种压缩

当前有两种"压缩"思路：

1. **dom_compressor.py**: 基于结构 hash 的兄弟节点折叠，将重复列表项聚合为 `compressed_list`，token 节省 55%-75%
2. **deep_research 的 group 概念**: 基于语义角色（list/table/grid/tree）的显式 group 建模，支撑爬虫循环

**单独使用的问题**:

- 只用 dom_compressor: 压缩后节点变成 `compressed_list` 类型，但缺少 `group_kind`、`item_ref`、`entry_action_ref` 等语义，Agent 看不懂这是"可循环抓取的产品列表"
- 只用 deep_research group: 有语义但缺少"将 50 个 li 折叠成 1 个 group + 数据列"的 token 节省能力

**融合方案**: 用 dom_compressor 的"结构折叠"做**物理压缩**，用 deep_research 的"group 语义"做**逻辑标注**，两者叠加。

### 4.2 融合后的压缩架构

```
┌─────────────────────────────────────────────────────────────┐
│  Stage 1: JS 提取 (adapter.py)                               │
│  - 遍历 DOM，提取语义节点 flat list                          │
│  - 输出: [{xpath, parent_xpath, tag, role, name, text, ...}] │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│  Stage 2: Python 归一化 (service.py / normalizer)            │
│  - 构建父子关系树                                            │
│  - 分配 ref (runtime.upsert_nodes)                          │
│  - 计算 visibility / interactability                        │
│  - 生成 fingerprint / locator_candidates                    │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│  Stage 3: 结构压缩 (compressor 模块)                         │
│  - 基于结构 hash 检测重复兄弟节点                            │
│  - 将重复组折叠为 compressed_group                          │
│  - 保留: member_refs, representative_ref, xpath_template     │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│  Stage 4: 语义标注 (grouper 模块)                            │
│  - 识别 group_kind: list/table/grid/tree/card                │
│  - 标注: item_refs, entry_action_refs, next_page_ref         │
│  - 抽取: sample_fields, schema_hints                        │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│  Stage 5: 指纹与定位 (fingerprint + locator 模块)            │
│  - 为每个节点计算 fingerprint                               │
│  - 生成 locator_candidates (role+name/css/text/relative)    │
│  - 建立 fingerprint → ref 索引                              │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│  Stage 6: 投影生成 (projector 模块)                          │
│  - full: 完整 nodes + groups + recovery                     │
│  - agent_summary: global_actions + visible_focus + repeated  │
│  - extract: group scoped items + fields + pagination        │
│  - 硬预算控制: 超限自动裁剪                                   │
└─────────────────────────────────────────────────────────────┘
```

### 4.3 结构压缩算法（基于 dom_compressor 改进）

#### 4.3.1 结构 Hash 改进

原 dom_compressor 的 hash 依赖 `class`，但现代站点 class 常动态变化。改进版：

```python
def compute_structural_hash(node, child_hashes):
    """
    计算节点结构指纹，用于检测重复兄弟节点。
    弱化 class 依赖，强化语义特征。
    """
    parts = []
    
    # 1. tag（最稳定）
    parts.append(node.get("tag", "unknown"))
    
    # 2. role（语义稳定，优于 class）
    if node.get("role"):
        parts.append(f"role={node['role']}")
    
    # 3. 子节点结构序列（用子节点的 tag+role 序列）
    child_signatures = []
    for ch in child_hashes:
        # 只取 tag 和 role，不取内容
        child_signatures.append(ch.split(":")[0])
    parts.append("children=" + "|".join(child_signatures))
    
    # 4. 特定 tag 的关键属性
    if node.get("tag") == "input" and node.get("input_type"):
        parts.append(f"type={node['input_type']}")
    
    # 5. class 清洗后参与（可选，用于提升精度）
    if node.get("class"):
        # 去掉明显随机的 token（长度>8且含数字/哈希）
        cleaned = clean_class_token(node["class"])
        if cleaned:
            parts.append(f"cls={cleaned}")
    
    return "sha256(" + "|".join(parts) + ")"
```

**class 清洗规则**:

```python
def clean_class_token(class_str: str) -> str:
    """
    清洗 CSS class，去掉动态生成的 hash token，保留语义化类名。
    
    清洗策略:
    1. 保留 BEM/语义化类名: btn-primary, card-title, nav-item
    2. 去掉 CSS Modules hash: Button_btn__3X7k2, style_title__abc123
    3. 去掉 styled-components 前缀: sc-bdnxRM, sc-gtsrHT
    4. 保留 Tailwind 工具类但去掉变体中的 hash: md:flex -> 保留, md:flex-[hash] -> 去掉
    """
    tokens = class_str.split()
    stable = []
    for t in tokens:
        # 保留短语义类名（BEM/传统语义类）
        if re.match(r'^[a-z]+(-[a-z]+)+$', t) and len(t) < 30:
            stable.append(t)
            continue
            
        # 去掉 styled-components: sc-XXX (2-3字母前缀+随机大写)
        if re.match(r'^sc-[a-zA-Z]{6,}$', t):
            continue
            
        # 去掉 CSS Modules: name_hash__hash 或 name__hash
        if re.match(r'^[A-Za-z]+_[A-Za-z_]+__[a-zA-Z0-9]{5,}$', t):
            continue
            
        # 去掉明显随机的长串（>15字符且含大小写混合数字）
        if len(t) > 15 and re.search(r'[A-Z]', t) and re.search(r'[0-9]', t):
            continue
            
        # 其他保留（可能是合法的工具类）
        stable.append(t)
    
    return " ".join(stable)
```

#### 4.3.2 压缩节点结构

融合后的压缩节点同时保留"结构压缩信息"和"语义标注信息"：

```json
{
  "ref": "r10",
  "kind": "group",
  "group_kind": "list",
  "name": "Products",
  "role": "list",
  "compressed": true,
  "compression": {
    "count": 24,
    "member_refs": ["r11", "r12", "r13", "r14", "r15", "r16"],
    "representative_ref": "r11",
    "xpath_template": "//div[@class='products']/div[{i}]",
    "member_indices": [1, 2, 3, 4, 5, 6]
  },
  "schema": {
    "sample_fields": ["title", "price", "detail_link"],
    "entry_action_refs": ["e113", "e123", "e133"],
    "next_page_ref": "e90"
  },
  "data_preview": {
    "title": ["Apple iPad Air", "Apple iPad Pro", "Samsung Galaxy Tab", "..."],
    "price": ["$599", "$799", "$449", "..."],
    "detail_link": ["/product/air", "/product/pro", "/product/galaxy", "..."]
  }
}
```

**关键设计决策**:

- `member_refs`: 前 N 个样本的 ref（默认 3 个，可配置），让 Agent 能看到具体例子
- `representative_ref`: 第一个 item 的 ref，`expand` 时用这个 ref 做锚点
- `xpath_template` + `member_indices`: 支持可回退到 XPath 定位（当 ref 失效时的兜底方案）
- `data_preview`: 列式数据，类似 dom_compressor 的 `data` 字段，但字段名是语义化的（不是原始的 `txt/href`）

#### 4.3.3 压缩阈值策略

```python
COMPRESSION_CONFIG = {
    "min_group_size": 3,           # 至少 3 个重复项才压缩
    "min_group_size_dense": 2,     # 高密度重复区（>10 项）可降到 2
    "max_sample_items": 3,         # summary 中保留的样本数
    "max_data_preview_rows": 5,    # data_preview 最多展示的行数
    "max_group_depth": 2,          # 只对直接子节点做压缩，不递归压缩压缩节点
    
    # 双门槛：除数量外，还需满足结构和语义一致性
    "require_parent_semantic": True,  # 父容器必须有语义（role/landmark/aria-label）
    "structure_similarity_threshold": 0.85,  # 结构相似度阈值（基于子节点 tag+role 序列）
    "action_pattern_consistency": True,  # 组内交互模式应一致（如都是链接/都是按钮）
}
```

**双门槛判定逻辑**:

1. **数量门槛**: `count >= min_group_size`
2. **语义门槛**: 父容器有明确语义（`role=list/table/grid` 或 `tag=ul/ol/table`）
3. **结构门槛**: 组内所有 item 的子节点 tag+role 序列相似度 > 0.85
4. **交互门槛**: 组内主要可交互元素类型一致（如都是 `role=link` 或都是 `role=button`）

只有同时满足以上门槛才压缩，误压缩率可控制在 <5%。

### 4.4 与现有 ref 体系的兼容

**核心保证**: 压缩发生在 ref 分配之后，压缩节点只是对已有 ref 的"引用包装"，不影响 ref 的稳定性。

```python
# 流程保证
adapter.snapshot_nodes() -> runtime.upsert_nodes() -> compressor.detect_groups() -> grouper.annotate()
#              ↑ ref 在这里分配且稳定            ↑ 压缩在这里做，只引用已有的 ref
```

**click/type 约束不变**:

- `click` / `type` 只接受真实 `e*` ref（element ref）
- 压缩 group 的 ref 是 `r*`（container ref），不能直接点击
- Agent 必须通过 `expand` 或 `list-items` 获取 group 内的 item/control ref 后再操作

---

## 5. 新契约 Schema 设计

### 5.1 三层契约总览

| 模式 | 用途 | 典型 token 成本 | 包含内容 |
|---|---|---|---|
| `full` | 调试、深度理解、脚本生成 | 1.0x（基准） | page + nodes + groups + recovery |
| `agent_summary` | 默认给 Agent | 0.15x-0.30x | global_actions + visible_focus + repeated_regions + recovery |
| `extract` | 列表/表格/详情抽取 | 0.10x-0.25x（sample_only）<br>0.50x-1.0x（全量） | target group + items + fields + pagination |

### 5.2 full 模式 Schema

```json
{
  "schema_version": "0.5",
  "mode": "full",
  "page": {
    "url": "https://example.com/search?q=ipad",
    "title": "Search Results",
    "runtime_id": "rt_6c1",
    "page_id": "pg_21",
    "snapshot_id": "ss_109",
    "viewport": { "w": 1280, "h": 800 }
  },
  "roots": ["r1", "r2"],
  "nodes": {
    "r1": {
      "ref": "r1",
      "kind": "region",
      "role": "main",
      "name": "Results",
      "children": ["r10", "r20"],
      "visible": true
    },
    "r10": {
      "ref": "r10",
      "kind": "group",
      "group_kind": "list",
      "name": "Products",
      "role": "list",
      "children": ["r11", "r12", "r13"],
      "visible": true,
      "compressed": true,
      "compression": {
        "count": 24,
        "member_refs": ["r11", "r12", "r13"],
        "representative_ref": "r11",
        "xpath_template": "//div[@class='products']/div[{i}]",
        "member_indices": [1, 2, 3]
      }
    },
    "r11": {
      "ref": "r11",
      "kind": "item",
      "role": "listitem",
      "name": "Apple iPad Air",
      "children": ["r111", "r112", "e113"],
    },
    "r111": {
      "ref": "r111",
      "kind": "field",
      "role": "text",
      "name": "title",
      "text": "Apple iPad Air",
      "group_ref": "r10",
      "item_ref": "r11"
    },
    "e113": {
      "ref": "e113",
      "kind": "control",
      "role": "link",
      "name": "Apple iPad Air",
      "href": "/product/air",
      "group_ref": "r10",
      "item_ref": "r11",
      "fingerprint": "fp_8c2d1e5...",
      "locator_candidates": [
        "role=link[name='Apple iPad Air']",
        "css=a[href*='/product/air']"
      ],
      "visible": true,
      "interactable_now": true
    }
  },
  "groups": [
    {
      "group_ref": "r10",
      "group_kind": "list",
      "name": "Products",
      "item_refs": ["r11", "r12", "r13"],
      "item_count": 24,
      "sample_fields": ["title", "price", "detail_link"],
      "entry_action_refs": ["e113", "e123", "e133"],
      "next_page_ref": "e90",
      "schema_hints": {
        "title": { "selector": ".title", "type": "text" },
        "price": { "selector": ".price", "type": "text" },
        "detail_link": { "selector": "a", "type": "href" }
      }
    }
  ],
  "recovery": {
    "expand_candidates": ["r10", "r20"],
    "offscreen_actionable_count": 8,
    "truncated_regions": ["pagination"],
    "truncation_reason": "node_cap_exceeded",
    "truncation_threshold": 300,
    "total_nodes": 412
  }
}
```

### 5.3 agent_summary 模式 Schema

```json
{
  "schema_version": "0.5",
  "mode": "agent_summary",
  "page": {
    "url": "https://example.com/search?q=ipad",
    "title": "Search Results",
    "snapshot_id": "ss_109"
  },
  "summary": {
    "global_actions": [
      { "ref": "e5", "role": "textbox", "name": "Search" },
      { "ref": "e6", "role": "button", "name": "Search" }
    ],
    "visible_focus": [
      { "ref": "r10", "kind": "group", "name": "Products", "item_count": 24 }
    ],
    "repeated_regions": [
      {
        "group_ref": "r10",
        "group_kind": "list",
        "name": "Products",
        "sample_item_names": ["Apple iPad Air", "Apple iPad Pro", "Samsung Galaxy Tab"],
        "entry_action_refs": ["e113", "e123", "e133"],
        "next_page_ref": "e90"
      }
    ]
  },
  "recovery": {
    "expand_candidates": ["r10", "pagination"],
    "truncated": true,
    "offscreen_actionable_count": 8
  }
}
```

**Token 预算约束（硬性）**:

- `global_actions` ≤ 12 个
- `visible_focus` ≤ 6 个 region/group
- 每个 `repeated_region` 样本 item ≤ 3 个
- 总 summary token ≤ 1500（目标）

### 5.4 extract 模式 Schema

```json
{
  "schema_version": "0.5",
  "mode": "extract",
  "page": {
    "snapshot_id": "ss_109",
    "url": "https://example.com/search?q=ipad"
  },
  "target": {
    "group_ref": "r10",
    "group_kind": "list",
    "name": "Products"
  },
  "items": [
    {
      "item_ref": "r11",
      "fields": {
        "title": "Apple iPad Air",
        "price": "$599",
        "detail_href": "/product/air"
      },
      "entry_action_ref": "e113",
      "fingerprint": "fp_item_1a2b..."
    },
    {
      "item_ref": "r12",
      "fields": {
        "title": "Apple iPad Pro",
        "price": "$799",
        "detail_href": "/product/pro"
      },
      "entry_action_ref": "e123",
      "fingerprint": "fp_item_3c4d..."
    }
  ],
  "pagination": {
    "next_page_ref": "e90",
    "has_more": true,
    "current_page": 1,
    "total_pages_hint": null
  },
  "schema": {
    "fields": ["title", "price", "detail_href"],
    "field_types": {
      "title": "text",
      "price": "text",
      "detail_href": "href"
    }
  }
}
```

---

## 6. 高成功率恢复定位体系

### 6.1 三层身份设计

| 层级 | 名称 | 作用域 | 稳定性 | 用途 |
|---|---|---|---|---|
| L1 | `ref` | 当前 snapshot | 低（页面刷新即失效） | Agent 当轮交互句柄 |
| L2 | `fingerprint` | 跨 snapshot | 中（DOM 小变可恢复） | 跨页面/刷新后重定位 |
| L3 | `locator_candidates` | 代码级 | 高（结构不变即可用） | 生成可复用脚本 |

### 6.2 Fingerprint 设计

```python
class NodeFingerprint:
    """
    节点跨 snapshot 稳定签名。
    """
    
    def compute(self, node) -> str:
        parts = []
        
        # 1. 语义标识（最稳定）
        if node.get("role"):
            parts.append(f"role={node['role']}")
        if node.get("name"):
            parts.append(f"name={self._normalize(node['name'])}")
        
        # 2. 稳定属性
        for attr in ["href", "src", "value", "title"]:
            if node.get(attr):
                parts.append(f"{attr}={node[attr][:50]}")
        
    # 3. 局部 DOM path hash（从 parent_xpath 推导的 tag+role 序列）
    # 注意：不修改 adapter.js，在 Python 侧从 flat 节点的 parent_xpath 重建路径
    ancestor_path = runtime.get_ancestor_path(node.get("parent_xpath"))
    path_sig = " > ".join([
        f"{p['tag']}[{p.get('role', '')}]"
        for p in ancestor_path
    ])
        parts.append(f"path={path_sig}")
        
    # 4. 组内位置签名（使用语义标识而非 ref）
    if node.get("group_ref") and node.get("item_ref"):
        # 用 group 的 role+name 代替不稳定的 ref
        group = runtime.get_node(node["group_ref"])
        if group:
            parts.append(f"group_role={group.get('role', '')}")
            parts.append(f"group_name={group.get('name', '')[:20]}")
        # 用 item 在 group 中的位置类型（first/middle/last）代替具体索引
        item_index = node.get("item_index", -1)
        group_size = node.get("group_size", 0)
        if item_index == 0:
            parts.append("pos=first")
        elif item_index == group_size - 1:
            parts.append("pos=last")
        else:
            parts.append("pos=middle")
    
    # 5. 近邻文本锚点
        neighbor_text = self._get_neighbor_text(node)
        if neighbor_text:
            parts.append(f"neighbor={neighbor_text[:30]}")
        
        # 6. 容器/landmark 签名
        if node.get("context", {}).get("landmark"):
            parts.append(f"landmark={node['context']['landmark']}")
        
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
```

### 6.3 Re-resolve 六步流程

```python
def re_resolve(ref: str, fingerprint: str, locator_candidates: list) -> ResolveResult:
    """
    跨 snapshot 重解析节点。
    按优先级从高到低尝试。
    注：以下方法均为对现有代码的扩展，非全新实现。
    """
    
    # Step 1: 同 snapshot 直接用 ref
    # 复用 runtime.py 的 _ref_item() 校验逻辑
    if ref and runtime.is_ref_valid(ref):
        return ResolveResult(status="direct", ref=ref, confidence=1.0)
    
    # Step 2: fingerprint 精确重匹配
    # 新增: runtime.find_by_fingerprint() 在 fingerprint.py 中实现
    if fingerprint:
        matched_ref = fingerprint_index.find(fingerprint)
        if matched_ref and runtime.is_ref_valid(matched_ref):
            return ResolveResult(status="fingerprint", ref=matched_ref, confidence=0.85)
    
    # Step 3: locator_candidates 依次尝试
    # 复用 adapter.find_by_locator()（已有实现）
    if locator_candidates:
        for locator in locator_candidates:
            try:
                elem = adapter.find_by_locator(locator)
                if elem:
                    # 复用 runtime.upsert_nodes() 的 ref 分配逻辑
                    new_ref = runtime.upsert_node_from_element(elem)
                    return ResolveResult(status="locator", ref=new_ref, confidence=0.7)
            except Exception:
                continue
    
    # Step 4: scope 内 role+name 回退
    # 复用 adapter.find_by_text() 的模糊匹配能力
    if node_info := session_store.get_last_known_node(ref):
        candidates = adapter.find_by_text(node_info.get("name", ""))
        if candidates:
            # 筛选同 role 的候选
            same_role = [c for c in candidates if c.get("role") == node_info.get("role")]
            if same_role:
                best = same_role[0]
                new_ref = runtime.upsert_node_from_element(best)
                return ResolveResult(status="role_name_fallback", ref=new_ref, confidence=0.45)
    
    # Step 5: find 模糊回退
    # 复用 service.find_elements() 的文本搜索
    if node_info and node_info.get("name"):
        matches = service.find_elements(text=node_info["name"])
        if matches and len(matches) == 1:
            new_ref = runtime.upsert_node_from_element(matches[0])
            return ResolveResult(status="find_fallback", ref=new_ref, confidence=0.25)
    
    # Step 6: 全部失败，要求新 snapshot
    return ResolveResult(status="failed", ref=None, confidence=0.0, 
                        suggestion="请重新执行 snapshot 获取最新页面状态")
```

### 6.4 Locator Candidates 生成

```python
def generate_locator_candidates(node) -> list:
    """
    为节点生成多种定位策略，按稳定性排序。
    """
    candidates = []
    
    # 策略 1: role + name（aria 语义定位）
    if node.get("role") and node.get("name"):
        candidates.append(f"role={node['role']}[name='{node['name']}']")
    
    # 策略 2: aria-label
    if node.get("aria_label"):
        candidates.append(f"[aria-label='{node['aria_label']}']")
    
    # 策略 3: 工程化测试标识（最稳定）
    for test_attr in ["data-testid", "data-test-id", "data-qa", "data-qa-id"]:
        if node.get(test_attr):
            candidates.append(f"[{test_attr}='{node[test_attr]}']")
            break
    
    # 策略 4: 稳定 CSS 属性
    if node.get("href"):
        candidates.append(f"css=a[href*='{node['href'].split('/')[-1]}']")
    if node.get("id") and not node["id"].startswith(("_", "temp")):
        candidates.append(f"css=#{node['id']}")
    
    # 策略 4: 文本内容（短文本才用）
    if node.get("text") and len(node["text"]) < 50:
        candidates.append(f"text='{node['text']}'")
    
    # 策略 5: 组内相对定位
    if node.get("group_ref") and node.get("item_ref"):
        candidates.append(
            f"group={node['group_ref']} >> item={node['item_ref']} >> "
            f"role={node.get('role', '*')}"
        )
    
    return candidates
```

---

## 7. 新 Skills 设计

### 7.1 Skill 总览

| Skill | 输入 | 输出 | Token 成本 | 典型失败模式 |
|---|---|---|---|---|
| `open` | `url`, `wait` | `page meta`, `snapshot_id` | 极低 | 跳转慢、重定向、登录墙 |
| `snapshot` | `mode`, `root_ref?`, `depth?` | `full/summary/extract` | `summary` 默认低 | 页面过长、虚拟列表 |
| `expand` | `ref`, `depth=2` | 指定 subtree 的 full 片段 | 中 | ref 失效、容器不稳定 |
| `find` | `query`, `scope_ref?`, `role?` | `matches[]`, `matched_by` | 低到中 | 文案歧义、重复命中 |
| `click` | `ref` 或 `locator_hint` | `action_result`, `new_snapshot_id` | 极低 | ref stale、遮挡、弹窗 |
| `fill` / `type` | `ref`, `value`, `clear?` | `action_result`, `field_state` | 极低 | 字段不是输入框、受控组件 |
| `list-items` | `group_ref`, `sample_size?` | `group schema`, `item refs` | 中 | group 识别错误、虚拟滚动 |
| `extract` | `target_ref`, `schema?` | `items[]` 或 `record` | 中 | 字段识别错、嵌套列表 |
| `resolve-locator` | `ref` 或 `fingerprint` | `locator_candidates`, `confidence` | 低 | DOM 变化大、class 动态化 |
| `eval` | `js`, `scope_ref?` | 任意 JSON-safe 结果 | 低到高 | unsafe script、不可序列化 |

### 7.2 关键 Skill 详细设计

#### expand

```json
{
  "skill": "expand",
  "input": {
    "ref": "r10",
    "depth": 2
  },
  "output": {
    "ok": true,
    "data": {
      "target_ref": "r10",
      "mode": "full",
      "nodes": {
        "r11": { ... },
        "r12": { ... },
        "r13": { ... }
      },
      "groups": [ ... ],
      "recovery": {
        "has_more": true,
        "remaining_count": 21,
        "next_expand_hint": "滚动后重新 snapshot"
      }
    }
  }
}
```

**实现方式**: `expand` 本质上是 `snapshot <ref> --depth N --view full`，复用现有 snapshot 能力，只是 scope 从 page 缩小到 subtree。

#### list-items

```json
{
  "skill": "list-items",
  "input": {
    "group_ref": "r10",
    "sample_size": 5
  },
  "output": {
    "ok": true,
    "data": {
      "group_ref": "r10",
      "group_kind": "list",
      "item_count": 24,
      "sample_items": [
        {
          "item_ref": "r11",
          "fields": { "title": "Apple iPad Air", "price": "$599" },
          "entry_action_ref": "e113"
        }
      ],
      "schema_hints": {
        "title": { "selector": ".title", "type": "text" },
        "price": { "selector": ".price", "type": "text" }
      },
      "pagination": {
        "has_more": true,
        "next_page_ref": "e90"
      }
    }
  }
}
```

**作用**: 让 Agent 理解"这是一个可循环抓取的产品列表，每个 item 有 title/price/detail_link 字段"，从而生成循环脚本。

#### extract

```json
{
  "skill": "extract",
  "input": {
    "target_ref": "r10",
    "schema": ["title", "price", "detail_href"],
    "sample_only": false
  },
  "output": {
    "ok": true,
    "data": {
      "target": { "group_ref": "r10", "group_kind": "list" },
      "items": [...],
      "pagination": { "next_page_ref": "e90", "has_more": true },
      "schema": { "fields": [...], "field_types": {...} }
    }
  }
}
```

**作用**: 直接输出结构化数据，供爬虫使用。`sample_only=true` 时只抽前 3 条做 schema 验证，避免全量抽取的 token 浪费。

#### resolve-locator

```json
{
  "skill": "resolve-locator",
  "input": {
    "ref": "e113"
  },
  "output": {
    "ok": true,
    "data": {
      "ref": "e113",
      "fingerprint": "fp_8c2d1e5...",
      "confidence": 0.86,
      "locator_candidates": [
        "role=link[name='Apple iPad Air']",
        "css=a[href*='/product/air']"
      ],
      "re_resolve_result": "matched",
      "suggestion": null
    }
  }
}
```

**作用**: 把一次性 ref 变成可复用的代码级定位策略，支撑脚本生成。

---

## 8. 数据流与架构图

### 8.1 完整数据流

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Browser (Chromium)                           │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  JS Extractor (adapter.py::SNAPSHOT_SCRIPT)                 │   │
│  │  - 遍历 DOM                                                 │   │
│  │  - 提取: tag, role, name, text, href, bounds, visibility   │   │
│  │  - 输出: flat list of semantic nodes                       │   │
│  └────────────────────────────┬────────────────────────────────┘   │
└───────────────────────────────┼─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│                    Python Layer (dp_cli)                            │
│                                                                     │
│  ┌─────────────────────────────┐  ┌─────────────────────────────┐  │
│  │  Normalizer                 │  │  Ref Manager (runtime.py)   │  │
│  │  - 构建父子关系              │  │  - xpath → ref 映射          │  │
│  │  - 计算 visibility          │  │  - runtime/page identity    │  │
│  │  - 标注 context             │  │  - stale 检测               │  │
│  └──────────────┬──────────────┘  └─────────────────────────────┘  │
│                 │                                                   │
│  ┌──────────────▼──────────────────────────────────────────────┐   │
│  │  Compressor (基于 dom_compressor 改进)                       │   │
│  │  - 结构 hash 检测重复兄弟节点                                │   │
│  │  - 折叠为 compressed_group                                  │   │
│  │  - 保留: member_refs, xpath_template, member_indices        │   │
│  └──────────────┬───────────────────────────────────────────────┘   │
│                 │                                                   │
│  ┌──────────────▼──────────────────────────────────────────────┐   │
│  │  Grouper (语义标注)                                          │   │
│  │  - 识别 group_kind: list/table/grid/tree/card                │   │
│  │  - 标注: item_refs, entry_action_refs, next_page_ref         │   │
│  │  - 抽取: sample_fields, schema_hints                        │   │
│  └──────────────┬───────────────────────────────────────────────┘   │
│                 │                                                   │
│  ┌──────────────▼──────────────────────────────────────────────┐   │
│  │  Fingerprint / Locator Generator                            │   │
│  │  - 为每个节点计算 fingerprint                               │   │
│  │  - 生成 locator_candidates (role+name/css/text/relative)    │   │
│  └──────────────┬───────────────────────────────────────────────┘   │
│                 │                                                   │
│  ┌──────────────▼──────────────────────────────────────────────┐   │
│  │  Projector (模式投影)                                        │   │
│  │  - full: nodes + groups + recovery                          │   │
│  │  - agent_summary: global_actions + visible_focus + repeated  │   │
│  │  - extract: items + fields + pagination                     │   │
│  └──────────────┬───────────────────────────────────────────────┘   │
│                 │                                                   │
│  ┌──────────────▼──────────────────────────────────────────────┐   │
│  │  Skill Surface (cli.py)                                     │   │
│  │  open → snapshot → expand/list-items/extract → click/type  │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│                          LLM / Agent                                │
│  - 接收 agent_summary (低 token)                                    │
│  - 决策: click / expand / list-items / extract                      │
│  - 生成可复用脚本 (使用 resolve-locator)                            │
└─────────────────────────────────────────────────────────────────────┘
```

### 8.2 观察策略切换

```python
def select_observation_strategy(nodes, groups):
    """
    根据页面特征动态选择观察策略。
    """
    accessible_count = sum(1 for n in nodes if n.get("role"))
    actionable_count = sum(1 for n in nodes if n.get("interactable_now"))
    group_count = len(groups)
    region_count = sum(1 for n in nodes if n.get("kind") == "region")
    
    a11y_action_coverage = accessible_count / max(actionable_count, 1)
    group_density = group_count / max(region_count, 1)
    
    if a11y_action_coverage > 0.8 and group_density < 0.3:
        return "aria-first"  # 标准表单/导航页
    elif a11y_action_coverage < 0.6:
        return "semantic-first"  # 自定义组件页
    elif group_density > 0.5 or any(g["group_kind"] in ("table", "grid") for g in groups):
        return "hybrid"  # 列表/表格页（默认）
    else:
        return "hybrid"  # 默认 hybrid
```

---

## 9. 实施路线图

### 9.1 八阶段实施计划

| 阶段 | 交付物 | 工作量 | 依赖 | 验收标准 |
|---|---|---|---|---|
| **P1: 冻结主契约** | `schema_version: 0.5`，`page/nodes/groups/summary/recovery` schema 定义落地 | S | 无 | README 更新，JSON 示例稳定；打开新闻页/搜索页，summary/full/extract 都能产出 |
| **P2: DOM 压缩引擎** | 改进版结构 hash、compressed_group 检测、token 节省验证 | M | P1 | 列表页（30+ 项）压缩后 token 节省 >55%；压缩不误伤非重复结构 |
| **P3: 补 ref 与 fingerprint** | `fingerprint`、`locator_candidates`、`re_resolve` 基础实现 | M | P1 | 页面轻微刷新后重解析成功率 >70%；ref stale 时有恢复建议 |
| **P4: 重做 summary** | 用 `global_actions/visible_focus/repeated_regions/recovery` 替换 planner_view | M | P2 | summary token < 1500；关键动作仍可见；电商搜索页/后台表格页通过 |
| **P5: group/list schema** | `list-items`、`group_kind`、`item_ref`、`entry_action_ref`、`next_page_ref` | M | P2 | 列表页自动识别 group；给出样本字段；可生成循环脚本 |
| **P6: 做 extract** | `extract(group_ref/detail_ref, schema?)` | M | P5 | 同一列表稳定抽出 title/link/price/date；支持 sample_only 模式；**分页自动翻页为必过项** |
| **P7: skill surface 补齐** | expand/list-items/extract/resolve-locator/eval 命令 | M | P3, P4, P5, P6 | 一次任务可全程只靠 skills 完成：登录→搜索→抓取前 5 项→生成定位脚本 |
| **P8: 默认参数与 benchmark** | depth/summary size/node cap/debug mode 调优 | S | P7 | summary 可控、full 可恢复、失败有 recovery；长页面/虚拟列表/分页站点通过 |

### 9.2 详细任务分解

#### P1: 冻结主契约（1-2 天）

**任务清单**:

1. [ ] 更新 `models.py`，新增/修改数据模型:
   - `SnapshotNodeRecord` 增加 `kind`, `group_ref`, `item_ref`, `fingerprint`, `locator_candidates`
   - 新增 `GroupRecord` dataclass
   - 新增 `RecoveryInfo` dataclass
   - `SnapshotArtifact` 增加 `groups`, `recovery`, `schema_version`
2. [ ] 更新 `service.py`，`snapshot_page()` 返回新契约结构（先不实现压缩，只改壳）
3. [ ] 更新 `cli.py`，`snapshot` 命令支持 `--mode full|agent_summary|extract`
4. [ ] 更新 README，记录 v0.5 契约定义
5. [ ] 写回归测试：验证三种模式的 JSON schema 正确性

**兼容性保证（双输出兼容层策略）**:

- **第一阶段（v0.5.0）**: 同时输出 `planner_view`（旧字段）和 `summary`（新字段），保持现有测试通过
- **第二阶段（v0.5.x）**: `planner_view` 标记 deprecated，引导迁移到 `summary`
- **第三阶段（v0.6）**: 移除 `planner_view`，仅保留新契约
- `--view planner` 映射为 `--mode agent_summary`，但内部同时填充旧 `planner_view` 字段
- `--view full` 映射为 `--mode full`
- 新增 `schema_version` 字段用于区分新旧格式

#### P2: DOM 压缩引擎（5-8 天）

**任务清单**:

1. [ ] 新建 `dp_cli/compressor.py`:
   - `StructuralHasher`: 改进版结构 hash（弱化 class 依赖）
   - `DOMCompressor`: 兄弟节点压缩核心
   - `CompressionConfig`: 压缩阈值配置
2. [ ] 在 `service.py` 的归一化层与投影层之间插入压缩层（`normalize -> compress -> group -> fingerprint -> project`）
3. [ ] 实现 compressed_group 的 JSON 序列化
4. [ ] 写 benchmark 脚本，使用本地 fixture 页面测量 token 节省率:
   - fixture: 电商搜索页（30+ 商品卡片，本地 HTML）
   - fixture: 新闻列表页（20+ 文章条目，本地 HTML）
   - fixture: 后台表格页（50+ 行，本地 HTML）
   - fixture: 混合页面（列表+表单+分页，本地 HTML）
   - 公网 spot check: 可选，仅用于验证 fixture 代表性
5. [ ] 调优阈值，确保误压缩率 < 5%（增加父容器语义+结构相似度+交互模式三重门槛）

**关键算法**:

```python
# dp_cli/compressor.py

class DOMCompressor:
    def __init__(self, config: CompressionConfig):
        self.config = config
        self.hasher = StructuralHasher()
    
    def compress(self, nodes: list[SnapshotNodeRecord]) -> list[CompressedGroup]:
        """
        检测并压缩重复兄弟节点组。
        返回压缩组列表（未压缩的节点不包含在结果中）。
        """
        # 1. 构建父子关系
        children_map = self._build_children_map(nodes)
        
        compressed_groups = []
        for parent_ref, children in children_map.items():
            # 2. 计算每个子节点的结构 hash
            hashes = [self.hasher.compute(c) for c in children]
            
            # 3. 扫描连续相同 hash 的组
            groups = self._group_by_hash(children, hashes)
            
            for group in groups:
                if len(group) >= self.config.min_group_size:
                    cg = self._create_compressed_group(group)
                    compressed_groups.append(cg)
        
        return compressed_groups
    
    def _create_compressed_group(self, group: list[SnapshotNodeRecord]) -> CompressedGroup:
        template = group[0]
        return CompressedGroup(
            representative_ref=template.ref,
            member_refs=[n.ref for n in group[:self.config.max_sample_items]],
            member_indices=[n.index for n in group],
            count=len(group),
            xpath_template=self._derive_xpath_template(template),
            tag=template.tag,
            role=template.role,
        )
```

#### P3: 补 ref 与 fingerprint（2-3 天）

**任务清单**:

1. [ ] 新建 `dp_cli/fingerprint.py`:
   - `NodeFingerprint.compute()`
   - `FingerprintIndex`（内存中的 fingerprint → ref 索引）
2. [ ] 更新 `runtime.py`:
   - `upsert_nodes()` 时计算 fingerprint
   - 新增 `find_by_fingerprint()`
   - 新增 `get_last_known_info()`（ref 失效后保留最后已知信息）
3. [ ] 新建 `dp_cli/locator.py`:
   - `LocatorGenerator.generate_candidates()`
   - `LocatorPriority`（按稳定性排序）
4. [ ] 更新 `service.py`，在 snapshot 输出中包含 fingerprint 和 locator_candidates
5. [ ] 写测试：验证 fingerprint 在 DOM 小变时的稳定性

#### P4: 重做 summary（2-3 天）

**任务清单**:

1. [ ] 新建 `dp_cli/projector.py`:
   - `SummaryProjector`: 生成 agent_summary
   - `ExtractProjector`: 生成 extract 视图
   - `RecoveryProjector`: 生成 recovery 信息
   - `TokenBudgetEnforcer`: 硬预算控制（生成后测 token，超限自动裁剪）
2. [ ] 实现 `global_actions` 选择逻辑（原 pinned_controls 的改进版）
3. [ ] 实现 `visible_focus` 选择逻辑（限制 6 个 region/group）
4. [ ] 实现 `repeated_regions` 生成（基于 compressed_group + grouper 标注）
5. [ ] 实现 `recovery` 生成（truncation 信息 + expand_candidates）
6. [ ] 实现硬预算器：
   - 生成 summary 后计算 token
   - 若超限，按优先级裁剪（先裁剪 sample_items，再裁剪 visible_focus，再裁剪 global_actions）
   - 记录 truncation 原因到 recovery
7. [ ] 写测试：验证 summary token < 1500（含硬预算器生效）

#### P5: group/list schema（2-3 天）

**任务清单**:

1. [ ] 新建 `dp_cli/grouper.py`:
   - `GroupKindDetector`: 识别 list/table/grid/tree/card
   - `FieldSchemaExtractor`: 从 item 样本中推断字段 schema
   - `PaginationDetector`: 检测分页控件
2. [ ] 实现 `list-items` skill（CLI 命令）
3. [ ] 实现 group 内字段抽取（从重复 item 中找共同字段）
4. [ ] 实现 entry_action 识别（item 内的主要可点击元素）
5. [ ] 实现 next_page 识别（分页区检测）
6. [ ] 写测试：商品列表页、博客列表页、表格页

#### P6: 做 extract（2-3 天）

**任务清单**:

1. [ ] 实现 `extract` skill（CLI 命令）
2. [ ] 支持 `schema` 参数（指定要抽取的字段）
3. [ ] 支持 `sample_only` 参数（只抽样本做 schema 验证）
4. [ ] **支持分页自动翻页（必选项，爬虫闭环关键）**
   - 实现 `next_page_ref` 的自动识别与点击
   - 跨页去重（基于 fingerprint 去重，避免重复抓取同一 item）
   - 最大翻页数限制（默认 10 页，可配置）
   - 虚拟列表/懒加载场景的增量抓取策略
5. [ ] 实现字段类型推断（text/href/src/image/date/number）
6. [ ] 写测试：新闻列表、招聘列表、商品列表（含分页场景）

#### P7: skill surface 补齐（3-5 天）

**任务清单**:

1. [ ] 实现 `expand` skill（CLI 命令）
2. [ ] 实现 `resolve-locator` skill（CLI 命令）
3. [ ] 实现 `eval` skill（CLI 命令，已有基础）
4. [ ] 更新 CLI help 文档
5. [ ] 编写端到端测试：登录→搜索→抓取→生成脚本
6. [ ] 更新 `scripts/test_min_agent_loop.py` 以使用新 skills

#### P8: 默认参数与 benchmark（1-2 天）

**任务清单**:

1. [ ] 调优 summary 预算参数（global_actions/visible_focus/repeated_regions 限制）
2. [ ] 调优压缩阈值（min_group_size/max_sample_items）
3. [ ] 调优 full 模式 node cap（200-300 个可见节点）
4. [ ] 实现 `--verbose` / `--debug` 输出模式
5. [ ] 编写 benchmark 报告：token 成本、重解析成功率、skill 完成率
6. [ ] 编写 dp_cli v0.5 使用指南

### 9.3 与现有代码的兼容性策略

| 现有组件 | 兼容性策略 |
|---|---|
| `cli.py` 命令结构 | 保留现有命令，新增命令为扩展；`--view planner` 映射为 `--mode agent_summary` |
| `adapter.py` JS 提取 | 不改 JS 脚本，只在 Python 后处理阶段增加压缩/分组 |
| `runtime.py` ref 体系 | 完全复用，只是新增 fingerprint 计算 |
| `session_store.py` 持久化 | `SnapshotArtifact` 增加新字段，旧数据通过 schema_version 区分 |
| `tests/` 测试 | 现有测试继续通过；新增测试覆盖新功能 |
| `planner_view` | 保留为兼容层，内部实现改为 summary 投影 |

---

## 10. 模块设计详图

### 10.1 新增/修改文件清单

```
dp_cli/
├── __init__.py
├── cli.py                    # [修改] 新增 expand/list-items/extract/resolve-locator/eval 命令
├── service.py                # [修改] 重构 snapshot 生成，插入 compressor/grouper/projector
├── adapter.py                # [不修改] JS 提取逻辑保持不变，所有后处理在 Python 侧完成
├── session.py                # [不修改]
├── runtime.py                # [修改] 增加 fingerprint 计算和查找
├── models.py                 # [修改] 增加 GroupRecord/RecoveryInfo/CompressedGroup 等模型
├── errors.py                 # [不修改]
├── session_store.py          # [轻微修改] SnapshotArtifact 增加 schema_version
│
├── compressor.py             # [新增] DOM 结构压缩引擎
├── grouper.py                # [新增] 语义分组与 schema 推断
├── projector.py              # [新增] full/summary/extract 投影生成
├── fingerprint.py            # [新增] 节点 fingerprint 计算
├── locator.py                # [新增] locator candidates 生成
└── normalizer.py             # [新增] JS 提取后的归一化层（从 service.py 拆出）
```

### 10.2 关键类接口设计

#### compressor.py

```python
@dataclass
class CompressionConfig:
    min_group_size: int = 3
    min_group_size_dense: int = 2
    max_sample_items: int = 3
    max_data_preview_rows: int = 5
    max_group_depth: int = 2

@dataclass  
class CompressedGroup:
    representative_ref: str           # 第一个 item 的 ref
    member_refs: list[str]           # 样本 refs
    member_indices: list[int]        # 原始索引
    count: int
    xpath_template: str
    tag: str
    role: str | None

class StructuralHasher:
    def compute(self, node: SnapshotNodeRecord, child_hashes: list[str]) -> str: ...

class DOMCompressor:
    def __init__(self, config: CompressionConfig): ...
    def compress(self, nodes: list[SnapshotNodeRecord]) -> list[CompressedGroup]: ...
```

#### grouper.py

```python
@dataclass
class GroupRecord:
    group_ref: str
    group_kind: str  # list | table | grid | tree | card
    name: str
    item_refs: list[str]
    item_count: int
    sample_fields: list[str]
    entry_action_refs: list[str]
    next_page_ref: str | None
    schema_hints: dict[str, dict]

class GroupKindDetector:
    def detect(self, compressed_group: CompressedGroup, nodes: dict[str, SnapshotNodeRecord]) -> str: ...

class FieldSchemaExtractor:
    def extract(self, item_refs: list[str], nodes: dict) -> dict[str, dict]: ...

class PaginationDetector:
    def detect(self, nodes: list[SnapshotNodeRecord]) -> str | None: ...  # 返回 next_page_ref
```

#### projector.py

```python
@dataclass
class AgentSummary:
    global_actions: list[dict]
    visible_focus: list[dict]
    repeated_regions: list[dict]

@dataclass
class RecoveryInfo:
    expand_candidates: list[str] = field(default_factory=list)
    offscreen_actionable_count: int = 0
    truncated_regions: list[str] = field(default_factory=list)
    truncation_reason: str | None = None
    truncation_threshold: int | None = None
    total_nodes: int = 0
    truncated: bool = False

class SummaryProjector:
    def project(self, nodes: dict, groups: list[GroupRecord], recovery: RecoveryInfo) -> AgentSummary: ...

class ExtractProjector:
    def project(self, group: GroupRecord, nodes: dict, schema: list[str] | None) -> dict: ...

class TokenBudgetEnforcer:
    """
    硬预算控制器：生成后测 token，超限自动裁剪。
    裁剪优先级（从高到低）:
    1. repeated_regions 的 sample_item_names（保留数量从 3 降到 1）
    2. visible_focus 中的非关键 region
    3. global_actions 中的非关键控件
    4. 若仍超限，标记 truncation 并返回 recovery
    """
    
    def __init__(self, max_tokens: int = 1500):
        self.max_tokens = max_tokens
    
    def enforce(self, summary: AgentSummary) -> tuple[AgentSummary, RecoveryInfo]:
        """
        检查 summary token 并裁剪。
        返回裁剪后的 summary 和 recovery 信息。
        """
        current_tokens = self._estimate_tokens(summary)
        
        if current_tokens <= self.max_tokens:
            return summary, RecoveryInfo()
        
        # 裁剪阶段 1: 减少 repeated_regions 样本
        for region in summary.repeated_regions:
            while self._estimate_tokens(summary) > self.max_tokens and len(region.sample_item_names) > 1:
                region.sample_item_names.pop()
        
        # 裁剪阶段 2: 减少 visible_focus
        while self._estimate_tokens(summary) > self.max_tokens and len(summary.visible_focus) > 1:
            summary.visible_focus.pop()
        
        # 裁剪阶段 3: 减少 global_actions（保留关键动作）
        critical_roles = {"textbox", "searchbox", "button", "link"}
        while self._estimate_tokens(summary) > self.max_tokens and len(summary.global_actions) > 1:
            # 优先移除非关键角色的动作
            for i, action in enumerate(summary.global_actions):
                if action.get("role") not in critical_roles:
                    summary.global_actions.pop(i)
                    break
            else:
                summary.global_actions.pop()
        
        recovery = RecoveryInfo(
            truncated=True,
            truncation_reason="token_budget_exceeded",
            truncation_threshold=self.max_tokens,
            expand_candidates=[r.get("ref") for r in summary.visible_focus]
        )
        
        return summary, recovery
    
    def _estimate_tokens(self, summary: AgentSummary) -> int:
        """
        粗略估算 JSON 的 token 数（字符数 / 4 作为保守估计）。
        实际应使用 tiktoken 等精确计算。
        """
        import json
        json_str = json.dumps(summary.__dict__, ensure_ascii=False)
        return len(json_str) // 4
```

#### fingerprint.py

```python
@dataclass
class ResolveResult:
    status: str  # direct | fingerprint | locator | role_name_fallback | find_fallback | failed
    ref: str | None
    confidence: float
    suggestion: str | None

class NodeFingerprint:
    def compute(self, node: SnapshotNodeRecord) -> str: ...

class FingerprintIndex:
    def add(self, ref: str, fingerprint: str): ...
    def find(self, fingerprint: str) -> str | None: ...  # 返回 ref
```

#### locator.py

```python
class LocatorGenerator:
    def generate(self, node: SnapshotNodeRecord) -> list[str]: ...
    
class LocatorPriority:
    ROLE_NAME = 1
    ARIA_LABEL = 2
    CSS_ID = 3
    CSS_HREF = 4
    TEXT = 5
    RELATIVE = 6
```

---

## 11. 风险评估与缓解

### 11.1 技术风险

| 风险 | 可能性 | 影响 | 缓解措施 |
|---|---|---|---|
| 结构 hash 误压缩（把不同功能节点压在一起） | 中 | 高 | class 清洗 + role 加权 + 人工 review benchmark 结果 |
| fingerprint 不稳定（DOM 小变导致匹配失败） | 中 | 中 | 多维度签名（role+name+path+neighbor），容忍部分字段变化 |
| group 识别错误（把非列表识别为列表） | 中 | 高 | 保守策略：多信号确认（role+结构+内容模式）；提供 `ungroup` 手动修正 |
| 长页面 truncation 导致关键信息丢失 | 高 | 高 | recovery 信息显式标注 truncation；expand 支持按需展开 |
| 虚拟列表/懒加载导致 group item 不全 | 高 | 中 | detect 虚拟滚动模式；自动滚动-采样-去重-合并（基于 fingerprint 去重）；提示 Agent 增量抓取 |
| SPA 同 URL 局部重渲染导致 ref 语义漂移 | 中 | 高 | page identity 增加 DOM hash（关键节点 fingerprint 集合）；局部变化时保留未变节点的 ref |
| 多语言站点文本定位失效 | 中 | 中 | locator candidates 优先使用非文本策略（role/css/aria-label）；文本策略作为 fallback |
| 旧版本测试不兼容 | 低 | 中 | 保留 `--view planner` 别名；渐进式迁移测试；双输出兼容层 |

### 11.2 性能风险

| 风险 | 缓解措施 |
|---|---|
| DOM 压缩增加 snapshot 耗时 | 压缩是 O(n) 的兄弟扫描，且只处理可见节点；benchmark 验证 < 100ms |
| fingerprint 计算增加内存占用 | fingerprint 是 16 字符字符串，内存增加可忽略 |
| full 模式 node cap 限制导致信息丢失 | 默认用 agent_summary，full 只用于调试；recovery 标注 truncation |

### 11.3 兼容性保证

**向前兼容（v0.5 兼容 v0.4）**:

- **第一阶段（v0.5.0）**: 同时输出 `planner_view`（旧字段）和 `summary`（新字段），现有测试无需修改
- **第二阶段（v0.5.x）**: `planner_view` 标记 deprecated，控制台输出 warning
- **第三阶段（v0.6）**: 移除 `planner_view`，仅保留新契约
- `--view planner` 映射为 `--mode agent_summary`，内部同时填充旧字段
- `--view full` 映射为 `--mode full`
- `SnapshotArtifact` 通过 `schema_version` 字段区分新旧格式

**向后兼容（v0.4 消费 v0.5）**:

- 新契约新增字段均为 optional，不影响旧代码解析
- 新增 skills 为扩展命令，不影响现有命令
- `planner_view` 字段在 v0.5.0 中继续存在，保证旧版 Agent 能正常消费

---

## 12. 验收标准

### 12.1 功能验收

| 验收项 | 标准 | 验证方式 |
|---|---|---|
| 主契约 schema 稳定 | `full/agent_summary/extract` 三种模式 JSON 结构固定 | 单元测试断言 schema |
| DOM 压缩有效 | 列表页（30+ 项）压缩后 token 节省 > 55% | benchmark 脚本对比 |
| 压缩不误伤 | 非重复结构误压缩率 < 5% | benchmark 脚本测试 4 个本地 fixture 页面 |
| 重解析可靠 | 页面刷新后 fingerprint 重解析成功率 > 70% | 自动化测试 |
| Group 识别 | 商品列表/博客列表/表格页自动识别 group | 端到端测试 |
| Extract 能力 | 稳定抽取 title/price/href/date 等字段 | 端到端测试 |
| Skill 闭环 | 登录→搜索→抓取→生成定位脚本能全程走通 | demo 脚本验证 |
| Token 控制 | agent_summary 默认输出 < 1500 tokens | benchmark 验证 |
| 向后兼容 | 现有测试 `pytest tests/test_cli_local.py` 全部通过 | CI 验证 |

### 12.2 性能验收

| 验收项 | 标准 |
|---|---|
| Snapshot 耗时 | agent_summary 模式 < 500ms（含压缩） |
| Extract 耗时 | 单页 < 1s |
| 内存占用 | 比 v0.4 增加 < 20% |

### 12.3 代码质量验收

| 验收项 | 标准 |
|---|---|
| 测试覆盖率 | 新增模块 > 80% |
| 类型注解 | 所有新增模块使用类型注解 |
| 文档 | 每个新模块有 docstring；README 更新使用说明 |
| 无 breaking change | 现有 CLI 命令行为不变 |

---

## 13. 附录

### 13.1 术语表

| 术语 | 定义 |
|---|---|
| `snapshot` | 页面某一时刻的完整状态捕获 |
| `ref` | 当前 snapshot 内节点的短期引用标识（如 `e5`, `r10`） |
| `fingerprint` | 节点的跨 snapshot 稳定签名 |
| `locator` | 可用于代码复用的元素定位策略（如 CSS selector, aria role） |
| `group` | 页面上的重复结构（如产品列表、表格行） |
| `item` | group 中的单个条目 |
| `control` | 可交互元素（按钮、链接、输入框） |
| `region` | 页面的逻辑区域（导航、主内容、侧边栏） |
| `compressed_group` | 被压缩算法折叠的重复节点组 |
| `agent_summary` | 默认给 Agent 看的低 token 视图 |
| `recovery` | 当信息被截断时的补全提示 |

### 13.2 参考文档

- [deep-research-report.md](./reference_script/deep-research-report.md) - 调研报告原文
- [dom_compressor.py](./reference_script/dom_compressor.py) - 原始 DOM 压缩算法
- Playwright CLI agent snapshots: https://playwright.dev/docs/agent-cli/snapshots
- Playwright aria snapshots: https://playwright.dev/docs/aria-snapshots

### 13.3 决策记录

| 决策 | 选项 A | 选项 B | 选择 | 理由 |
|---|---|---|---|---|
| 压缩插入位置 | JS 提取后 | Python 后处理 | Python 后处理 | 不改 JS，保持 adapter 稳定；Python 侧更易调参 |
| class 处理方式 | 完全忽略 | 清洗后参与 | 清洗后参与 | 完全忽略会降低精度；清洗后平衡稳定性和精度 |
| group 识别方式 | 纯结构 hash | 结构+语义混合 | 结构+语义混合 | 纯结构会误识别；加入 role/context 提升准确率 |
| ref 失效策略 | 自动重解析 | 返回错误让 Agent 处理 | 自动重解析 | 减少 Agent 负担；但保留 fallback 到错误 |
| summary 预算 | 硬限制 | 动态调节 | 硬限制+动态调节 | 硬限制保证 token 可控；动态调节应对不同页面密度 |

### 13.4 API 对齐表（计划方法 vs 现有代码）

以下表格列出升级计划中提及的新方法/新模块与现有代码的映射关系，避免实现期猜测接口。

| 计划中的方法/模块 | 所在文件 | 与现有代码的关系 | 实现方式 |
|---|---|---|---|
| `StructuralHasher.compute()` | `compressor.py` [新增] | 全新实现 | 基于 tag+role+child_sequence 的结构 hash |
| `DOMCompressor.compress()` | `compressor.py` [新增] | 全新实现 | 兄弟节点连续分组检测 |
| `GroupKindDetector.detect()` | `grouper.py` [新增] | 全新实现 | 基于 role/tag/child_pattern 的 group 类型识别 |
| `FieldSchemaExtractor.extract()` | `grouper.py` [新增] | 全新实现 | 从 item 样本中找共同字段 |
| `PaginationDetector.detect()` | `grouper.py` [新增] | 全新实现 | 识别下一页/加载更多按钮 |
| `NodeFingerprint.compute()` | `fingerprint.py` [新增] | 全新实现 | 六维语义签名 |
| `FingerprintIndex.find()` | `fingerprint.py` [新增] | 依赖 `runtime.py` ref 体系 | 内存中的 fingerprint → ref 索引 |
| `LocatorGenerator.generate()` | `locator.py` [新增] | 全新实现 | 生成 6 种定位策略候选 |
| `SummaryProjector.project()` | `projector.py` [新增] | 替换 `service._build_planner_view()` | 生成 agent_summary |
| `ExtractProjector.project()` | `projector.py` [新增] | 全新实现 | 生成 extract 视图 |
| `TokenBudgetEnforcer.enforce()` | `projector.py` [新增] | 全新实现 | 硬预算裁剪 |
| `re_resolve()` | `service.py` [扩展] | 复用现有 `runtime.is_ref_valid()` / `adapter.find_by_locator()` / `service.find_elements()` | 六步重解析流程 |
| `expand` skill | `cli.py` [扩展] | 复用现有 `snapshot` 命令 | `snapshot <ref> --depth N --view full` 的别名 |
| `list-items` skill | `cli.py` [扩展] | 复用 `grouper.py` 输出 | 返回 group schema + item 样本 |
| `extract` skill | `cli.py` [扩展] | 复用 `grouper.py` + `projector.py` | 结构化数据抽取 |
| `resolve-locator` skill | `cli.py` [扩展] | 复用 `fingerprint.py` + `locator.py` | 返回 locator 候选 + 置信度 |
| `eval` skill | `cli.py` [扩展] | 已有基础 | JS 代码执行 |
| `RuntimeContext.upsert_nodes()` | `runtime.py` [扩展] | 已有实现 | 新增 fingerprint 计算调用 |
| `RuntimeContext.find_by_fingerprint()` | `runtime.py` [扩展] | 新增方法 | 查 fingerprint 索引 |
| `SnapshotArtifact` | `models.py` [扩展] | 已有 dataclass | 新增 `groups`, `recovery`, `schema_version` 字段 |
| `SnapshotNodeRecord` | `models.py` [扩展] | 已有 dataclass | 新增 `kind`, `group_ref`, `item_ref`, `fingerprint`, `locator_candidates` 字段 |

**关键保证**: 所有"复用现有代码"的方法都在现有签名基础上扩展，不破坏现有调用。

---

## 14. 下一步行动

1. **立即开始**: P1 冻结主契约（更新 models.py + README）
2. **本周完成**: P2 DOM 压缩引擎（compressor.py）
3. **下周完成**: P3 + P4（fingerprint + summary 重做）
4. **后续排期**: P5-P8 按优先级依次实现

**本计划的关键成功因素**:

- 不推倒重来：充分利用现有 adapter/runtime/session 基础
- 契约先行：先定义 schema，再写实现
- benchmark 驱动：每个阶段都有量化验收标准
- 兼容过渡：旧命令和旧格式保留兼容层

---

## 15. 评审记录

### 15.1 Oracle 评审意见（2026-04-22）

**总体结论**: 计划方向正确，但有条件可行。需修正以下关键问题后方可执行：

1. **P2 插入点修正**: 压缩层应在归一化与投影之间，而非 `_build_planner_view()` 之后
2. **Fingerprint 修正**: 去掉 `group_ref/item_ref`，改用语义锚点（role/name/position type）
3. **兼容层策略**: 采用双输出兼容层（同时输出旧 `planner_view` 和新 `summary`），分阶段迁移
4. **Class 清洗规则**: 重写规则，避免误删 BEM/语义类名，正确识别 CSS Modules/styled-components
5. **Token 控制**: 增加硬预算器（生成后测 token，超限自动裁剪）
6. **压缩双门槛**: 增加语义门槛+结构门槛+交互门槛，误压缩率控制到 <5%
7. **Locator 候选**: 增加 `data-testid/data-qa` 等工程化定位策略
8. **风险评估**: 补充 SPA 局部重渲染、多语言站点的风险

### 15.2 已采纳的修正

- [x] P2 插入点改为 `normalize -> compress -> group -> fingerprint -> project`
- [x] Fingerprint 去掉短期 ref，增加语义锚点
- [x] 兼容层改为三阶段双输出策略
- [x] Class 清洗规则重写，覆盖 BEM/CSS Modules/Tailwind
- [x] 增加 `TokenBudgetEnforcer` 硬预算器
- [x] 压缩增加双门槛（语义+结构+交互）
- [x] Locator candidates 增加 `data-testid/data-qa`
- [x] 风险评估补充 SPA 和多语言
- [x] Extract 模式 token 成本分 sample_only/全量两档
- [x] 章节标题从"七阶段"修正为"八阶段"
- [x] 不引入 `f*` 新 ref 类型，field 节点使用现有 `r*` ref
- [x] adapter.py 明确不修改，ancestor_path 在 Python 侧从 parent_xpath 推导
- [x] benchmark 改为本地 fixture 页面，避免依赖公网
- [x] 分页自动翻页从"可选"提升为"必选项"
- [x] 添加 API 对齐表（计划方法 vs 现有代码映射）
- [x] "无损定位"改为"高成功率恢复定位"，避免过度承诺
- [x] TokenBudgetEnforcer 与 RecoveryInfo 接口对齐（添加默认值）
- [x] re-resolve 伪代码对齐现有代码方法签名

---

*文档版本: v0.5-draft-rev1*  
*最后更新: 2026-04-22*  
*状态: 已评审，待执行*
