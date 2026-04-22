from __future__ import annotations

import pytest

from dp_cli.models import (
    Bounds,
    ContextInfo,
    GroupRecord,
    RecoveryInfo,
    SnapshotArtifact,
    SnapshotNodeRecord,
    Visibility,
)


class TestSnapshotNodeRecordV5Fields:
    def test_new_fields_default_to_empty(self):
        record = SnapshotNodeRecord(
            xpath="/html/body/div[1]",
            ref_type="container",
            tag="div",
        )
        assert record.kind == ""
        assert record.group_ref is None
        assert record.item_ref is None
        assert record.fingerprint == ""
        assert record.locator_candidates == []

    def test_new_fields_roundtrip(self):
        record = SnapshotNodeRecord(
            xpath="/html/body/div[1]",
            ref_type="element",
            tag="a",
            role="link",
            name="Click me",
            kind="control",
            group_ref="r10",
            item_ref="r11",
            fingerprint="fp_abc123",
            locator_candidates=["role=link[name='Click me']"],
        )
        assert record.kind == "control"
        assert record.group_ref == "r10"
        assert record.item_ref == "r11"
        assert record.fingerprint == "fp_abc123"
        assert record.locator_candidates == ["role=link[name='Click me']"]

    def test_to_output_includes_new_fields_when_set(self):
        record = SnapshotNodeRecord(
            xpath="/html/body/div[1]",
            ref_type="element",
            tag="a",
            kind="control",
            group_ref="r10",
            fingerprint="fp_abc123",
        )
        output = record.to_output("e5")
        assert output["kind"] == "control"
        assert output["group_ref"] == "r10"
        assert output["fingerprint"] == "fp_abc123"

    def test_to_output_omits_new_fields_when_empty(self):
        record = SnapshotNodeRecord(
            xpath="/html/body/div[1]",
            ref_type="container",
            tag="div",
        )
        output = record.to_output("r1")
        assert "kind" not in output
        assert "group_ref" not in output
        assert "fingerprint" not in output


class TestGroupRecord:
    def test_group_record_creation(self):
        group = GroupRecord(
            group_ref="r10",
            group_kind="list",
            name="Products",
            item_refs=["r11", "r12", "r13"],
            item_count=24,
            sample_fields=["title", "price"],
            entry_action_refs=["e113"],
        )
        assert group.group_ref == "r10"
        assert group.group_kind == "list"
        assert group.next_page_ref is None


class TestRecoveryInfo:
    def test_recovery_info_defaults(self):
        recovery = RecoveryInfo()
        assert recovery.truncated is False
        assert recovery.expand_candidates == []
        assert recovery.truncation_reason is None

    def test_recovery_info_truncated(self):
        recovery = RecoveryInfo(
            truncated=True,
            truncation_reason="token_budget_exceeded",
            truncation_threshold=1500,
            expand_candidates=["r10"],
        )
        assert recovery.truncated is True
        assert recovery.truncation_threshold == 1500


class TestSnapshotArtifactV5:
    def test_schema_version_default(self):
        artifact = SnapshotArtifact(
            page={"url": "https://example.com"},
            page_identity={"page_id": "page1"},
            mode="agent_summary",
            scope="page",
            root_ref=None,
            depth=None,
            nodes=[],
        )
        assert artifact.schema_version == "0.4"
        assert artifact.groups == []
        assert artifact.recovery == {}

    def test_v5_artifact_fields(self):
        artifact = SnapshotArtifact(
            page={"url": "https://example.com"},
            page_identity={"page_id": "page1"},
            mode="agent_summary",
            scope="page",
            root_ref=None,
            depth=None,
            nodes=[],
            schema_version="0.5",
            groups=[{"group_ref": "r10", "group_kind": "list"}],
            recovery={"truncated": True},
        )
        output = artifact.to_output()
        assert output["schema_version"] == "0.5"
        assert output["groups"] == [{"group_ref": "r10", "group_kind": "list"}]
        assert output["recovery"] == {"truncated": True}
