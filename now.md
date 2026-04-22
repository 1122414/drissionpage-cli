# dp_cli Now

这份文档用于记录 `dp_cli` 当前已经实现到什么程度、现在实际是怎么工作的、以及目前最明显的问题是什么。

它不是路线图，也不是对外宣传文档，而是一份**当前状态快照**。

## 1. 项目现在是什么

`dp_cli` 现在是一个基于 DrissionPage 的 Agent-first CLI 原型。

目标方向已经比较明确：

- 不做传统“脚本库套一层命令行”
- 主交互合同尽量靠：
  - `snapshot`
  - `ref`
  - `click/type --ref`
- 尝试参考 Playwright CLI 的工作流：
  - 先获取页面语义快照
  - 再用 `ref` 操作元素
  - 必要时再局部展开，而不是每步都猜 selector

当前代码还处于 **MVP + 多轮试错后的原型期**，核心能力已经能跑，但 Agent 真实稳定性还没有收敛。

## 2. 当前已经实现的能力

当前 CLI 命令有：

- `open`
- `snapshot`
- `find`
- `click`
- `type`
- `session inspect`

统一输出 JSON 顶层结构：

- `ok`
- `session`
- `action`
- `data`
- `error`

### 2.1 session / runtime

已经实现了最小可持续会话机制：

- `--session <name>` 作为逻辑会话入口
- 内部有：
  - `session_id`
  - `runtime_id`
  - `page_id`
  - `snapshot_id`
- 已经处理过这些稳定性问题：
  - stale tab id 导致的 404
  - 活 session 在 headed / headless 间漂移
  - 旧页面 ref 被跨页面误复用

状态主要落盘在：

- `.dpcli/sessions/<session>/`
- `.dpcli/snapshots/<session>/`

### 2.2 ref 驱动交互

当前主路径已经不是“靠 text 或 locator 猜点”，而是：

1. `snapshot`
2. 获得 `ref`
3. `click --ref`
4. `type --ref`

目前有两类 ref：

- `r*`
  - container / semantic container
- `e*`
  - interactive element

规则：

- `snapshot` 可以接受 `r*` / `e*`
- `click/type` 只接受 `e*`
- 对 container ref 做 `click/type` 会返回 `invalid_ref_type`

### 2.3 snapshot

当前 `snapshot` 已经不是最初的“直接把当前视口前面几个元素吐出来”，而是：

1. 先做一轮**全页发现**
2. 再生成一个给 Agent 用的低 token 视图

目前 CLI 层的默认输出还是：

- `planner_view`
  - `pinned_controls`
  - `viewport_nodes`
  - `condensed_groups`
  - `stats`
  - `omitted_summary`

并支持：

- `snapshot --view full`
  - 返回完整发现图
- `snapshot <ref> --view full --depth N`
  - 对局部子树做展开

### 2.4 find

`find --text` 目前已经不是只看当前视口。  
它会基于**全页发现图**进行文本匹配，并返回匹配节点。

也就是说，“当前屏幕没看到”不等于“find 找不到”。

### 2.5 click / type

执行层当前的策略是：

1. 校验 ref 仍属于当前页面
2. 拿到目标元素
3. 如果当前不可直接操作，尝试 `scrollIntoView`
4. 再检查是否可操作
5. 执行动作

所以从执行层角度看：

- **元素不在当前视口里，不等于不能点**

这是当前项目里一个非常重要的事实。

## 3. 当前 Agent loop 是怎么工作的

当前最小 Agent loop 脚本在：

- [scripts/test_min_agent_loop.py](</E:/GitHub/Repositories/drissionpage-cli/scripts/test_min_agent_loop.py>)

它现在使用：

- `langchain_openai.ChatOpenAI`

模型配置写在脚本内部：

- `OPENAI_CONFIG["api_key"]`
- `OPENAI_CONFIG["base_url"]`
- `OPENAI_CONFIG["model"]`

当前默认兼容的是：

- `base_url = https://dashscope.aliyuncs.com/compatible-mode/v1`
- `model = kimi-k2.5`

### 3.1 之前的问题

之前的脚本直接把原始 `planner_view` 喂给模型，问题很明显：

