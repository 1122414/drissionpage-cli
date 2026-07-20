from __future__ import annotations

from dp_cli.adapter import DrissionPageAdapter


class _NativeScroll:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.centers: list[bool | None] = []

    def to_see(self, center: bool | None = None) -> None:
        self.centers.append(center)
        if self.fail:
            raise RuntimeError("native scroll unavailable")


class _Element:
    def __init__(self, *, native_fails: bool = False) -> None:
        self.scroll = _NativeScroll(fail=native_fails)
        self.scripts: list[str] = []

    def run_js(self, script: str) -> None:
        self.scripts.append(script)


def test_scroll_into_view_uses_drissionpage_native_waiting_scroll() -> None:
    element = _Element()

    DrissionPageAdapter().scroll_into_view(element)

    assert element.scroll.centers == [True]
    assert element.scripts == []


def test_scroll_into_view_falls_back_to_javascript() -> None:
    element = _Element(native_fails=True)

    DrissionPageAdapter().scroll_into_view(element)

    assert element.scroll.centers == [True]
    assert len(element.scripts) == 1
    assert "scrollIntoView" in element.scripts[0]
