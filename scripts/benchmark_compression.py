from __future__ import annotations

import json

from dp_cli.compressor import CompressionConfig, DOMCompressor


def build_test_nodes():
    nodes = []
    nodes.append({
        "ref": "r1", "ref_type": "container", "tag": "div", "role": "list",
        "name": "Products", "xpath": "/html/body/div[1]", "parent_ref": None,
        "visibility": {"visible": True, "in_viewport": True, "interactable_now": False},
    })
    for i in range(1, 25):
        item_ref = f"r{i+1}"
        nodes.append({
            "ref": item_ref, "ref_type": "container", "tag": "div", "role": "listitem",
            "name": f"Product {i}", "xpath": f"/html/body/div[1]/div[{i}]",
            "parent_ref": "r1", "parent_xpath": "/html/body/div[1]",
            "visibility": {"visible": True, "in_viewport": i <= 5, "interactable_now": False},
        })
        nodes.append({
            "ref": f"e{i*2-1}", "ref_type": "element", "tag": "a", "role": "link",
            "name": f"Product {i}", "text": f"Product {i}", "href": f"/product/{i}",
            "xpath": f"/html/body/div[1]/div[{i}]/a[1]",
            "parent_ref": item_ref, "parent_xpath": f"/html/body/div[1]/div[{i}]",
            "visibility": {"visible": True, "in_viewport": i <= 5, "interactable_now": True},
        })
        nodes.append({
            "ref": f"e{i*2}", "ref_type": "element", "tag": "span", "role": "text",
            "name": f"Price {i}", "text": f"${i*10}",
            "xpath": f"/html/body/div[1]/div[{i}]/span[1]",
            "parent_ref": item_ref, "parent_xpath": f"/html/body/div[1]/div[{i}]",
            "visibility": {"visible": True, "in_viewport": i <= 5, "interactable_now": False},
        })
    nodes.append({
        "ref": "e100", "ref_type": "element", "tag": "input", "role": "textbox",
        "name": "Search", "xpath": "/html/body/input[1]", "parent_ref": None,
        "visibility": {"visible": True, "in_viewport": True, "interactable_now": True},
    })
    return nodes


def count_tokens(nodes):
    json_str = json.dumps(nodes, ensure_ascii=False)
    return len(json_str) // 4


def main():
    nodes = build_test_nodes()
    original_tokens = count_tokens(nodes)
    print(f"Original nodes: {len(nodes)}")
    print(f"Original tokens (est.): {original_tokens}")

    compressor = DOMCompressor(CompressionConfig(min_group_size=3))
    groups = compressor.compress(nodes)

    print(f"\nCompressed groups found: {len(groups)}")
    for g in groups:
        print(f"  - {g.tag} (role={g.role}): {g.count} items, refs={g.member_refs}")

    compressed_nodes = []
    compressed_refs = set()
    for g in groups:
        for ref in g.member_refs:
            compressed_refs.add(ref)
        for node in nodes:
            if node.get("parent_ref") in compressed_refs:
                compressed_refs.add(node["ref"])
        compressed_nodes.append({
            "ref": g.representative_ref,
            "ref_type": "container",
            "tag": g.tag,
            "role": g.role,
            "compressed": True,
            "count": g.count,
            "member_refs": g.member_refs,
            "xpath_template": g.xpath_template,
        })
    for node in nodes:
        if node["ref"] not in compressed_refs:
            compressed_nodes.append(node)

    compressed_tokens = count_tokens(compressed_nodes)
    print(f"\nCompressed nodes: {len(compressed_nodes)}")
    print(f"Compressed tokens (est.): {compressed_tokens}")
    if original_tokens > 0:
        savings = (original_tokens - compressed_tokens) / original_tokens * 100
        print(f"Token savings: {savings:.1f}%")


if __name__ == "__main__":
    main()
