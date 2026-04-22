from __future__ import annotations
import sys
sys.path.insert(0, r"E:\GitHub\Repositories\drissionpage-cli")

# Final comprehensive validation
print("=" * 60)
print("dp_cli v0.5 Final Validation")
print("=" * 60)

# 1. Module imports
modules = [
    'dp_cli.models',
    'dp_cli.compressor',
    'dp_cli.fingerprint',
    'dp_cli.locator',
    'dp_cli.projector',
    'dp_cli.grouper',
    'dp_cli.cli',
    'dp_cli.service',
]
for mod in modules:
    __import__(mod)
    print(f"PASS: {mod}")

# 2. Models v0.5 fields
from dp_cli.models import SnapshotNodeRecord, GroupRecord, RecoveryInfo, SnapshotArtifact
r = SnapshotNodeRecord(xpath="/x", ref_type="element", tag="a", kind="control",
                       group_ref="r10", fingerprint="fp_123", locator_candidates=["css:#id"])
out = r.to_output("e1")
assert out["kind"] == "control"
assert out["group_ref"] == "r10"
assert out["fingerprint"] == "fp_123"
assert out["locator_candidates"] == ["css:#id"]
print("PASS: SnapshotNodeRecord v0.5 fields")

# 3. Empty fields omitted
r2 = SnapshotNodeRecord(xpath="/x", ref_type="element", tag="a")
out2 = r2.to_output("e2")
assert "kind" not in out2
assert "fingerprint" not in out2
print("PASS: Empty fields omitted")

# 4. GroupRecord & RecoveryInfo
gr = GroupRecord(group_ref="r10", group_kind="list", name="Products",
                 item_refs=["r11", "r12"], item_count=24)
assert gr.group_kind == "list"
ri = RecoveryInfo(truncated=True, truncation_reason="test")
assert ri.truncated is True
print("PASS: GroupRecord & RecoveryInfo")

# 5. SnapshotArtifact v0.5
artifact = SnapshotArtifact(page={}, page_identity={}, mode="agent_summary",
                            scope="page", root_ref=None, depth=None, nodes=[],
                            schema_version="0.5", groups=[{"ref": "r10"}],
                            recovery={"truncated": False})
assert artifact.to_output()["schema_version"] == "0.5"
print("PASS: SnapshotArtifact v0.5")

# 6. Compressor
from dp_cli.compressor import DOMCompressor, CompressionConfig
nodes = []
for i in range(1, 8):
    nodes.append({"ref": f"r{i}", "ref_type": "container", "tag": "div", "role": "listitem",
                  "name": f"Item {i}", "xpath": f"/x[{i}]", "parent_ref": "r0",
                  "visibility": {"visible": True, "in_viewport": True, "interactable_now": False},
                  "context": {"landmark": ""}})
c = DOMCompressor(CompressionConfig(min_group_size=3))
groups = c.compress(nodes)
assert len(groups) >= 1
print(f"PASS: DOMCompressor (found {len(groups)} groups)")

# 7. Fingerprint
from dp_cli.fingerprint import NodeFingerprint
fp = NodeFingerprint()
fp_val = fp.compute({"ref": "e1", "role": "link", "name": "Test", "tag": "a",
                     "xpath": "/x", "parent_ref": None, "context": {"landmark": ""}})
assert len(fp_val) == 16
print(f"PASS: NodeFingerprint ({fp_val[:8]}...)")

# 8. Locator
from dp_cli.locator import LocatorGenerator
lg = LocatorGenerator()
candidates = lg.generate({"ref": "e1", "role": "link", "name": "Test", "tag": "a",
                          "href": "/test", "text": "Click"})
assert len(candidates) >= 2
print(f"PASS: LocatorGenerator ({len(candidates)} candidates)")

# 9. Projector
from dp_cli.projector import SummaryProjector, TokenBudgetEnforcer, RecoveryProjector
sp = SummaryProjector()
rp = RecoveryProjector()
summary = sp.project(nodes, groups, rp.project(nodes))
assert len(summary.global_actions) >= 0
be = TokenBudgetEnforcer()
s2, rec = be.enforce(summary)
assert rec.truncated is False
print("PASS: Projector pipeline")

# 10. Grouper
from dp_cli.grouper import GroupKindDetector, FieldSchemaExtractor
gkd = GroupKindDetector()
kind = gkd.detect({"role": "list", "tag": "ul"}, [])
assert kind == "list"
fse = FieldSchemaExtractor()
fields = fse.extract(["r11"], [{"ref": "e1", "ref_type": "element", "role": "link",
                                "name": "Title", "text": "Title", "href": "/x",
                                "item_ref": "r11", "tag": "a"}])
assert "detail_link" in fields or "title" in fields
print("PASS: Grouper")

# 11. CLI commands
from dp_cli.cli import build_parser
parser = build_parser()
args = parser.parse_args(["snapshot", "--mode", "agent_summary"])
assert args.mode == "agent_summary"
args = parser.parse_args(["expand", "r10"])
assert args.ref == "r10"
args = parser.parse_args(["list-items", "r10"])
assert args.group_ref == "r10"
args = parser.parse_args(["extract", "r10", "--sample-only"])
assert args.sample_only is True
args = parser.parse_args(["resolve-locator", "--ref", "e1"])
assert args.ref == "e1"
print("PASS: CLI new commands")

# 12. Backward compat
args = parser.parse_args(["snapshot", "--view", "planner"])
assert args.view == "planner"
args = parser.parse_args(["snapshot", "--view", "full"])
assert args.view == "full"
print("PASS: Backward compatibility (--view)")

# 13. Service signature
from dp_cli.service import CliService
import inspect
sig = inspect.signature(CliService.snapshot_page)
assert "view" in sig.parameters
assert "mode" in sig.parameters
print("PASS: Service signature")

print("=" * 60)
print("ALL VALIDATIONS PASSED")
print("=" * 60)
