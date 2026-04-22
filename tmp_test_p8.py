from __future__ import annotations
import sys
sys.path.insert(0, r"E:\GitHub\Repositories\drissionpage-cli")

from dp_cli.models import SnapshotNodeRecord
from dp_cli.compressor import CompressionConfig, DOMCompressor
from dp_cli.fingerprint import NodeFingerprint
from dp_cli.locator import LocatorGenerator
from dp_cli.projector import SummaryProjector, TokenBudgetEnforcer
from dp_cli.grouper import GroupKindDetector, FieldSchemaExtractor

print("All v0.5 modules import successfully")

nodes = [
    {"ref": "r1", "ref_type": "container", "tag": "div", "role": "list", "name": "Products", "xpath": "/html/body/div[1]", "parent_ref": None, "visibility": {"visible": True, "in_viewport": True, "interactable_now": False}, "context": {"landmark": ""}},
]
for i in range(1, 6):
    nodes.append({"ref": f"r{i+1}", "ref_type": "container", "tag": "div", "role": "listitem", "name": f"Item {i}", "xpath": f"/html/body/div[1]/div[{i}]", "parent_ref": "r1", "visibility": {"visible": True, "in_viewport": True, "interactable_now": False}, "context": {"landmark": ""}})
    nodes.append({"ref": f"e{i}", "ref_type": "element", "tag": "a", "role": "link", "name": f"Item {i}", "text": f"Item {i}", "href": f"/item/{i}", "xpath": f"/html/body/div[1]/div[{i}]/a[1]", "parent_ref": f"r{i+1}", "visibility": {"visible": True, "in_viewport": True, "interactable_now": True}, "context": {"landmark": ""}})

compressor = DOMCompressor(CompressionConfig(min_group_size=3))
groups = compressor.compress(nodes)
print(f"Compression: found {len(groups)} groups")

fp = NodeFingerprint()
for node in nodes:
    node["fingerprint"] = fp.compute(node)
    node["locator_candidates"] = LocatorGenerator().generate(node)
print(f"Fingerprint: {nodes[0]['fingerprint'][:8]}...")
print(f"Locator candidates for e1: {nodes[1]['locator_candidates']}")

projector = SummaryProjector()
summary = projector.project(nodes, groups, None)
print(f"Summary: {len(summary.global_actions)} global actions, {len(summary.visible_focus)} visible focus")

enforcer = TokenBudgetEnforcer(max_tokens=1500)
summary2, recovery = enforcer.enforce(summary)
print(f"Token budget: truncated={recovery.truncated}")

print("P8 smoke test passed!")
