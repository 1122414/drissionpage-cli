# dp_cli 批量爬取功能分析报告

**生成日期**: 2026-04-28  
**项目版本**: v0.6  
**分析范围**: `dp_cli/` 核心包 + `tests/test_agent_computor.py` 代理循环

---

## 一、概述

dp_cli 的批量爬取采用 **"列表页提取 + 详情页批量抓取" 两阶段设计**。整个流程由一个 LLM 驱动的代理（Agent Loop）编排，通过 CLI 命令与浏览器进行交互。

### 核心设计理念

- **语义快照（Semantic Snapshot）+ ref 驱动** 而非原始 CSS/XPath 选择器
- **数据区域自动检测**——系统自动识别页面中的列表/表格数据容器
- **详情信息通过注入的 JavaScript 脚本提取**，不依赖每个详情页做独立的 LLM 决策

---

## 二、架构概览

```
                    ┌─────────────────────────┐
                    │  test_agent_computor.py  │  ← LLM 代理循环
                    │  (Agent Loop)            │
                    └───────────┬─────────────┘
                                │ CLI commands (subprocess)
                    ┌───────────▼─────────────┐
                    │      dp_cli/cli.py       │  ← argparse 分发
                    │  batch-detail-extract    │
                    └───────────┬─────────────┘
                                │
                    ┌───────────▼─────────────┐
                    │   dp_cli/service.py      │  ← 核心编排层
                    │   batch_extract_detail_  │
                    │   pages()                │
                    └───────────┬─────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        │                       │                       │
┌───────▼──────┐    ┌──────────▼──────────┐   ┌────────▼────────┐
│ adapter.py   │    │   projector.py       │   │  compressor.py   │
│ 浏览器适配器  │    │  ExtractProjector    │   │  DOMCompressor   │
│ snapshot_nodes│    │  SummaryProjector    │   │  结构哈希分组     │
│ extract_detail│    │  TokenBudgetEnforcer │   └─────────────────┘
└──────────────┘    └─────────────────────┘
        │
┌───────▼──────┐
│ 注入的 JS    │
│ SNAPSHOT_    │  ← 页面结构发现
│ SCRIPT       │
│ DETAIL_      │  ← 详情字段提取
│ EXTRACTION_  │
│ SCRIPT       │
└──────────────┘
```

---

## 三、两阶段批量爬取流程

### 阶段一：列表页提取（LLM 代理控制）

LLM 代理通过标准 CLI 命令在列表页上操作：

1. **`open`** → 打开目标网站
2. **`snapshot --mode agent_summary`** → 获取页面语义快照
3. **检查 `data_regions`** → 系统自动检测到的数据容器（列表、表格等）
4. **`extract <ref> --schema title url`** → 从数据区域提取结构化数据
5. **`find --text "下一页"` + `click`** → 翻页
6. **重复步骤 2-5** 直到达到目标页数

**数据区域自动检测算法** (位于 `service.py::_detect_data_regions`):

```python
# 检测逻辑：
# 1. 找到所有 container 节点（ref_type == "container"）
# 2. 找到所有包含 "detail"/"item" 等关键词的链接
# 3. 对每个 container，检查其下有多少个数据链接（需 >= 3 个）
# 4. 按深度排序，优先选择最深且覆盖最多链接的容器
# 5. 返回前 5 个数据区域供 LLM 使用
```

代理从列表页收集到的数据项存储在 `self.list_items` 中，每个 item 包含 `title`、`url`/`detail_url` 等字段。

### 阶段二：详情页批量抓取（确定性执行）

当列表页收集完成（`list_pages_extracted >= target_pages`）且代理标记为 `detail_crawler` 时：

1. 代理调用 `batch-detail-extract` CLI 命令
2. 传入所有列表项的 JSON 数组（包含 `detail_url` 字段）
3. **确定性执行**——不使用 LLM，每个详情页统一用注入的 JS 脚本处理

**核心实现** (`service.py::batch_extract_detail_pages`):

```python
for list_item in list_items:
    # 1. 从 list_item 中提取 detail_url
    detail_url = list_item.get("detail_url") or list_item.get("url")
    
    # 2. 直接访问详情页 URL
    self.adapter.open_url(runtime.tab, detail_url)
    
    # 3. 注入 JS 脚本提取详情字段
    extracted = self.adapter.extract_detail(runtime.tab)
    
    # 4. 合并列表信息和详情信息
    merged = {
        "title": ..., "url": ..., 
        "list_info": {...},    # 列表页获取的信息
        "detail_info": {...},  # 详情页提取的信息
        "detail_ok": True/False
    }
```

---

## 四、批量爬取 URL 的处理方式

### 4.1 URL 来源

