from __future__ import annotations


class LocatorGenerator:
    def generate(self, node: dict) -> list[str]:
        candidates = []
        if node.get("role") and node.get("name"):
            candidates.append(f"role={node['role']}[name='{node['name']}']")
        for test_attr in ["data-testid", "data-test-id", "data-qa", "data-qa-id"]:
            if node.get(test_attr):
                candidates.append(f"[{test_attr}='{node[test_attr]}']")
                break
        if node.get("aria_label"):
            candidates.append(f"[aria-label='{node['aria_label']}']")
        if node.get("href"):
            candidates.append(f"css=a[href*='{node['href'].split('/')[-1]}']")
        if node.get("id") and not node["id"].startswith(("_", "temp")):
            candidates.append(f"css=#{node['id']}")
        if node.get("text") and len(node["text"]) < 50:
            candidates.append(f"text='{node['text']}'")
        if node.get("group_ref") and node.get("item_ref"):
            candidates.append(
                f"group={node['group_ref']} >> item={node['item_ref']} >> "
                f"role={node.get('role', '*')}"
            )
        return candidates