- 分组结构偏内部，不够像真正给 LLM 的输入
- 空字段、统计字段、无关节点太多
- 模型明明已经看到了 `ref`，还会退回 `find_text`
- `in_viewport=false` 会把模型误导成“先别点”

### 3.2 现在脚本层做过的修正

脚本里现在已经新增了一层 **`llm_view`**，这层目前只存在于 agent loop，不是 CLI 正式公开接口。

当前 `llm_view` 的方向是：

- 不再把原始 `planner_view` 直接给模型
- 改成：
  - `state`
  - `nodes`

其中：

- `state`
  - 当前 URL
  - 标题
  - 页面身份最小信息
  - 历史动作摘要
- `nodes`
  - 当前少量候选节点
  - 只保留决策字段

脚本层已经尝试遵守这几个原则：

- 给模型看的字段极少
- 有 ref 就直接点
- 缺目标才 `find_text`
- `find_text` 命中多个结果时，不再盲点第一个，而是把候选重新交给模型判断

## 4. 当前最大的实际问题

虽然上面这些东西都已经实现了一部分，但**Agent 真实稳定性仍然不够**。

目前最核心的问题有 5 个。

### 4.1 snapshot 默认视图仍然太偏系统内部

虽然现在已经有 `planner_view` 和脚本层的 `llm_view`，但从产品合同角度看，仍然没有真正收敛成：

- 一份稳定、低噪音、适合 LLM 直接消费的官方输入视图

现在的状态更像：

- CLI 默认输出一个内部工作视图
- 脚本再自己做二次压缩

这说明“给 LLM 看什么”这件事还没有在 `dp_cli` 核心层定型。

### 4.2 模型仍然可能“看见 ref 但不用 ref”

这是目前最实际的问题之一。

例如：

- “下一页”明明在当前输入里已经有 ref
- 但模型仍然会选择 `find_text`

这说明目前的问题不只在页面发现，还在：

- 输入视图设计
- prompt 规则
- 动作约束

这三层还没有形成稳定闭环。

### 4.3 分层展开策略还没有彻底定型

现在项目里已经有这些东西：

- `snapshot`
- `snapshot --view full`
- `snapshot <ref> --depth N`
- `find --text`

但“默认该给模型看哪一层、什么时候该展开、什么时候该 find”这套策略还在试错中。

当前比较明确的方向是：

1. 默认只给少量关键节点
2. 当前层没有目标才 `find`
3. 仍不明确再局部展开
4. 一旦有 ref，直接执行

但这套策略还没有完全固化成项目主合同。

### 4.4 大页面下，哪些节点该优先进入默认视图，仍然很关键

比如 `wfei.la` 这种页面：

- 有导航
- 有分页
- 有大量电影卡片

如果默认层里被卡片淹没，模型就会看不到真正关键的“电影”“下一页”。

当前已经做了：

- `pinned_controls`
- `condensed_groups`

但实际效果仍然需要继续打磨，因为：

- 哪些节点必须永不省略
- 哪些节点应压缩成容器入口
- 哪些节点应进入默认动作层

这三件事还没有完全稳定。

### 4.5 测试环境和真实网站验证还有落差

本地 fixture 和部分本地工作流是能跑的。  
但真实网站 + 真实 LLM + 真实长任务下的稳定性，仍然没有完全验证透。

尤其像：

- 连续三次翻页
- 页面变化后保持正确目标感知
- 不重复已完成动作

这类真实 Agent 行为，仍然是当前最需要继续验证的部分。

## 5. 当前文件结构大致职责

核心包：

- [dp_cli/cli.py](</E:/GitHub/Repositories/drissionpage-cli/dp_cli/cli.py>)
  - CLI 参数解析与命令分发
- [dp_cli/service.py](</E:/GitHub/Repositories/drissionpage-cli/dp_cli/service.py>)
  - 主要 use case 编排
- [dp_cli/adapter.py](</E:/GitHub/Repositories/drissionpage-cli/dp_cli/adapter.py>)
  - DrissionPage 交互适配、快照发现、元素状态