批量爬取的 URL **不是通过爬虫自动发现**，而是**从阶段一的列表页提取结果中获取**：

```python
# 在 test_agent_computor.py 中：
def _remember_extracted_items(self, data, page_url):
    items = data.get("items", [])
    for item in items:
        key = self._item_key(item)  # url 或 title 作为去重 key
        if key not in self.extracted_keys:
            self.extracted_keys.add(key)
            self.list_items.append(self._normalize_list_item(item))

def _normalize_list_item(self, item):
    # 确保 detail_url 字段存在
    detail_url = item.get("detail_url") or item.get("url") or item.get("href")
    if detail_url:
        item["detail_url"] = detail_url
    return item
```

### 4.2 URL 处理方式

**直接访问 + JS 注入提取**，没有使用复杂的爬取算法：

| 步骤 | 实现 | 位置 |
|------|------|------|
| 打开详情页 | `tab.get(url)` (DrissionPage) | `adapter.py::open_url` |
| 提取详情信息 | `tab.run_js(DETAIL_EXTRACTION_SCRIPT)` | `adapter.py::extract_detail` |
| 错误处理 | `try/except` 每个 item，单个失败不影响整体 | `service.py:378` |

### 4.3 详情页 JS 提取脚本 (`DETAIL_EXTRACTION_SCRIPT`)

这是批量爬取的核心。它注入一段自执行的 JavaScript，在详情页上提取结构化字段：

```javascript
// 提取策略（位于 adapter.py::DETAIL_EXTRACTION_SCRIPT）：
// 1. 标题: 从 h1, .title, [class*=title], [class*=name] 等选择器获取
// 2. 元数据标签: 扫描页面文本行，匹配中英文标签：
//    - 导演/director, 主演/actors, 类型/category, 地区/region
//    - 年份/year, 语言/language, 上映/release_date, 更新/updated_at
// 3. 描述: 从 [class*=desc], [class*=content], [class*=plot] 等获取
// 4. 封面图: 选取页面最大的可见图片
// 5. 播放链接: 筛选包含 play/m3u8/episode 等关键词的 <a> 标签
```

**关键特征**：这段 JS 是根据目标网站类型（视频/电影网站）硬编码的字段提取逻辑，不是通用的 AI 驱动提取。

---

## 五、数据选择机制

### 5.1 列表页数据选择（阶段一）

**自动检测 + LLM 决策**：

```
1. snapshot 后系统自动计算 data_regions:
   _detect_data_regions(nodes)
   → 返回前5个最可能的列表容器（按深度、链接数量排序）

2. LLM 看到 compact_state 中的 data_regions 信息:
   {
     "data_regions": [
       {"ref": "r3", "item_count": 25, "sample_items": [...]}
     ]
   }

3. LLM 决定: extract r3 --schema title url

4. 如果 LLM 尝试用非数据区域的容器做 extract，
   _guard_extraction_action 会自动纠正到最佳数据区域
```

**`_detect_data_regions` 算法详细步骤** (`service.py:758-812`):

1. 筛选所有 `ref_type == "container"` 且有 xpath 的节点
2. 筛选所有"可提取的详情链接"（`_is_extractable_item_link`）：
   - `ref_type == "element"` 且 `role == "link"`
   - href 包含 `detail`、`/vod/`、`/movie/`、`/video/`、`/item/` 等关键词
   - 排除分页链接（`vod-show`、`vod-type`、`year-`、`area-`、`class-`、`page-`）
   - 排除导航文本（"首页"、"下一页"、纯数字）
3. 对每个 container，统计其 xpath 子树下的详情链接数（需 ≥ 3）
4. 排序：`(-depth, text_len, -item_count)` ——优先选最深、文本最少、链接最多的容器
5. 返回前 5 个

### 5.2 详情页数据选择（阶段二）

**硬编码字段匹配**——不涉及用户选择：

```javascript
// DETAIL_EXTRACTION_SCRIPT 中的字段标签映射：
const labels = [
    ['director',  ['director', '导演']],
    ['actors',    ['actor', 'actors', 'cast', '主演', '演员']],
    ['category',  ['category', 'genre', 'type', '类型']],
    ['region',    ['region', 'area', '地区']],
    ['year',      ['year', '年份', '年代']],
    ['language',  ['language', '语言']],
    ['release_date', ['release', '上映', '发行']],
    ['updated_at',   ['update', 'updated', '更新', '更新时间']]
];
```

### 5.3 Schema 过滤（`extract` 命令）

当用户指定 `--schema title url author` 时，`ExtractProjector` 会：
1. 只返回匹配 schema 中字段名的键值对
2. 如果 schema 包含 url/href/link 字段，过滤掉没有 URL 的项
3. 未指定 schema 时自动检测字段结构

