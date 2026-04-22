import json
import hashlib
import re
from typing import List, Dict, Any, Union, Optional

class DOMCompressor:
    """
    智能 DOM 压缩器
    将重复的 DOM 结构（如列表项）折叠为“模板 + 数据列”的形式，
    显著减少 Token 消耗，同时保留数据索引能力。
    """
    def __init__(self, mode: str = "lite"):
        """
        :param mode: 'lite' (仅保留文本和核心链接) | 'full' (保留所有属性)
        """
        self.mode = mode
        # 定义需要提取的属性字段
        if self.mode == "full":
            self.capture_keys = ["txt", "href", "src", "title", "value", "placeholder", "aria-label", "name", "type", "role"]
        else:
            # Lite 模式：只保留人眼能看到的内容和核心操作属性
            self.capture_keys = ["txt", "href", "title"]

    def compress(self, dom_data: Union[str, Dict]) -> Dict:
        """主入口"""
        if isinstance(dom_data, str):
            try:
                root = json.loads(dom_data)
            except json.JSONDecodeError:
                return {"error": "Invalid JSON string", "raw": dom_data[:100]}
        else:
            root = dom_data

        # 如果 root 是列表（比如多个节点），包裹一层处理
        if isinstance(root, list):
            return self._compress_siblings(root)
        
        return self._traverse_and_compress(root)

    def _traverse_and_compress(self, node: Dict) -> Dict:
        """递归遍历并压缩"""
        # 1. 浅拷贝节点，避免修改原数据影响（如果需要的话，其实这里由于是从 JSON 加载的，直接改也没事）
        new_node = node.copy()
        
        # 2. 优先处理子节点 (Post-order traversal)
        # 这样确保我们在计算当前节点的 Hash 时，子节点已经是通过压缩后的形态参与计算（或者我们只计算子节点的特征）
        # 但要注意：如果子节点被压缩成了 compressed_list，父节点的结构 Hash 可能会变
        if "kids" in new_node and isinstance(new_node["kids"], list):
            # 先递归处理所有子孙
            processed_kids = []
            for k in new_node["kids"]:
                processed_kids.append(self._traverse_and_compress(k))
            
            # 再对当前层级的子节点列表进行压缩
            new_node["kids"] = self._compress_siblings(processed_kids)
            
        return new_node

    def _compress_siblings(self, siblings: List[Dict]) -> List[Dict]:
        """核心压缩逻辑：扫描并聚合兄弟节点"""
        if not siblings or len(siblings) < 3:
            return siblings
            
        result = []
        i = 0
        min_group_size = 3 # 至少3个才压缩
        
        while i < len(siblings):
            current = siblings[i]
            
            # 特殊节点（如 skipped 标记）不参与压缩
            if current.get("t") == "skipped":
                result.append(current)
                i += 1
                continue
                
            current_hash = self._compute_structural_hash(current)
            
            # 向后寻找相同结构的节点
            group = [current]
            j = i + 1
            while j < len(siblings):
                next_sib = siblings[j]
                # 遇到 skipped 就中断
                if next_sib.get("t") == "skipped":
                    break
                    
                if self._compute_structural_hash(next_sib) == current_hash:
                    group.append(next_sib)
                    j += 1
                else:
                    break
            
            # 判定是否压缩
            if len(group) >= min_group_size:
                compressed_node = self._aggregate_group(group)
                result.append(compressed_node)
                i = j # 跳过已处理的一组
            else:
                result.append(current)
                i += 1
                
        return result

    def _compute_structural_hash(self, node: Dict) -> str:
        """
        计算结构指纹。
        指纹由 Tag, Class, 和子节点的 Tag 结构组成。
        注意：不包含具体的文本内容或 href，因为这些是变量。
        """
        if node.get("type") == "compressed_list":
            # 已经是压缩节点了，由其 template 决定
            key = f"compressed_{node.get('template_xpath', 'unknown')}"
            return hashlib.md5(key.encode()).hexdigest()

        parts = [node.get("t", "unknown")]
        
        # 加入 Class (清洗过的 Class 往往代表样式结构)
        if "c" in node:
            parts.append(node["c"])
            
        # 加入直接子节点的 Tag 序列作为结构特征
        # 例如: div -> [img, span, div]
        if "kids" in node and isinstance(node["kids"], list):
            kid_tags = []
            for k in node["kids"]:
                if k.get("t"):
                    kid_tags.append(k.get("t"))
                elif k.get("type") == "compressed_list":
                    kid_tags.append("compressed_list")
            parts.append("|".join(kid_tags))
        
        # 对于 input 类型，type 属性很重要 (text vs checkbox)
        if node.get("t") == "input" and "type" in node:
            parts.append(node["type"])

        raw_key = "_".join(parts)
        return hashlib.md5(raw_key.encode()).hexdigest()

    def _get_node_text(self, node: Dict) -> str:
        """
        [Helper] 递归提取节点及其子孙的文本精华
        """
        # 1. 自身有文本
        if node.get("txt"):
            return node["txt"]
        
        # 2. 如果没有，递归找第一个非空的孩子
        if "kids" in node and isinstance(node["kids"], list):
            for kid in node["kids"]:
                # 如果是 skipped 忽略
                if kid.get("t") == "skipped":
                    continue
                    
                # 如果子节点是压缩列表，从中取样
                if kid.get("type") == "compressed_list":
                     # 尝试取 text 数据的第一个
                     if "data" in kid and "text" in kid["data"] and kid["data"]["text"]:
                         return kid["data"]["text"][0]
                     return ""
                
                # 普通子节点递归
                txt = self._get_node_text(kid)
                if txt:
                    return txt
        
        return ""

    def _aggregate_group(self, group: List[Dict]) -> Dict:
        """将一组节点聚合为一个压缩节点"""
        template = group[0]
        count = len(group)
        
        # 1. 生成模板 XPath
        base_xpath = template.get("x", "")
        # 匹配结尾的 [数字]
        xpath_template = re.sub(r"\[\d+\]$", "[{i}]", base_xpath)
        if xpath_template == base_xpath:
            pass

        # 2. 提取数据列
        data = {}
        # 总是提取文本
        # 扫描所有定义的感兴趣 key
        text_values = [] # 特别保留文本列表用于生成 description
        
        for key in self.capture_keys:
            extracted_values = []
            has_content = False
            
            for item in group:
                val = item.get(key)
                
                # [Deep Text Extraction]
                # 如果是 text 键且当前节点没有值，尝试深度提取
                if key == "txt":
                    if not val:
                        val = self._get_node_text(item)
                    # Normalize text for description
                    text_values.append(val if val else "")
                
                if val:
                    has_content = True
                    extracted_values.append(val)
                else:
                    extracted_values.append("") # 占位对齐
            
            # 只有当该列有至少一个非空值时才保留
            if has_content:
                # [Normalization] 将 'txt' 统一为 'text' 以匹配 Prompt
                out_key = "text" if key == "txt" else key
                
                # 简单优化：如果所有值都一样（例如 class="btn"），则不必列出数组，保留在 template 属性里即可
                unique_vals = set(extracted_values)
                if len(unique_vals) == 1 and list(unique_vals)[0] == "":
                    pass
                else:
                    data[out_key] = extracted_values

        # [CRITICAL Fix] 提取原始索引 (Original Index Extraction)
        # 从 XPath 中提取结尾的索引，例如 .../li[2] -> 2
        indices = []
        has_indices = False
        for item in group:
            xpath = item.get("x", "")
            match = re.search(r"\[(\d+)\]$", xpath)
            if match:
                indices.append(int(match.group(1)))
                has_indices = True
            else:
                indices.append(-1) # 无法提取
        
        if has_indices:
             data["_index"] = indices

        # [Enhancement] 生成可视化的路径描述 [Values]
        # 过滤掉空文本以便阅读
        readable_texts = [t for t in text_values if t]
        # 截断过长的列表显示，避免 Token 爆炸 (LLM 仍可通过 data['text'] 获取全量)
        summary_text = str(readable_texts[:20]) 
        if len(readable_texts) > 20: 
            summary_text = summary_text[:-1] + ", ...]" # 表示还有更多
            
        description = f"{xpath_template} {summary_text}"

        return {
            "type": "compressed_list",
            "count": count,
            "tag": template.get("t"),
            "xpath_template": xpath_template,
            "description": description, # 新增：路径+内容摘要
            "data": data,
            # 保留 kids 确保能看到内部结构
            "kids": template.get("kids", []),
            "sample_attributes": {k:v for k,v in template.items() if k not in ["x", "kids", "txt"] and k in self.capture_keys},
        }
