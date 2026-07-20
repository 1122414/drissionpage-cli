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


class _PageScroll:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, int | None]] = []

    def down(self, amount: int) -> None:
        self.calls.append(("down", amount))
        if self.fail:
            raise RuntimeError("native page scroll unavailable")

    def to_bottom(self) -> None:
        self.calls.append(("bottom", None))
        if self.fail:
            raise RuntimeError("native page scroll unavailable")


class _Tab:
    def __init__(self, *, native_fails: bool = False) -> None:
        self.scroll = _PageScroll(fail=native_fails)
        self.scripts: list[tuple[str, tuple[object, ...]]] = []

    def run_js(self, script: str, *args, **kwargs):
        self.scripts.append((script, args))
        return {}


class _Input:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def input(self, value: str, clear: bool = False) -> None:
        self.calls.append((value, clear))


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


def test_page_scroll_uses_native_direction_and_bottom_methods() -> None:
    tab = _Tab()
    adapter = DrissionPageAdapter()

    adapter.scroll_page(tab, direction="down", amount=700)
    adapter.scroll_page(tab, to="bottom")

    assert tab.scroll.calls == [("down", 700), ("bottom", None)]
    assert tab.scripts == []


def test_page_scroll_falls_back_to_javascript() -> None:
    tab = _Tab(native_fails=True)

    DrissionPageAdapter().scroll_page(tab, direction="down", amount=700)

    assert tab.scroll.calls == [("down", 700)]
    assert len(tab.scripts) == 1
    assert "scrollBy" in tab.scripts[0][0]
    assert tab.scripts[0][1] == (0, 700)


def test_type_text_appends_enter_when_submit_requested() -> None:
    element = _Input()

    DrissionPageAdapter().type_text(element, "Boston", submit=True)

    assert element.calls == [("Boston\n", True)]
