from __future__ import annotations

import json
import random
import re
import sys
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

from dp_cli.adapter import DrissionPageAdapter
from dp_cli.ai_extractor import AiDetailExtractor
from dp_cli.compressor import CompressedGroup, CompressionConfig, DOMCompressor
from dp_cli.errors import (
    ElementNotFoundError,
    ElementNotInteractableError,
    InvalidInputError,
    InvalidRefTypeError,
    RefNotFoundError,
    RefStaleError,
)
from dp_cli.models import DEFAULT_SESSION, SNAPSHOT_DEFAULT_DEPTH, SnapshotArtifact
from dp_cli.record_projection import group_record_nodes
from dp_cli.session import SessionManager

PAGINATION_KEYWORDS = {
    "first",
    "last",
    "next",
    "prev",
    "previous",
    "nextpage",
    "prevpage",
    "previouspage",
    "firstpage",
    "lastpage",
    "首页",
    "上一页",
    "下一页",
    "尾页",
}

PRIMARY_ACTION_KEYWORDS = {
    "search",
    "submit",
    "save",
    "confirm",
    "login",
    "搜索",
    "提交",
    "保存",
    "确认",
    "登录",
}



class CliService:
    def __init__(self, sessions: SessionManager | None = None, adapter: DrissionPageAdapter | None = None) -> None:
        self.sessions = sessions or SessionManager()
        self.adapter = adapter or DrissionPageAdapter()

    def open_page(
        self,
        url: str,
        session: str = DEFAULT_SESSION,
        headless: bool | None = None,
        wait_time: float = 0.0,
    ) -> dict:
        with self._with_runtime(session=session, headless=headless) as runtime:
            page = self.adapter.open_url(runtime.tab, url)
            runtime.sync_page_identity()
            self._wait(wait_time)
            runtime.persist()
            return {"page": page}

    def snapshot_page(
        self,
        session: str = DEFAULT_SESSION,
        ref: str | None = None,
        depth: int | None = None,
        headless: bool | None = None,
        view: str | None = None,
        mode: str = "agent_summary",
        wait_time: float = 0.0,
    ) -> dict:
        if view is not None:
            mode = "full" if view == "full" else "agent_summary"
        if mode not in {"full", "agent_summary", "extract"}:
            raise InvalidInputError("snapshot --mode must be one of: full, agent_summary, extract.")

        snapshot_depth = depth if depth is not None else (SNAPSHOT_DEFAULT_DEPTH if ref else None)
        scope = "subtree" if ref else "page"

        with self._with_runtime(session=session, headless=headless) as runtime:
            self._wait(wait_time)
            runtime.begin_snapshot()
            root_ref = ref
            root_xpath = None
            if ref is not None:
                item = self._ref_item(runtime, ref)
                root_xpath = item["xpath"]

            records = self.adapter.snapshot_nodes(runtime.tab, root_xpath=root_xpath, depth=snapshot_depth)
            nodes = runtime.upsert_nodes(records, track_delta=ref is None)
            index = self._build_index(nodes)

            payload = {
                "schema_version": "0.6",
                "mode": mode,
                "page": self._page_payload(runtime),
                "page_identity": self._page_identity_payload(runtime),
                "scope": scope,
                "root_ref": root_ref,
                "depth": snapshot_depth,
                "index": index,
                "delta": dict(runtime.state.last_snapshot_diff or {}),
            }
            artifact_file = self._write_snapshot_artifact(
                session=session,
                artifact=SnapshotArtifact(
                    page=payload["page"],
                    page_identity=payload["page_identity"],
                    mode=mode,
                    scope=scope,
                    root_ref=root_ref,
                    depth=snapshot_depth,
                    nodes=nodes,
                    planner_view=index,
                    schema_version="0.6",
                    delta=payload["delta"],
                ),
                snapshot_id=runtime.state.active_page.snapshot_id or "snapshot",
            )
            runtime.remember_snapshot(artifact_file, mode)
            runtime.persist()
            payload["artifact_file"] = artifact_file

            if mode == "full":
                payload["count"] = len(nodes)
                payload["nodes"] = nodes
            return payload

    def find_elements(
        self,
        session: str = DEFAULT_SESSION,
        locator: str | None = None,
        text: str | None = None,
        headless: bool | None = None,
        wait_time: float = 0.0,
    ) -> dict:
        if not locator and not text:
            raise InvalidInputError("find requires either --locator or --text.")
        with self._with_runtime(session=session, headless=headless) as runtime:
            self._wait(wait_time)
            runtime.begin_snapshot()
            if locator:
                records = self.adapter.find_by_locator(runtime.tab, locator)
                nodes = runtime.upsert_nodes(records)
            else:
                nodes = runtime.upsert_nodes(self.adapter.snapshot_nodes(runtime.tab, depth=None))
                nodes = self._filter_text_matches(nodes, text or "")
            runtime.persist()
            return {
                "page": self._page_payload(runtime),
                "page_identity": self._page_identity_payload(runtime),
                "count": len(nodes),
                "nodes": nodes,
                "query": {"locator": locator, "text": text},
            }

    def click_element(
        self,
        session: str = DEFAULT_SESSION,
        ref: str | None = None,
        locator: str | None = None,
        headless: bool | None = None,
        wait_time: float = 0.0,
    ) -> dict:
        return self._perform_element_action(
            session=session,
            headless=headless,
            ref=ref,
            locator=locator,
            element_error_message="Could not find element to click.",
            action=lambda element, _text: self.adapter.click(element),
            wait_time=wait_time,
            include_payload=lambda runtime, target_locator, state: {
                "page": self._page_payload(runtime),
                "target": self._target_payload(ref, target_locator),
                "target_state": state,
            },
        )

    def type_into_element(
        self,
        text: str,
        session: str = DEFAULT_SESSION,
        ref: str | None = None,
        locator: str | None = None,
        submit: bool = False,
        headless: bool | None = None,
        wait_time: float = 0.0,
    ) -> dict:
        return self._perform_element_action(
            session=session,
            headless=headless,
            ref=ref,
            locator=locator,
            text=text,
            element_error_message="Could not find element to type into.",
            action=lambda element, value: self.adapter.type_text(
                element,
                value or "",
                submit=submit,
            ),
            wait_time=wait_time,
            include_payload=lambda runtime, target_locator, state: {
                "page": self._page_payload(runtime),
                "target": self._target_payload(ref, target_locator),
                "target_state": state,
                "typed_text": text,
                "submitted": bool(submit),
            },
        )

    def scroll_page(
        self,
        session: str = DEFAULT_SESSION,
        direction: str = "down",
        amount: int = 900,
        to: str | None = None,
        headless: bool | None = None,
        wait_time: float = 0.0,
        ready_condition: str | None = None,
        ready_locator: str | None = None,
        ready_timeout: float | None = 10.0,
    ) -> dict:
        normalized_direction = str(direction or "down").lower()
        normalized_to = str(to or "").lower() or None
        if normalized_direction not in {"down", "up", "left", "right"}:
            raise InvalidInputError(
                "scroll --direction must be one of: down, up, left, right."
            )
        if normalized_to not in {
            None,
            "top",
            "bottom",
            "half",
            "leftmost",
            "rightmost",
        }:
            raise InvalidInputError(
                "scroll --to must be one of: top, bottom, half, leftmost, rightmost."
            )
        if int(amount) <= 0:
            raise InvalidInputError("scroll --amount must be greater than zero.")
        normalized_ready = (
            str(ready_condition).lower().replace("_", "-")
            if ready_condition
            else None
        )
        if normalized_ready == "element" and not ready_locator:
            raise InvalidInputError("scroll --ready-locator is required for --ready-condition element.")

        with self._with_runtime(session=session, headless=headless) as runtime:
            before = self.adapter.scroll_metrics(runtime.tab)
            listener_started = False
            readiness_started = False
            if normalized_ready == "network-idle":
                runtime.tab.listen.start(True)
                listener_started = True
            try:
                self.adapter.scroll_page(
                    runtime.tab,
                    direction=normalized_direction,
                    amount=int(amount),
                    to=normalized_to,
                )
                ready = None
                if normalized_ready:
                    try:
                        readiness_started = True
                        ready = self.adapter.wait_ready(
                            runtime.tab,
                            condition=normalized_ready,
                            locator=ready_locator,
                            timeout=self._positive_float(ready_timeout),
                            listener_started=listener_started,
                        )
                    except ValueError as exc:
                        raise InvalidInputError(str(exc)) from exc
            finally:
                # ``wait_ready()`` owns normal listener shutdown.  If scrolling
                # fails first, do not leave the network listener attached to a
                # reusable browser session.
                if listener_started and not readiness_started:
                    try:
                        runtime.tab.listen.stop()
                    except Exception:
                        pass
            self._wait(wait_time)
            after = self.adapter.scroll_metrics(runtime.tab)
            runtime.persist()
            return {
                "page": self._page_payload(runtime),
                "page_identity": self._page_identity_payload(runtime),
                "direction": normalized_direction,
                "amount": int(amount),
                "to": normalized_to,
                "before": before,
                "after": after,
                "readiness": (
                    {
                        "condition": normalized_ready,
                        "ready": bool(ready),
                        "locator": ready_locator if normalized_ready == "element" else None,
                    }
                    if normalized_ready
                    else None
                ),
                "moved": (
                    before.get("x") != after.get("x")
                    or before.get("y") != after.get("y")
                ),
            }

    def wait_ready(
        self,
        *,
        session: str = DEFAULT_SESSION,
        condition: str = "document",
        locator: str | None = None,
        timeout: float | None = 10.0,
        headless: bool | None = None,
        wait_time: float = 0.0,
    ) -> dict:
        normalized = str(condition or "document").lower().replace("_", "-")
        if normalized == "element" and not locator:
            raise InvalidInputError("wait-ready --locator is required for condition element.")
        with self._with_runtime(session=session, headless=headless) as runtime:
            try:
                ready = self.adapter.wait_ready(
                    runtime.tab,
                    condition=normalized,
                    locator=locator,
                    timeout=self._positive_float(timeout),
                )
            except ValueError as exc:
                raise InvalidInputError(str(exc)) from exc
            self._wait(wait_time)
            runtime.persist()
            return {
                "page": self._page_payload(runtime),
                "page_identity": self._page_identity_payload(runtime),
                "condition": normalized,
                "locator": locator if normalized == "element" else None,
                "timeout": self._positive_float(timeout),
                "ready": bool(ready),
            }

    def expand_container(
        self,
        session: str = DEFAULT_SESSION,
        ref: str | None = None,
        depth: int = 2,
        headless: bool | None = None,
        wait_time: float = 0.0,
    ) -> dict:
        with self._with_runtime(session=session, headless=headless) as runtime:
            self._wait(wait_time)
            runtime.begin_snapshot()
            records = self.adapter.snapshot_nodes(runtime.tab, root_xpath=self._ref_item(runtime, ref)["xpath"], depth=depth)
            nodes = runtime.upsert_nodes(records)
            runtime.persist()
            return {
                "page": self._page_payload(runtime),
                "page_identity": self._page_identity_payload(runtime),
                "target_ref": ref,
                "mode": "full",
                "count": len(nodes),
                "nodes": nodes,
            }

    def list_items(
        self,
        session: str = DEFAULT_SESSION,
        group_ref: str | None = None,
        sample_size: int = 3,
        headless: bool | None = None,
        wait_time: float = 0.0,
    ) -> dict:
        with self._with_runtime(session=session, headless=headless) as runtime:
            self._wait(wait_time)
            runtime.begin_snapshot()
            group_item = self._ref_item(runtime, group_ref)
            records = self.adapter.snapshot_nodes(runtime.tab, root_xpath=group_item["xpath"], depth=2)
            nodes = runtime.upsert_nodes(records)
            compressed_groups = self._compress_nodes(nodes)
            if compressed_groups:
                cg = compressed_groups[0]
                group = {
                    "representative_ref": cg.representative_ref,
                    "count": cg.count,
                    "member_refs": cg.member_refs,
                    "role": cg.role,
                    "tag": cg.tag,
                }
            else:
                group = {"representative_ref": group_ref, "count": len(nodes), "member_refs": [n["ref"] for n in nodes[:sample_size]]}
            from dp_cli.grouper import FieldSchemaExtractor, GroupKindDetector
            kind = GroupKindDetector().detect(group, nodes)
            fields = FieldSchemaExtractor().extract(group.get("member_refs", []), nodes)
            runtime.persist()
            return {
                "page": self._page_payload(runtime),
                "group_ref": group_ref,
                "group_kind": kind,
                "item_count": group.get("count", 0),
                "sample_items": [{"item_ref": ref, "fields": {}} for ref in group.get("member_refs", [])[:sample_size]],
                "schema_hints": fields,
            }

    def extract_group(
        self,
        session: str = DEFAULT_SESSION,
        target_ref: str | None = None,
        schema: list[str] | None = None,
        limit: int | None = None,
        headless: bool | None = None,
        wait_time: float = 0.0,
    ) -> dict:
        with self._with_runtime(session=session, headless=headless) as runtime:
            self._wait(wait_time)
            runtime.begin_snapshot()
            target_item = self._ref_item(runtime, target_ref)
            # Broad portal regions can place repeated cards 8-16 levels below
            # their semantic wrapper while shallow sidebars sit closer to the
            # root.  A depth of 6 therefore exposed only filters on pages such
            # as movie charts.  Capture enough descendants to reach the cards.
            records = self.adapter.snapshot_nodes(runtime.tab, root_xpath=target_item["xpath"], depth=16)
            nodes = runtime.upsert_nodes(records)
            from dp_cli.projector import ExtractProjector
            projector = ExtractProjector()
            compressed_groups = self._compress_nodes(nodes)
            projection_item_refs = self._projection_item_refs(nodes)
            if compressed_groups:
                cg = compressed_groups[0]
                group = {
                    "group_ref": target_ref,
                    "representative_ref": cg.representative_ref,
                    "item_refs": projection_item_refs,
                    "count": cg.count,
                    "root_xpath": target_item["xpath"],
                }
            else:
                group = {
                    "group_ref": target_ref,
                    "representative_ref": target_ref,
                    "item_refs": projection_item_refs,
                    "root_xpath": target_item["xpath"],
                }
            result = projector.project(group, nodes, schema)
            if limit is not None and limit > 0:
                result["items"] = result["items"][:limit]
                result["item_count"] = len(result["items"])
            result["page"] = self._page_payload(runtime)
            result["page_identity"] = self._page_identity_payload(runtime)
            runtime.persist()
            return result

    def resolve_locator(
        self,
        session: str = DEFAULT_SESSION,
        ref: str | None = None,
        headless: bool | None = None,
        wait_time: float = 0.0,
    ) -> dict:
        with self._with_runtime(session=session, headless=headless) as runtime:
            self._wait(wait_time)
            item = self._ref_item(runtime, ref)
            return {
                "ref": ref,
                "fingerprint": item.get("fingerprint", ""),
                "semantic_fingerprint": item.get("semantic_fingerprint", ""),
                "fingerprint_version": item.get("fingerprint_version", "1"),
                "confidence": (
                    0.98
                    if item.get("semantic_fingerprint")
                    else (0.9 if item.get("fingerprint") else 0.0)
                ),
                "ref_rebound": bool(item.get("ref_rebound")),
                "locator_candidates": item.get("locator_candidates", []),
                "re_resolve_result": "matched",
                "page": self._page_payload(runtime),
                "page_identity": self._page_identity_payload(runtime),
            }

    def eval_js(
        self,
        session: str = DEFAULT_SESSION,
        js: str = "",
        headless: bool | None = None,
        wait_time: float = 0.0,
    ) -> dict:
        with self._with_runtime(session=session, headless=headless) as runtime:
            self._wait(wait_time)
            result = runtime.tab.run_js(js, as_expr=True)
            return {
                "result": result,
                "page": self._page_payload(runtime),
                "page_identity": self._page_identity_payload(runtime),
            }

    def batch_extract_detail_pages(
        self,
        items: list[dict],
        session: str = DEFAULT_SESSION,
        source_url: str | None = None,
        target_pages: int | None = None,
        list_pages_extracted: int | None = None,
        limit: int | None = None,
        schema: list[str] | None = None,
        extractor: str = "ai",
        navigation_mode: str = "click",
        fallback_mode: str = "direct",
        wait_time: float = 0.0,
        wait_jitter: float = 0.0,
        max_retries: int = 1,
        item_timeout: float | None = None,
        ai_timeout: float | None = None,
        output_file: str | None = None,
        progress_file: str | None = None,
        headless: bool | None = None,
    ) -> dict:
        if not isinstance(items, list):
            raise InvalidInputError("batch-detail-extract requires a list of item objects.")
        if extractor not in {"ai", "legacy-js", "auto"}:
            raise InvalidInputError("batch-detail-extract --extractor must be one of: ai, legacy-js, auto.")
        if navigation_mode not in {"click", "direct"}:
            raise InvalidInputError("batch-detail-extract --navigation-mode must be one of: click, direct.")
        if fallback_mode not in {"direct", "skip"}:
            raise InvalidInputError("batch-detail-extract --fallback-mode must be one of: direct, skip.")

        list_items = [item for item in items if isinstance(item, dict)]
        if limit is not None and limit > 0:
            list_items = list_items[:limit]

        source = source_url or ""
        detail_items = []
        detail_pages_extracted = 0
        detail_template = None
        current_page: dict = {}
        max_attempts = max(1, int(max_retries or 1))
        item_timeout_value = self._positive_float(item_timeout)
        ai_timeout_value = self._positive_float(ai_timeout)
        output_path = self._prepare_batch_path(output_file)
        progress_path = self._prepare_batch_path(progress_file)
        resumed_items = self._load_batch_progress(progress_path)
        resumed_count = 0
        processed_count = 0

        def current_payload(partial: bool) -> dict:
            return self._batch_detail_payload(
                source_url=source_url,
                target_pages=target_pages,
                list_pages_extracted=list_pages_extracted,
                detail_pages_extracted=detail_pages_extracted,
                detail_template=detail_template,
                schema=schema,
                extractor=extractor,
                navigation_mode=navigation_mode,
                fallback_mode=fallback_mode,
                wait_time=wait_time,
                wait_jitter=wait_jitter,
                max_attempts=max_attempts,
                item_timeout=item_timeout_value,
                ai_timeout=ai_timeout_value,
                output_file=str(output_path) if output_path else None,
                progress_file=str(progress_path) if progress_path else None,
                detail_items=detail_items,
                resumed_count=resumed_count,
                processed_count=processed_count,
                partial=partial,
            )

        self._write_batch_output(output_path, current_payload(partial=True))

        with self._with_runtime(session=session, headless=headless) as runtime:
            for index, list_item in enumerate(list_items, start=1):
                item_started_at = time.monotonic()
                requested_url = self._raw_detail_item_url(list_item)
                detail_url = self._detail_item_url(list_item, source)
                resume_key = self._batch_progress_key(detail_url or requested_url)
                resumed_item = resumed_items.get(resume_key)
                if resumed_item and resumed_item.get("detail_ok") is True:
                    detail_items.append(dict(resumed_item))
                    detail_pages_extracted += 1
                    resumed_count += 1
                    continue
                processed_count += 1
                merged = {
                    "title": list_item.get("title") or list_item.get("name"),
                    "url": detail_url or requested_url,
                    "requested_url": requested_url,
                    "final_url": None,
                    "list_info": dict(list_item),
                    "detail_info": {},
                    "detail_ok": False,
                    "detail_error": None,
                    "extractor": extractor,
                    "navigation_mode": navigation_mode,
                }
                if not detail_url:
                    merged["detail_error"] = (
                        "Missing detail URL."
                        if not requested_url
                        else "Detail URL must use HTTP or HTTPS."
                    )
                    detail_items.append(merged)
                    self._persist_batch_item(
                        output_path=output_path,
                        progress_path=progress_path,
                        payload=current_payload(partial=True),
                        index=index,
                        total=len(list_items),
                        item=merged,
                    )
                    continue

                errors = []
                for attempt in range(max_attempts):
                    if self._item_timed_out(item_started_at, item_timeout_value):
                        errors.append(f"Item timeout after {item_timeout_value:.1f}s.")
                        break
                    try:
                        if navigation_mode == "click":
                            self._open_detail_by_click_or_fallback(
                                runtime=runtime,
                                list_item=list_item,
                                detail_url=detail_url,
                                source_url=source,
                                fallback_mode=fallback_mode,
                                wait_time=wait_time,
                                item_started_at=item_started_at,
                                item_timeout=item_timeout_value,
                            )
                        else:
                            self._open_url(
                                runtime.tab,
                                detail_url,
                                timeout=self._remaining_item_timeout(item_started_at, item_timeout_value),
                            )
                            runtime.sync_page_identity()
                            self._wait(wait_time)
                        final_url = self._runtime_page_url(runtime)
                        merged["final_url"] = final_url
                        if not self._detail_navigation_matches(detail_url, final_url, source):
                            raise RuntimeError(
                                "Detail navigation did not reach the requested page: "
                                f"requested={detail_url!r}, final={final_url!r}."
                            )
                        if self._item_timed_out(item_started_at, item_timeout_value):
                            raise TimeoutError(f"Item timeout after {item_timeout_value:.1f}s before extraction.")
                        extracted = self._extract_detail(
                            runtime.tab,
                            extractor=extractor,
                            schema=schema,
                            ai_timeout=ai_timeout_value,
                        )
                        detail_info = extracted.get("detail_info") if isinstance(extracted, dict) else {}
                        if not isinstance(detail_info, dict):
                            detail_info = {}
                        extracted_source = (
                            str(extracted.get("source_url") or "")
                            if isinstance(extracted, dict)
                            else ""
                        )
                        if extracted_source and not self._same_document_url(extracted_source, final_url):
                            errors.append(
                                "Extractor source URL does not match final page: "
                                f"source={extracted_source!r}, final={final_url!r}."
                            )
                            detail_info = {}
                        if self._has_meaningful_detail(detail_info, schema):
                            merged["detail_info"] = detail_info
                            merged["detail_ok"] = True
                            merged["detail_error"] = None
                            merged["fields"] = extracted.get("fields", list(detail_info.keys()))
                            merged["confidence"] = extracted.get("confidence")
                            merged["warnings"] = extracted.get("warnings", [])
                            detail_pages_extracted += 1
                            if detail_template is None:
                                detail_template = extracted.get("template") if isinstance(extracted, dict) else None
                            break
                        errors.append("No detail fields extracted.")
                    except Exception as exc:  # keep batch crawling resilient per item
                        errors.append(str(exc))
                    if self._item_timed_out(item_started_at, item_timeout_value):
                        errors.append(f"Item timeout after {item_timeout_value:.1f}s.")
                        break
                    if attempt < max_attempts - 1:
                        self._wait(self._retry_wait(wait_time, wait_jitter, attempt))
                if not merged["detail_ok"]:
                    merged["detail_error"] = "; ".join(error for error in errors if error) or "Detail extraction failed."
                detail_items.append(merged)
                self._persist_batch_item(
                    output_path=output_path,
                    progress_path=progress_path,
                    payload=current_payload(partial=True),
                    index=index,
                    total=len(list_items),
                    item=merged,
                )
                runtime.persist()
                self._wait(self._item_wait(wait_time, wait_jitter))

            runtime.persist()
            current_page = self._page_payload(runtime)

        final_payload = current_payload(partial=False)
        final_payload["page"] = current_page
        self._write_batch_output(output_path, final_payload)
        return final_payload

    def _batch_detail_payload(
        self,
        *,
        source_url: str | None,
        target_pages: int | None,
        list_pages_extracted: int | None,
        detail_pages_extracted: int,
        detail_template: dict | None,
        schema: list[str] | None,
        extractor: str,
        navigation_mode: str,
        fallback_mode: str,
        wait_time: float,
        wait_jitter: float,
        max_attempts: int,
        item_timeout: float | None,
        ai_timeout: float | None,
        output_file: str | None,
        progress_file: str | None,
        detail_items: list[dict],
        resumed_count: int,
        processed_count: int,
        partial: bool,
    ) -> dict:
        return {
            "task_type": "detail_crawler",
            "source_url": source_url,
            "target_pages": target_pages,
            "list_pages_extracted": list_pages_extracted,
            "detail_pages_extracted": detail_pages_extracted,
            "detail_schema_learned": detail_template is not None,
            "detail_template": detail_template,
            "schema": schema,
            "extractor": extractor,
            "navigation_mode": navigation_mode,
            "fallback_mode": fallback_mode,
            "wait_time": wait_time,
            "wait_jitter": wait_jitter,
            "max_retries": max_attempts,
            "item_timeout": item_timeout,
            "ai_timeout": ai_timeout,
            "output_file": output_file,
            "progress_file": progress_file,
            "partial": partial,
            "item_count": len(detail_items),
            "resumed_count": resumed_count,
            "processed_count": processed_count,
            "items": list(detail_items),
        }

    def inspect_session(
        self,
        session: str = DEFAULT_SESSION,
        headless: bool | None = None,
        wait_time: float = 0.0,
    ) -> dict:
        with self._with_runtime(session=session, headless=headless) as runtime:
            self._wait(wait_time)
            return {
                "session_name": runtime.meta.session,
                "session_id": runtime.meta.session_id,
                "runtime": {
                    "runtime_id": runtime.meta.runtime_id,
                    "status": runtime.meta.runtime_status,
                    "browser_pid": runtime.meta.browser_pid,
                    "port": runtime.meta.port,
                    "headless": runtime.meta.headless,
                    "last_seen_at": runtime.meta.last_seen_at,
                },
                "page": {
                    "tab_id": runtime.state.active_page.tab_id,
                    "url": runtime.state.active_page.url,
                    "title": runtime.state.active_page.title,
                    "page_id": runtime.state.active_page.page_id,
                    "snapshot_id": runtime.state.active_page.snapshot_id,
                    "snapshot_seq": runtime.state.active_page.snapshot_seq,
                },
                "ref_count": runtime.total_ref_count(),
                "container_ref_count": len(runtime.state.container_refs),
                "element_ref_count": len(runtime.state.element_refs),
                "last_snapshot_file": runtime.state.last_snapshot_file,
                "last_snapshot_mode": runtime.state.last_snapshot_mode,
                "last_snapshot_diff": runtime.state.last_snapshot_diff,
            }

    def close_session(self, session: str = DEFAULT_SESSION) -> dict:
        return self.sessions.close_session(session=session)

    @contextmanager
    def _with_runtime(self, session: str, headless: bool | None):
        with self.sessions.open_runtime(session=session, headless=headless) as runtime:
            runtime.refresh_active_tab()
            yield runtime

    def _page_payload(self, runtime) -> dict:
        return self.adapter.page_info(runtime.tab)

    def _page_identity_payload(self, runtime) -> dict:
        return {
            "runtime_id": runtime.meta.runtime_id,
            "page_id": runtime.state.active_page.page_id,
            "snapshot_id": runtime.state.active_page.snapshot_id,
            "snapshot_seq": runtime.state.active_page.snapshot_seq,
        }

    def _target_payload(self, ref: str | None, locator: str) -> dict:
        return {"ref": ref, "locator": locator}

    def _ref_item(self, runtime, ref: str) -> dict:
        try:
            item = runtime.ref_item(ref)
        except KeyError as exc:
            raise RefNotFoundError(ref) from exc
        if item.get("runtime_id") != runtime.meta.runtime_id:
            raise RefStaleError(
                ref,
                {
                    "expected_runtime_id": runtime.meta.runtime_id,
                    "actual_runtime_id": item.get("runtime_id"),
                },
            )
        current_page_id = runtime.state.active_page.page_id
        if item.get("page_id") != current_page_id:
            raise RefStaleError(
                ref,
                {
                    "expected_page_id": current_page_id,
                    "actual_page_id": item.get("page_id"),
                },
            )
        return item

    def _resolve_target(self, runtime, ref: str | None, locator: str | None) -> str:
        if ref:
            item = self._ref_item(runtime, ref)
            if item.get("ref_type") != "element":
                raise InvalidRefTypeError(ref, expected="element", actual=item.get("ref_type", "unknown"))
            return item["locator"]
        if locator:
            return locator
        raise InvalidInputError("Command requires either --ref or --locator.")

    def _ensure_element_interactable(self, element, locator: str) -> dict:
        state = self.adapter.element_state(element)
        if state.get("interactable_now"):
            return state
        self.adapter.scroll_into_view(element)
        state = self.adapter.element_state(element)
        if state.get("interactable_now"):
            return state
        raise ElementNotInteractableError(
            "Element exists but is not interactable right now.",
            {
                "locator": locator,
                "visible": state.get("visible"),
                "in_viewport": state.get("in_viewport"),
                "enabled": state.get("enabled"),
                "interactable_now": state.get("interactable_now"),
            },
        )

    def _perform_element_action(
        self,
        session: str,
        headless: bool | None,
        ref: str | None,
        locator: str | None,
        element_error_message: str,
        action: Callable,
        include_payload: Callable,
        text: str | None = None,
        wait_time: float = 0.0,
    ) -> dict:
        with self._with_runtime(session=session, headless=headless) as runtime:
            target_locator = self._resolve_target(runtime, ref, locator)
            element = self.adapter.resolve(runtime.tab, target_locator)
            if not element:
                raise ElementNotFoundError(element_error_message, {"locator": target_locator})
            state = self._ensure_element_interactable(element, target_locator)
            before_tab_ids = set(getattr(runtime.browser, "tab_ids", []) or [])
            action(element, text)
            tab_transition = runtime.refresh_after_possible_tab_change(before_tab_ids)
            self._wait(wait_time)
            if tab_transition.get("opened_new_tab") or tab_transition.get("active_tab_changed"):
                state = {**state, "state_after_action_unavailable": True}
            else:
                try:
                    state = self.adapter.element_state(element)
                except Exception:
                    state = {**state, "state_after_action_unavailable": True}
            runtime.persist()
            payload = include_payload(runtime, target_locator, state)
            payload["page_identity"] = self._page_identity_payload(runtime)
            payload["tab_transition"] = tab_transition
            return payload

    def _extract_detail(
        self,
        tab,
        extractor: str,
        schema: list[str] | None = None,
        ai_timeout: float | None = None,
    ) -> dict:
        if extractor == "legacy-js":
            return self.adapter.extract_detail(tab)
        if extractor == "ai":
            ai = AiDetailExtractor(timeout=ai_timeout) if ai_timeout else AiDetailExtractor()
            return ai.extract(self.adapter.detail_page_package(tab), schema=schema)
        try:
            ai = AiDetailExtractor(timeout=ai_timeout) if ai_timeout else AiDetailExtractor()
            return ai.extract(self.adapter.detail_page_package(tab), schema=schema)
        except Exception as exc:
            legacy = self.adapter.extract_detail(tab)
            warnings = legacy.get("warnings") if isinstance(legacy, dict) else None
            if not isinstance(warnings, list):
                warnings = []
            warnings.append(f"AI extractor failed; legacy-js fallback used: {exc}")
            if isinstance(legacy, dict):
                legacy["warnings"] = warnings
            return legacy

    def _open_detail_by_click_or_fallback(
        self,
        runtime,
        list_item: dict,
        detail_url: str,
        source_url: str,
        fallback_mode: str,
        wait_time: float,
        item_started_at: float,
        item_timeout: float | None,
    ) -> None:
        try:
            self._open_detail_by_click(
                runtime,
                list_item,
                detail_url,
                source_url,
                wait_time,
                item_started_at,
                item_timeout,
            )
            return
        except Exception:
            if fallback_mode == "skip":
                raise
        self._open_url(
            runtime.tab,
            detail_url,
            timeout=self._remaining_item_timeout(item_started_at, item_timeout),
        )
        runtime.sync_page_identity()
        self._wait(wait_time)

    def _open_detail_by_click(
        self,
        runtime,
        list_item: dict,
        detail_url: str,
        source_url: str,
        wait_time: float,
        item_started_at: float,
        item_timeout: float | None,
    ) -> None:
        source_page_url = list_item.get("source_page_url") or list_item.get("page_url") or source_url
        if source_page_url and runtime.state.active_page.url != source_page_url:
            self._open_url(
                runtime.tab,
                source_page_url,
                timeout=self._remaining_item_timeout(item_started_at, item_timeout),
            )
            runtime.sync_page_identity()
            self._wait(wait_time)

        locator = None
        item_ref = list_item.get("item_ref") or list_item.get("ref")
        if item_ref:
            try:
                locator = self._ref_item(runtime, str(item_ref))["locator"]
            except Exception:
                locator = None
        if not locator:
            locator = self._find_detail_link_locator(runtime, list_item, detail_url)
        if not locator:
            raise ElementNotFoundError("Could not find detail link to click.", {"url": detail_url})

        element = self.adapter.resolve(runtime.tab, locator)
        if not element:
            raise ElementNotFoundError("Could not resolve detail link to click.", {"locator": locator})
        self._ensure_element_interactable(element, locator)
        before_tab_ids = set(getattr(runtime.browser, "tab_ids", []) or [])
        self.adapter.click(element)
        runtime.refresh_after_possible_tab_change(before_tab_ids)
        runtime.sync_page_identity()
        self._wait(wait_time)

    def _find_detail_link_locator(self, runtime, list_item: dict, detail_url: str) -> str | None:
        runtime.begin_snapshot()
        nodes = runtime.upsert_nodes(self.adapter.snapshot_nodes(runtime.tab, depth=None))
        detail_abs = self._absolute_url(detail_url, runtime.state.active_page.url or "")
        text = str(list_item.get("text") or list_item.get("title") or list_item.get("name") or "").strip()
        best: tuple[int, str] | None = None
        for node in nodes:
            if node.get("ref_type") != "element" or node.get("role") != "link":
                continue
            href = node.get("href") or ""
            node_abs = self._absolute_url(href, node.get("url") or runtime.state.active_page.url or "")
            node_text = str(node.get("text") or node.get("name") or "").strip()
            score = 0
            if detail_abs and node_abs == detail_abs:
                score += 100
            if text and (text == node_text or text in node_text or node_text in text):
                score += 40
            if score <= 0:
                continue
            locator = node.get("locator")
            if locator and (best is None or score > best[0]):
                best = (score, locator)
        runtime.persist()
        return best[1] if best else None

    def _absolute_url(self, url: str, base_url: str = "") -> str:
        if not isinstance(url, str):
            return ""
        return urljoin(base_url or "", url.strip())

    def _item_wait(self, wait_time: float, wait_jitter: float) -> float:
        base = max(0.0, float(wait_time or 0.0))
        jitter = max(0.0, float(wait_jitter or 0.0))
        return base + (random.uniform(0, jitter) if jitter else 0.0)

    def _retry_wait(self, wait_time: float, wait_jitter: float, attempt: int) -> float:
        return self._item_wait(wait_time, wait_jitter) * max(1, attempt + 1)

    def _positive_float(self, value: float | int | None) -> float | None:
        if value is None:
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    def _item_timed_out(self, started_at: float, item_timeout: float | None) -> bool:
        return bool(item_timeout and time.monotonic() - started_at >= item_timeout)

    def _remaining_item_timeout(self, started_at: float, item_timeout: float | None) -> float | None:
        if not item_timeout:
            return None
        remaining = item_timeout - (time.monotonic() - started_at)
        return max(0.1, remaining)

    def _open_url(self, tab, url: str, timeout: float | None = None) -> dict:
        try:
            return self.adapter.open_url(tab, url, timeout=timeout)
        except TypeError:
            return self.adapter.open_url(tab, url)

    def _prepare_batch_path(self, path: str | None) -> Path | None:
        if not path:
            return None
        prepared = Path(path)
        prepared.parent.mkdir(parents=True, exist_ok=True)
        return prepared

    def _write_batch_output(self, path: Path | None, payload: dict) -> None:
        if not path:
            return
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _append_batch_progress(self, path: Path | None, entry: dict) -> None:
        if not path:
            return
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _load_batch_progress(self, path: Path | None) -> dict[str, dict]:
        if not path or not path.exists():
            return {}
        completed: dict[str, dict] = {}
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return {}
        for line in lines:
            try:
                entry = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(entry, dict) or entry.get("detail_ok") is not True:
                continue
            item = entry.get("item")
            if not isinstance(item, dict):
                item = {
                    "title": entry.get("title"),
                    "url": entry.get("url"),
                    "requested_url": entry.get("url"),
                    "final_url": entry.get("url"),
                    "list_info": {},
                    "detail_info": (
                        entry.get("detail_info")
                        if isinstance(entry.get("detail_info"), dict)
                        else {}
                    ),
                    "detail_ok": True,
                    "detail_error": None,
                }
            key = self._batch_progress_key(
                item.get("final_url")
                or item.get("url")
                or item.get("requested_url")
                or entry.get("url")
            )
            if key:
                completed[key] = item
        return completed

    @staticmethod
    def _batch_progress_key(url: object) -> str:
        value = str(url or "").strip()
        if not value:
            return ""
        try:
            parsed = urlparse(value)
        except Exception:
            return value.rstrip("/")
        return urlunparse(
            (
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                parsed.path.rstrip("/") or "/",
                "",
                parsed.query,
                "",
            )
        )

    def _persist_batch_item(
        self,
        *,
        output_path: Path | None,
        progress_path: Path | None,
        payload: dict,
        index: int,
        total: int,
        item: dict,
    ) -> None:
        entry = {
            "index": index,
            "total": total,
            "url": item.get("url"),
            "title": item.get("title"),
            "detail_ok": bool(item.get("detail_ok")),
            "detail_info": item.get("detail_info") if isinstance(item.get("detail_info"), dict) else {},
            "detail_error": item.get("detail_error"),
            "item": item,
        }
        self._append_batch_progress(progress_path, entry)
        self._write_batch_output(output_path, payload)
        status = "ok" if entry["detail_ok"] else "failed"
        print(f"[DetailBatch] {index}/{total} {status} {entry['url']}", file=sys.stderr, flush=True)

    def _wait(self, seconds: float | int | None) -> None:
        try:
            value = float(seconds or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            time.sleep(value)

    def _write_snapshot_artifact(self, session: str, artifact: SnapshotArtifact, snapshot_id: str) -> str:
        snapshots_dir = self.sessions.store.base_dir / "snapshots" / session
        snapshots_dir.mkdir(parents=True, exist_ok=True)
        path = snapshots_dir / f"{snapshot_id}.json"
        path.write_text(json.dumps(artifact.to_output(), ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    def _compress_nodes(self, nodes: list[dict]) -> list[CompressedGroup]:
        compressor = DOMCompressor(CompressionConfig(min_group_size=3))
        return compressor.compress(nodes)

    def _filter_text_matches(self, nodes: list[dict], query: str) -> list[dict]:
        from dp_cli.models import score_text_match
        lookup = {node["ref"]: node for node in nodes}
        children = self._children_map(nodes)
        normalized_query = self._normalized(query)
        matches: list[tuple[int, dict]] = []
        for node in nodes:
            if node["ref_type"] != "element":
                continue
            haystack = self._searchable_text(node)
            if normalized_query not in haystack:
                continue
            # Work on a copy to avoid mutating persisted state
            node_copy = dict(node)
            node_copy["_pinned"] = self._is_pinned_control(node, lookup, children)
            score = score_text_match(
                node_copy,
                normalized_query,
                pinned_bias=20,
                viewport_bias=5,
                interactable_bias=5,
                native_tag_bias=10,
            )
            # Mark exact text matches so callers can distinguish precise targets from container parents
            node_copy["exact_match"] = (
                self._normalized(node.get("text", "")) == normalized_query
                or self._normalized(node.get("name", "")) == normalized_query
            )
            matches.append((score, node_copy))
        matches.sort(key=lambda item: item[0], reverse=True)
        return [node for _, node in matches]

    def _children_map(self, nodes: list[dict]) -> dict[str, list[dict]]:
        mapping: dict[str, list[dict]] = {}
        for node in nodes:
            parent_ref = node.get("parent_ref")
            if not parent_ref:
                continue
            mapping.setdefault(parent_ref, []).append(node)
        return mapping

    def _descendant_elements(self, ref: str, children: dict[str, list[dict]]) -> list[dict]:
        queue = list(children.get(ref, []))
        descendants: list[dict] = []
        while queue:
            node = queue.pop(0)
            if node["ref_type"] == "element":
                descendants.append(node)
            queue.extend(children.get(node["ref"], []))
        return descendants

    def _is_pinned_control(self, node: dict, lookup: dict[str, dict], children: dict[str, list[dict]]) -> bool:
        if node["ref_type"] != "element":
            return False
        if self._is_pagination_control(node, lookup, children):
            return True
        if self._is_form_primary_action(node, lookup):
            return True
        if self._is_navigation_control(node, lookup):
            return True
        if any(node["states"].get(flag) for flag in ("selected", "expanded")):
            return True
        return False

    def _is_pagination_control(self, node: dict, lookup: dict[str, dict], children: dict[str, list[dict]]) -> bool:
        text = self._normalized(" ".join(part for part in (node.get("name"), node.get("text")) if part))
        if not text:
            return False
        if any(keyword in text for keyword in PAGINATION_KEYWORDS):
            return True
        if re.fullmatch(r"\d+", text):
            parent = lookup.get(node.get("parent_ref") or "")
            if not parent:
                return False
            siblings = [item for item in children.get(parent["ref"], []) if item["ref_type"] == "element"]
            sibling_texts = [
                self._normalized(" ".join(part for part in (item.get("name"), item.get("text")) if part))
                for item in siblings
            ]
            if sum(1 for value in sibling_texts if re.fullmatch(r"\d+", value or "")) >= 2:
                return True
            if any(any(keyword in value for keyword in PAGINATION_KEYWORDS) for value in sibling_texts):
                return True
        return False

    def _is_form_primary_action(self, node: dict, lookup: dict[str, dict]) -> bool:
        if node.get("role") not in {"button", "link"}:
            return False
        if not self._has_ancestor_role(node, lookup, {"form", "search", "dialog"}):
            return False
        text = self._normalized(" ".join(part for part in (node.get("name"), node.get("text")) if part))
        return any(keyword in text for keyword in PRIMARY_ACTION_KEYWORDS)

    def _is_navigation_control(self, node: dict, lookup: dict[str, dict]) -> bool:
        if node.get("role") not in {"button", "link", "tab"}:
            return False
        if self._has_ancestor_role(node, lookup, {"navigation"}):
            return True
        text = (node.get("name") or node.get("text") or "").strip()
        bounds = node.get("bounds") or {}
        if not text:
            return False
        if node.get("depth", 0) <= 8 and len(text) <= 32:
            if bounds.get("y", 9999) <= 180:
                return True
            if bounds.get("x", 9999) <= 180 and bounds.get("width", 9999) <= 260:
                return True
        return False

    def _has_ancestor_role(self, node: dict, lookup: dict[str, dict], roles: set[str]) -> bool:
        current_ref = node.get("parent_ref")
        while current_ref:
            parent = lookup.get(current_ref)
            if not parent:
                return False
            if parent.get("role") in roles:
                return True
            current_ref = parent.get("parent_ref")
        return False

    def _searchable_text(self, node: dict) -> str:
        return self._normalized(
            " ".join(
                part
                for part in (
                    node.get("name") or "",
                    node.get("text") or "",
                    node.get("label") or "",
                    node.get("value") or "",
                    node.get("placeholder") or "",
                    node.get("href") or "",
                    node.get("id") or "",
                    node.get("title") or "",
                    node.get("aria_label") or "",
                    node.get("context", {}).get("heading") or "",
                    node.get("context", {}).get("landmark") or "",
                    node.get("context", {}).get("form") or "",
                    node.get("context", {}).get("list") or "",
                )
                if part
            )
        )

    def _normalized(self, value: str) -> str:
        text = re.sub(r"\s+", "", (value or "").strip().lower())
        # Normalize leading zeros in numbers so "第05集" matches "第5集"
        return re.sub(r"(?<!\d)0+(\d)", r"\1", text)

    def _build_index(self, nodes: list[dict]) -> dict:
        lookup = {node["ref"]: node for node in nodes}
        children = self._children_map(nodes)
        data_regions = self._detect_data_regions(nodes)
        data_region_lookup = {region["ref"]: region for region in data_regions}
        for node in nodes:
            region = data_region_lookup.get(node["ref"])
            if region:
                node["_data_region_item_count"] = region["item_count"]

        surface_nodes = []
        deep_nodes = []
        for node in nodes:
            if node.get("semantic_level") == "surface":
                surface_nodes.append(node)
            else:
                deep_nodes.append(node)

        surface_nodes = self._rank_index_nodes(surface_nodes, lookup)
        surface_index = [self._surface_node_summary(node, children) for node in surface_nodes]
        deep_index = [self._deep_node_summary(node) for node in deep_nodes]

        roots = [n["ref"] for n in nodes if not n.get("parent_ref")]
        parent_map = {n["ref"]: n.get("parent_ref") for n in nodes if n.get("parent_ref")}
        children_map = {}
        for parent_ref, child_nodes in children.items():
            children_map[parent_ref] = [c["ref"] for c in child_nodes]

        interactable_nodes = self._rank_index_nodes(
            [
                n
                for n in nodes
                if n["ref_type"] == "element"
                and (
                    n["visibility"]["interactable_now"]
                    or (
                        n.get("role") in {"checkbox", "radio", "switch"}
                        and n["visibility"].get("visible")
                    )
                )
                and not self._is_redundant_action_parent(n, children)
            ],
            lookup,
        )
        interactable = [self._interactable_node_summary(n) for n in interactable_nodes]

        stats = {
            "total_nodes": len(nodes),
            "surface_count": len(surface_index),
            "deep_count": len(deep_index),
            "in_viewport": sum(1 for n in nodes if n["visibility"]["in_viewport"]),
            "offscreen": sum(1 for n in nodes if not n["visibility"]["in_viewport"]),
            "interactable_now": len(interactable),
        }

        index = {
            "interactable_elements": [self._filter_empty_fields(item) for item in interactable],
            "data_regions": [self._filter_empty_fields(item) for item in data_regions],
            "surface_index": [self._filter_empty_fields(item) for item in surface_index],
            "deep_index": [self._filter_empty_fields(item) for item in deep_index],
            "tree": {
                "roots": roots,
                "parent_map": parent_map,
                "children_map": children_map,
            },
            "stats": stats,
        }
        return index

    def _detect_data_regions(self, nodes: list[dict]) -> list[dict]:
        lookup = {node["ref"]: node for node in nodes if node.get("ref")}
        containers = [node for node in nodes if node.get("ref_type") == "container" and node.get("xpath")]

        candidates = []
        for container in containers:
            if self._is_footer_region(container, lookup):
                continue
            xpath = container.get("xpath") or ""
            descendants = [
                node
                for node in nodes
                if node.get("ref") != container.get("ref")
                and str(node.get("xpath") or "").startswith(xpath.rstrip("/") + "/")
            ]
            if len(descendants) < 3:
                continue
            link_nodes = [node for node in descendants if self._is_extractable_item_link(node)]
            href_link_nodes = [
                node
                for node in descendants
                if node.get("ref_type") == "element"
                and node.get("role") == "link"
                and str(node.get("href") or "").strip()
            ]
            non_link_content = [
                node
                for node in descendants
                if node.get("role") != "link"
                and str(
                    node.get("text")
                    or node.get("name")
                    or ""
                ).strip()
            ]
            if (
                len(href_link_nodes) >= 3
                and not link_nodes
                and not non_link_content
            ):
                continue
            row_groups = self._row_groups(descendants, xpath)
            unique_link_count = len(
                {
                    self._absolute_href(node) or str(node.get("href") or "")
                    for node in link_nodes
                    if self._absolute_href(node) or str(node.get("href") or "")
                }
            )
            record_count = len(row_groups)
            item_count = (
                record_count
                if record_count >= 3
                else unique_link_count
            )
            if item_count < 3:
                continue
            kind = self._data_region_kind(container, descendants, link_nodes, row_groups)
            score = self._data_region_score(container, descendants, link_nodes, row_groups, lookup)
            if score <= 0:
                continue
            candidates.append(
                {
                    "ref": container["ref"],
                    "ref_type": container["ref_type"],
                    "tag": container.get("tag"),
                    "role": container.get("role"),
                    "name": container.get("name"),
                    "kind": kind,
                    "item_count": item_count,
                    "sample_items": self._data_region_samples(descendants, link_nodes, row_groups),
                    "score": score,
                    "why": self._data_region_reason(kind, descendants, link_nodes, row_groups),
                    "_depth": container.get("depth", 0),
                    "_text_len": len(container.get("text") or ""),
                }
            )

        if not candidates:
            return []

        # Prefer strong, specific repeated-content regions over broad page wrappers.
        candidates.sort(key=lambda item: (-item["score"], -item["_depth"], item["_text_len"], -item["item_count"]))
        selected = []
        for candidate in candidates:
            if self._is_covered_by_selected(candidate, selected, lookup):
                continue
            candidate = dict(candidate)
            candidate.pop("_depth", None)
            candidate.pop("_text_len", None)
            selected.append(candidate)
            if len(selected) >= 5:
                break
        return selected

    def _is_extractable_item_link(self, node: dict) -> bool:
        return self._is_candidate_detail_link(node)

    def _is_content_link(self, node: dict) -> bool:
        return self._is_candidate_detail_link(node)

    def _is_footer_region(self, container: dict, lookup: dict[str, dict]) -> bool:
        role = str(container.get("role") or "").lower()
        tag = str(container.get("tag") or "").lower()
        if role == "contentinfo" or tag == "footer":
            return True

        xpath = str(container.get("xpath") or "").rstrip("/")
        if not xpath:
            return False
        for node in lookup.values():
            if node.get("ref_type") != "container":
                continue
            ancestor_xpath = str(node.get("xpath") or "").rstrip("/")
            if not ancestor_xpath or (
                xpath != ancestor_xpath
                and not xpath.startswith(ancestor_xpath + "/")
            ):
                continue
            text = self._normalized(
                " ".join(
                    str(value or "")
                    for value in (
                        node.get("name"),
                        node.get("text"),
                    )
                )
            )
            if len(text) > 2500:
                continue
            footer_markers = (
                ("contactus", "联系我们"),
                ("privacypolicy", "隐私政策"),
                ("sitenavigation", "站点导航"),
                ("followus", "关注我们"),
                ("copyright",),
                ("icp", "备案"),
                ("reportemail", "举报邮箱"),
            )
            marker_count = sum(
                any(marker in text for marker in marker_group)
                for marker_group in footer_markers
            )
            if marker_count >= 3:
                return True
        return False

    def _projection_item_refs(self, nodes: list[dict]) -> list[str]:
        all_element_refs = [
            node["ref"]
            for node in nodes
            if node.get("ref_type") == "element" and node.get("ref")
        ]
        detail_link_refs = [
            node["ref"]
            for node in nodes
            if node.get("ref") and self._is_extractable_item_link(node)
        ]
        # A repeated region with at least three detail links is a stronger seed
        # than every descendant element in a broad wrapper.  Keep the full-node
        # fallback for linkless tables, quote lists and form-like structures.
        return detail_link_refs if len(detail_link_refs) >= 3 else all_element_refs

    def _is_candidate_detail_link(self, node: dict) -> bool:
        if node.get("ref_type") != "element" or node.get("role") != "link":
            return False
        href = (node.get("href") or "").strip().lower()
        text = (node.get("text") or node.get("name") or "").strip()
        if not href or len(text) < 2:
            return False
        parsed = urlparse(href)
        if parsed.scheme not in {"", "http", "https"}:
            return False
        if href.startswith(("#", "?")) or href in {"/", "./", "../"}:
            return False
        normalized_text = self._normalized(text)
        if normalized_text in PAGINATION_KEYWORDS or re.fullmatch(r"\d+", normalized_text):
            return False
        path_segments = {
            segment
            for segment in parsed.path.lower().split("/")
            if segment
        }
        if path_segments & {
            "author",
            "authors",
            "writer",
            "user",
            "profile",
            "category",
            "categories",
            "genre",
            "rank",
            "ranking",
            "search",
            "tag",
            "topic",
            "typerank",
            "trailer",
            "trailers",
            "photo",
            "photos",
            "video",
            "videos",
            "help",
            "login",
            "logout",
            "register",
        }:
            return False
        noise_patterns = (
            "vod-show",
            "vod-type",
            "year-",
            "area-",
            "by-",
            "class-",
            "page-",
            "/author/",
            "/authors/",
            "/writer/",
            "/user/",
            "/profile/",
            "/category/",
            "/categories/",
            "/genre/",
            "/rank",
            "/ranking",
            "/search",
            "/tag/",
            "/topic/",
            "/typerank",
            "/trailer",
            "/trailers",
            "/photo",
            "/photos",
            "/video",
            "/videos",
            "/promotion",
            "/help",
            "/login",
            "/logout",
            "/register",
        )
        if any(token in href for token in noise_patterns):
            return False
        return bool(parsed.path and parsed.path not in {"", "/"})

    def _row_groups(self, nodes: list[dict], root_xpath: str = "") -> dict[str, list[dict]]:
        return group_record_nodes(nodes, root_xpath)

    def _data_region_kind(
        self,
        container: dict,
        descendants: list[dict],
        link_nodes: list[dict],
        row_groups: dict[str, list[dict]],
    ) -> str:
        tag = (container.get("tag") or "").lower()
        role = (container.get("role") or "").lower()
        if tag == "table" or role in {"table", "grid"}:
            return "table"
        if role in {"list", "listbox"} or any((node.get("tag") or "").lower() == "li" for node in descendants):
            return "list"
        if len(link_nodes) >= 3 and len(row_groups) >= 3:
            return "card_grid"
        return "repeated_structure"

    def _data_region_score(
        self,
        container: dict,
        descendants: list[dict],
        link_nodes: list[dict],
        row_groups: dict[str, list[dict]],
        lookup: dict[str, dict],
    ) -> int:
        score = min(len(descendants), 30)
        score += min(int(container.get("depth") or 0), 20) * 4
        score += min(len(link_nodes), 50) * 5
        score += min(len(row_groups), 12) * 4
        if len(link_nodes) >= 3:
            score += 80
        if len(row_groups) >= 3:
            score += 60
        if link_nodes:
            titles = [
                str(node.get("text") or node.get("name") or "").strip()
                for node in link_nodes
            ]
            long_title_ratio = sum(len(title) >= 8 for title in titles) / len(titles)
            short_title_ratio = sum(len(title) <= 4 for title in titles) / len(titles)
            url_title_ratio = sum(
                title.lower().startswith(("http://", "https://"))
                for title in titles
            ) / len(titles)
            score += round(long_title_ratio * 100)
            score -= round(short_title_ratio * 50)
            score -= round(url_title_ratio * 100)
        role = (container.get("role") or "").lower()
        tag = (container.get("tag") or "").lower()
        if role in {"main", "region", "list", "table", "grid"} or tag in {"main", "section", "ul", "ol", "table"}:
            score += 40
        if role in {"navigation", "banner", "contentinfo", "form", "search"} or self._has_ancestor_role(container, lookup, {"navigation"}):
            score -= 140
        if tag in {"html", "body", "nav", "header", "footer"}:
            score -= 180
        if descendants and len(link_nodes) / len(descendants) < 0.08:
            score -= 80
        if len(container.get("text") or "") > 8000:
            score -= 60
        return score

    def _data_region_samples(
        self,
        descendants: list[dict],
        link_nodes: list[dict],
        row_groups: dict[str, list[dict]],
    ) -> list[dict]:
        samples = []
        preferred = link_nodes if len(link_nodes) >= 3 else [group[0] for group in row_groups.values() if group]
        for node in preferred[:3]:
            samples.append(
                self._filter_empty_fields(
                    {
                        "ref": node.get("ref"),
                        "text": node.get("text") or node.get("name"),
                        "url": self._absolute_href(node) if node.get("href") else None,
                    }
                )
            )
        if samples:
            return samples
        return [
            self._filter_empty_fields({"ref": node.get("ref"), "text": node.get("text") or node.get("name")})
            for node in descendants[:3]
        ]

    def _data_region_reason(
        self,
        kind: str,
        descendants: list[dict],
        link_nodes: list[dict],
        row_groups: dict[str, list[dict]],
    ) -> str:
        return (
            f"{kind}: {len(descendants)} descendant elements, "
            f"{len(link_nodes)} content links, {len(row_groups)} repeated row groups"
        )

    def _is_covered_by_selected(self, candidate: dict, selected: list[dict], lookup: dict[str, dict]) -> bool:
        current_ref = candidate.get("ref")
        ancestors = set()
        while current_ref:
            parent = lookup.get(current_ref, {}).get("parent_ref")
            if not parent:
                break
            ancestors.add(parent)
            current_ref = parent
        for item in selected:
            if item.get("ref") in ancestors and item.get("item_count", 0) >= candidate.get("item_count", 0):
                return True
            selected_ref = item.get("ref")
            selected_ancestors = set()
            while selected_ref:
                parent = lookup.get(selected_ref, {}).get("parent_ref")
                if not parent:
                    break
                selected_ancestors.add(parent)
                selected_ref = parent
            if (
                candidate.get("ref") in selected_ancestors
                and candidate.get("item_count", 0) <= item.get("item_count", 0) * 1.25
            ):
                return True
        return False

    def _absolute_href(self, node: dict) -> str:
        return self._absolute_url(node.get("href") or "", node.get("url") or "")

    def _detail_item_url(self, item: dict, source_url: str = "") -> str:
        raw_url = self._raw_detail_item_url(item)
        if not raw_url:
            return ""
        absolute = self._absolute_url(raw_url, source_url)
        return absolute if self._is_http_url(absolute) else ""

    @staticmethod
    def _raw_detail_item_url(item: dict) -> str:
        raw_url = item.get("detail_url") or item.get("url") or item.get("href") or ""
        return raw_url.strip() if isinstance(raw_url, str) else ""

    @staticmethod
    def _is_http_url(url: str) -> bool:
        parsed = urlparse(str(url or "").strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    @staticmethod
    def _canonical_document_url(url: str) -> str:
        parsed = urlparse(str(url or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        path = parsed.path or "/"
        if path != "/":
            path = path.rstrip("/")
        return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", parsed.query, ""))

    def _same_document_url(self, left: str, right: str) -> bool:
        return bool(left and right) and self._canonical_document_url(left) == self._canonical_document_url(right)

    def _detail_navigation_matches(self, requested_url: str, final_url: str, source_url: str = "") -> bool:
        if not self._is_http_url(final_url):
            return False
        if self._same_document_url(requested_url, final_url):
            return True
        if (
            source_url
            and self._same_document_url(source_url, final_url)
            and not self._same_document_url(source_url, requested_url)
        ):
            return False
        return True

    @staticmethod
    def _runtime_page_url(runtime) -> str:
        state_url = getattr(getattr(runtime.state, "active_page", None), "url", None)
        return str(state_url or getattr(runtime.tab, "url", "") or "")

    @staticmethod
    def _has_meaningful_detail(detail_info: dict, schema: list[str] | None = None) -> bool:
        def meaningful(value) -> bool:
            if value is None or value is False:
                return False
            if isinstance(value, str):
                return bool(value.strip())
            if isinstance(value, (list, tuple, set, dict)):
                return bool(value)
            return True

        meaningful_fields = {
            str(key).strip().lower()
            for key, value in detail_info.items()
            if meaningful(value)
            and str(key).strip().lower() not in {"source_url", "page_url"}
        }
        if not meaningful_fields:
            return False
        requested_fields = {
            str(field).strip().lower()
            for field in (schema or [])
            if str(field).strip()
        }
        if not requested_fields:
            return True
        return len(meaningful_fields & requested_fields) / len(requested_fields) >= 0.5

    def _is_redundant_action_parent(self, node: dict, children: dict[str, list[dict]]) -> bool:
        if node.get("role") != "button" or node.get("tag") not in {"div", "span"}:
            return False
        text = self._normalized(" ".join(part for part in (node.get("name"), node.get("text")) if part))
        if not text:
            return False
        for descendant in self._descendant_elements(node["ref"], children):
            if descendant is node:
                continue
            if descendant.get("role") != "button":
                continue
            descendant_text = self._normalized(
                " ".join(part for part in (descendant.get("name"), descendant.get("text")) if part)
            )
            if descendant_text == text:
                return True
        return False

    def _rank_index_nodes(self, nodes: list[dict], lookup: dict[str, dict]) -> list[dict]:
        return [
            node
            for _, node in sorted(
                enumerate(nodes),
                key=lambda item: (-self._planner_node_priority(item[1], lookup), item[0]),
            )
        ]

    def _planner_node_priority(self, node: dict, lookup: dict[str, dict]) -> int:
        score = 0
        role = node.get("role") or ""
        tag = node.get("tag") or ""
        visibility = node.get("visibility") or {}
        if visibility.get("in_viewport"):
            score += 20
        if visibility.get("interactable_now"):
            score += 50
        if role == "dialog" or self._has_ancestor_role(node, lookup, {"dialog"}):
            score += 1000
        if self._has_ancestor_role(node, lookup, {"form", "search"}):
            score += 120
        if role in {"tab", "button", "link", "textbox", "checkbox", "radio", "switch", "combobox", "option"}:
            score += 180
        if role == "tab":
            score += 80
        if role == "checkbox":
            score += 160
        if tag in {"button", "a", "input", "textarea", "select"}:
            score += 40
        if role == "button" and tag == "span":
            score += 90
        if role == "button" and tag == "div":
            score -= 60
        if node.get("_data_region_item_count"):
            score += 700 + min(int(node.get("_data_region_item_count") or 0), 100) + int(node.get("depth") or 0) * 20
        text = self._normalized(" ".join(part for part in (node.get("name"), node.get("text"), node.get("label")) if part))
        if any(keyword in text for keyword in ("验证码", "获取验证码", "发送验证码", "同意", "协议", "隐私", "条款")):
            score += 120
        if text in {"注册", "提交", "确认"} and role == "button":
            score += 40
        if node.get("states", {}).get("selected") or node.get("states", {}).get("expanded"):
            score += 30
        if text:
            score += 20
        return score

    def _interactable_node_summary(self, node: dict) -> dict:
        summary = {
            "ref": node["ref"],
            "role": node["role"],
            "name": node["name"],
            "tag": node.get("tag"),
            "text": node.get("text"),
            "placeholder": node.get("placeholder"),
            "label": node.get("label"),
            "input_type": node.get("input_type"),
            "value": node.get("value"),
            "checked": node.get("states", {}).get("checked"),
            "selected": node.get("states", {}).get("selected"),
        }
        return self._filter_empty_fields(summary)

    def _surface_node_summary(self, node: dict, children: dict[str, list[dict]]) -> dict:
        return {
            "ref": node["ref"],
            "ref_type": node["ref_type"],
            "tag": node["tag"],
            "role": node["role"],
            "name": node["name"],
            "text": node["text"],
            "parent_ref": node.get("parent_ref"),
            "placeholder": node.get("placeholder"),
            "label": node.get("label"),
            "input_type": node.get("input_type"),
            "value": node.get("value"),
            "in_viewport": node["visibility"]["in_viewport"],
            "interactable_now": node["visibility"]["interactable_now"],
            "child_count": len(children.get(node["ref"], [])),
            "item_count": node.get("_data_region_item_count"),
        }

    def _deep_node_summary(self, node: dict) -> dict:
        return {
            "ref": node["ref"],
            "ref_type": node["ref_type"],
            "tag": node["tag"],
            "role": node["role"],
            "name": node["name"][:40] if node["name"] else "",
            "text": node["text"][:60] if node["text"] else "",
            "parent_ref": node.get("parent_ref"),
            "in_viewport": node["visibility"]["in_viewport"],
        }

    @staticmethod
    def _filter_empty_fields(obj: dict) -> dict:
        result = {}
        for key, value in obj.items():
            if value is None:
                continue
            if value == "":
                continue
            if value == []:
                continue
            if value == {}:
                continue
            if isinstance(value, dict):
                filtered = CliService._filter_empty_fields(value)
                if filtered:
                    result[key] = filtered
            elif isinstance(value, list):
                filtered_list = [
                    CliService._filter_empty_fields(item) if isinstance(item, dict) else item
                    for item in value
                    if item not in (None, "", [], {})
                ]
                if filtered_list:
                    result[key] = filtered_list
            else:
                result[key] = value
        return result