---

## 六、数据提取算法详解

### 6.1 ExtractProjector（`projector.py`）

这是列表页数据提取的核心类。支持两种提取模式：

#### 从容器提取 (`_extract_from_containers`)
- 对每个容器 ref，BFS 遍历其所有子节点
- `_build_item()` 合并子节点信息为单条记录：
  - **URL 优先级排序**：外部链接 > 普通链接 > 噪声链接
  - **标题选择**：取最长的文本（> 3字符）
  - URL 归一化：相对路径 → 绝对 URL

#### 从元素提取 (`_extract_from_elements`)
- 如果元素是链接且符合详情页特征 → `_extract_link_items()`
  - 过滤规则：排除导航链接、分页链接、噪声链接
  - 去重：按 `url|title|name|text` 签名去重
- 否则按父节点分组，用 xpath 行分组算法处理表格行

#### xpath 行分组算法 (`_group_by_xpath_row`)
```python
# 将元素按 xpath 中的索引位置分组
# 例如 /html/body/div[2]/a[1] 和 /html/body/div[2]/a[2]
# 分到同一组 /html/body/div[2]（截取倒数第二个索引位置）
```

### 6.2 DOMCompressor（`compressor.py`）

在列表页提取前对 DOM 节点进行结构压缩：

1. **结构哈希** (`StructuralHasher`): 对每个节点的 tag + role + 子节点签名进行 SHA256 哈希
2. **连续哈希分组** (`_group_by_hash`): 相同结构的连续节点分到一组
3. **压缩条件判断** (`_should_compress`):
   - 组大小 ≥ 3
   - 父节点需为语义容器（list/table/grid/tree/region/main/navigation）
   - 元素 role 一致性 ≥ 85%

这使系统能识别出"这是一个包含 25 个结构相同项的列表"。

### 6.3 Detail 提取 JS（`DETAIL_EXTRACTION_SCRIPT`）

不走 DOM 树遍历，而是：

1. **CSS 选择器优先** 匹配标题、描述等结构化区域
2. **文本行扫描** 按 `\n` 分割 `document.body.innerText`，逐行匹配标签
3. **图片面积排序** 选取最大可见图片作为封面
4. **链接关键词过滤** 筛选播放相关链接

---

## 七、LLM 代理如何编排批量爬取

### 7.1 场景触发

在 `test_agent_computor.py` 中定义场景：

```python
SCENARIOS = {
    "scrawl_info": "去 https://www.wangfei.la/，进入左侧电影栏目，
                    爬取前两页的电影信息，注意要点进每一部电影去获取其详情信息，
                    并存储为json文件"
}
```

### 7.2 代理决策流程

```
1. LLM 解析目标 → 识别 URL、任务类型、目标页数
   plan_goal() → 返回 {"task_type": "detail_crawler", "url": "...", ...}

2. 主循环 (每步):
   a. snapshot → 获取页面状态（compact_state）
   b. LLM 决策 → 返回下一步 action
   c. 执行 action → 记录结果
   
3. 爬取特定逻辑:
   - _goal_requests_detail_crawl() 检测是否需要详情爬取
   - _target_page_count() 解析目标页数
   - _remember_extracted_items() 累积提取的列表项
   - _detail_crawler_ready_for_batch() 检查是否可启动批量详情提取

4. 当条件满足时:
   _run_detail_batch() → executor.batch_detail_extract(items)
   → 不经过 LLM，确定性执行
```

### 7.3 安全守卫

| 守卫 | 位置 | 作用 |
|------|------|------|
| `_guard_extraction_action` | agent | 强制用 data_region 而非侧边栏 |
| `_guard_verification_action` | agent | 防止 LLM 猜测验证码 |
| `_is_duplicate_action` | agent | 检测重复操作 |
| `_continuation_action_for_extraction` | agent | LLM 说 stop 但爬取未完成时自动继续 |

---

## 八、关键技术细节

### 8.1 Token 预算控制

`TokenBudgetEnforcer` (projector.py) 确保 LLM 的 context 不超出限制：
- 默认 `max_tokens = 1500`
- 超出时逐步裁剪：sample_items → visible_focus → global_actions
- 保留关键交互元素（textbox, searchbox, button, link）

### 8.2 容错设计

```python
# 每个详情页独立 try/except，单个失败不影响批量
except Exception as exc:
    merged["detail_error"] = str(exc)
```

### 8.3 去重机制

- 列表项按 `detail_url` 或 `title` 去重（`_item_key`）
- 详情项按 `url|title|name|text` 签名去重

### 8.4 会话持久化

