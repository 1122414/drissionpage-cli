from __future__ import annotations
import sys
sys.path.insert(0, r"E:\GitHub\Repositories\drissionpage-cli")

from dp_cli.models import SnapshotNodeRecord, GroupRecord, RecoveryInfo, SnapshotArtifact

record = SnapshotNodeRecord(xpath="/html/body/div[1]", ref_type="container", tag="div")
assert record.kind == ""
assert record.group_ref is None
assert record.fingerprint == ""
print("PASS: new_fields_default_to_empty")

record = SnapshotNodeRecord(
    xpath="/html/body/div[1]", ref_type="element", tag="a", role="link", name="Click me",
    kind="control", group_ref="r10", item_ref="r11", fingerprint="fp_abc123",
    locator_candidates=["role=link[name='Click me']"]
)
assert record.kind == "control"
assert record.group_ref == "r10"
assert record.fingerprint == "fp_abc123"
print("PASS: new_fields_roundtrip")

output = record.to_output("e5")
assert output["kind"] == "control"
assert output["group_ref"] == "r10"
assert output["fingerprint"] == "fp_abc123"
print("PASS: to_output_includes_new_fields")

record2 = SnapshotNodeRecord(xpath="/html/body/div[1]", ref_type="container", tag="div")
output2 = record2.to_output("r1")
assert "kind" not in output2
assert "group_ref" not in output2
print("PASS: to_output_omits_empty_fields")

group = GroupRecord(group_ref="r10", group_kind="list", name="Products",
                    item_refs=["r11", "r12"], item_count=24, sample_fields=["title", "price"])
assert group.group_kind == "list"
print("PASS: group_record_creation")

recovery = RecoveryInfo()
assert recovery.truncated is False
print("PASS: recovery_info_defaults")

recovery2 = RecoveryInfo(truncated=True, truncation_reason="token_budget_exceeded", truncation_threshold=1500)
assert recovery2.truncated is True
assert recovery2.truncation_threshold == 1500
print("PASS: recovery_info_truncated")

artifact = SnapshotArtifact(page={"url": "https://example.com"}, page_identity={"page_id": "page1"},
                            mode="agent_summary", scope="page", root_ref=None, depth=None, nodes=[])
assert artifact.schema_version == "0.4"
print("PASS: artifact_schema_version_default")

artifact2 = SnapshotArtifact(page={"url": "https://example.com"}, page_identity={"page_id": "page1"},
                             mode="agent_summary", scope="page", root_ref=None, depth=None, nodes=[],
                             schema_version="0.5", groups=[{"group_ref": "r10"}], recovery={"truncated": True})
output = artifact2.to_output()
assert output["schema_version"] == "0.5"
assert output["groups"] == [{"group_ref": "r10"}]
print("PASS: artifact_v5_fields")

print("\nAll P1 schema tests passed!")