- [dp_cli/runtime.py](</E:/GitHub/Repositories/drissionpage-cli/dp_cli/runtime.py>)
  - runtime / ref / snapshot 元数据持久化
- [dp_cli/session.py](</E:/GitHub/Repositories/drissionpage-cli/dp_cli/session.py>)
  - session 管理与浏览器恢复
- [dp_cli/session_store.py](</E:/GitHub/Repositories/drissionpage-cli/dp_cli/session_store.py>)
  - session 文件路径与持久化基础设施
- [dp_cli/models.py](</E:/GitHub/Repositories/drissionpage-cli/dp_cli/models.py>)
  - 数据模型

脚本：

- [scripts/test_local_cli.py](</E:/GitHub/Repositories/drissionpage-cli/scripts/test_local_cli.py>)
  - 本地 CLI 工作流 smoke
- [scripts/test_public_smoke.py](</E:/GitHub/Repositories/drissionpage-cli/scripts/test_public_smoke.py>)
  - 公网 smoke
- [scripts/test_min_agent_loop.py](</E:/GitHub/Repositories/drissionpage-cli/scripts/test_min_agent_loop.py>)
  - 最小自然语言 Agent loop
- [scripts/test_langchain_openai_connection.py](</E:/GitHub/Repositories/drissionpage-cli/scripts/test_langchain_openai_connection.py>)
  - LangChain OpenAI 连接测试

测试：

- [tests/test_cli_local.py](</E:/GitHub/Repositories/drissionpage-cli/tests/test_cli_local.py>)
  - 本地 CLI 集成测试
- [tests/test_agent_loop_view.py](</E:/GitHub/Repositories/drissionpage-cli/tests/test_agent_loop_view.py>)
  - agent loop 输入视图的纯 Python 测试
- [tests/support.py](</E:/GitHub/Repositories/drissionpage-cli/tests/support.py>)
  - 测试辅助方法

## 6. 当前验证现状

当前至少做过这些验证：

- CLI 语法与基本命令链路可跑
- 本地 fixture 下：
  - `open`
  - `snapshot`
  - `find`
  - `click`
  - `type`
  - session inspect
  能形成闭环
- session 稳定性的一些关键 bug 已修过
- `test_min_agent_loop.py` 已经切到 `langchain_openai.ChatOpenAI`

但也要诚实记录：

- 当前这台环境里 `pytest` 有一部分集成测试会因为全局浏览器探针被统一跳过
- 所以很多验证仍然依赖：
  - 单独脚本运行
  - 手动真实站点验证
  - 纯 Python 单元测试

这意味着：

- 当前“代码逻辑正确”与“真实 Agent 行为稳定”之间，仍然有一段距离

## 7. 现在最值得继续想清楚的问题

如果接下来继续推进，这几个问题最关键：

1. `dp_cli` 核心层是否要正式引入一个稳定的 `llm_view` 合同  
   还是继续让脚本层自己压缩 `snapshot`。

2. 默认动作层到底应该包含哪些节点  
   例如：
   - 导航
   - 分页
   - 表单主控件
   - 当前上下文主按钮
   这套优先级是否要正式固化。

3. `find` 和 `snapshot <ref>` 的职责边界是否要再收紧  
   也就是：
   - 缺目标先 find
   - 歧义再展开
   这套策略是否要成为硬规则。

4. 对 LLM 来说，“有 ref 就直接点”是否还需要更强的约束  
   例如在脚本层进一步减少模型可自由发挥的空间。

5. 是否要继续往 Playwright CLI 的方向靠近  
   即：
   - 更轻的默认输入
   - 更明确的局部展开
   - 更少的系统内部视图暴露给模型

## 8. 一句话结论

`dp_cli` 现在已经不是一个“空想项目”，而是一个：

- CLI 主体已经成型
- ref 驱动交互已经跑通
- snapshot / find / click / type / session 机制已经具备
- 但**Agent 输入合同和默认探索策略仍然没有完全收敛**

当前最需要继续做的，不是再堆更多命令，而是把下面这三件事彻底做稳：

- 给模型看什么
- 有 ref 后怎么避免反复确认
- 大页面时怎么分层展开，而不是让模型在噪音里瞎猜