批量爬取过程中，每次操作后调用 `runtime.persist()` 保存状态到 `.dpcli/sessions/<name>/state.json`，支持断点续爬。

---

## 九、数据流总结

```
用户目标 (自然语言)
    │
    ▼
LLM 解析 (plan_goal)
    │
    ▼
┌─ 阶段一: 列表页提取 ─────────────────────────┐
│                                                │
│  open URL                                       │
│    ↓                                            │
│  snapshot (注入 SNAPSHOT_SCRIPT)                │
│    ↓                                            │
│  _detect_data_regions (自动识别列表容器)        │
│    ↓                                            │
│  extract rN --schema title url                  │
│    ↓ (ExtractProjector)                         │
│  结构化数据 [{title, url}, ...]                  │
│    ↓                                            │
│  find "下一页" → click → 重复                   │
│    ↓                                            │
│  list_items 累积 (去重)                         │
└────────────────────────────────────────────────┘
    │
    ▼
┌─ 阶段二: 详情页批量提取 ───────────────────────┐
│                                                │
│  batch-detail-extract (确定性，无 LLM)          │
│    ↓                                            │
│  for each item:                                │
│    open_url(detail_url)                        │
│    tab.run_js(DETAIL_EXTRACTION_SCRIPT)        │
│      ↓                                         │
│    提取 title, director, actors, category,      │
│    region, year, description, cover, play_urls  │
│    ↓                                            │
│  合并 list_info + detail_info → merged item     │
│    ↓                                            │
│  输出 JSON:                                     │
│  {                                              │
│    task_type: "detail_crawler",                 │
│    items: [{title, url, list_info,              │
│             detail_info, detail_ok}, ...]        │
│  }                                              │
└────────────────────────────────────────────────┘
```

---

## 十、与常规爬虫框架的对比

| 特性 | dp_cli 批量爬取 | Scrapy | Playwright 手写 |
|------|----------------|--------|----------------|
| 选择器方式 | 语义 ref + data_region 自动检测 | CSS/XPath 手写 | CSS/XPath 手写 |
| 翻页 | LLM 自动识别"下一页"控件 | 手写规则 | 手写规则 |
| 详情提取 | 注入的 JS 脚本（目标网站特定） | 手写解析器 | 手写逻辑 |
| 适应性 | 仅限电影/视频类网站（硬编码字段） | 通用 | 通用 |
| 容错 | 单 item 失败不影响整体 | 需手写中间件 | 需手写 |
| LLM 依赖 | 阶段一依赖 LLM，阶段二不依赖 | 无 | 无 |
| 适用场景 | Agent 驱动的自动化爬取 | 大规模定向爬取 | 精确控制的爬取 |

---

## 十一、关键文件索引

| 文件 | 行数 | 核心功能 |
|------|------|----------|
| `dp_cli/adapter.py` | 568-697 | `DETAIL_EXTRACTION_SCRIPT` 详情提取 JS |
| `dp_cli/adapter.py` | 128-551 | `SNAPSHOT_SCRIPT` 页面结构发现 JS |
| `dp_cli/adapter.py` | 805-806 | `extract_detail()` 方法 |
| `dp_cli/service.py` | 325-394 | `batch_extract_detail_pages()` 批量详情爬取 |
| `dp_cli/service.py` | 758-812 | `_detect_data_regions()` 数据区域自动检测 |
| `dp_cli/service.py` | 814-831 | `_is_extractable_item_link()` 链接筛选 |
| `dp_cli/service.py` | 263-297 | `extract_group()` 列表页数据提取 |
| `dp_cli/projector.py` | 82-134 | `ExtractProjector.project()` 提取投影器 |
| `dp_cli/projector.py` | 136-234 | 容器/元素/链接提取算法 |
| `dp_cli/projector.py` | 265-283 | xpath 行分组算法 |
| `dp_cli/projector.py` | 285-348 | `_build_item()` 单条记录构建 |
| `dp_cli/compressor.py` | 54-144 | `DOMCompressor` 结构哈希压缩 |
| `dp_cli/grouper.py` | 4-16 | `GroupKindDetector` 组类型检测 |
| `dp_cli/grouper.py` | 19-48 | `FieldSchemaExtractor` 字段推断 |
| `dp_cli/cli.py` | 73-84 | `batch-detail-extract` CLI 命令注册 |
| `dp_cli/cli.py` | 201-213 | 命令分发 |
| `tests/test_agent_computor.py` | 863-898 | `_remember_extracted_items()` 列表项累积 |
| `tests/test_agent_computor.py` | 959-990 | `_run_detail_batch()` 批量详情触发 |
| `tests/test_agent_computor.py` | 1154-1337 | 主循环中的爬取逻辑 |
