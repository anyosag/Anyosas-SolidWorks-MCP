"""``export_image``: a view from any direction, Z-up standard views, components hidden.

From The Dipper build (a Z-up lab part with a hinged cover, SOLIDWORKS 2026
SP4.1): every standard view showed the part on end, so each render went
through a throwaway assembly with the part turned -90 degrees about X; and to
render it "cover off" the cover was moved 60 mm aside and back.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters import pywin32_adapter
from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter
from solidworks_mcp.adapters.solidworks import component_visibility as cv
from solidworks_mcp.adapters.solidworks import view_matrix as vm
from solidworks_mcp.adapters.solidworks import window as window_module
from solidworks_mcp.adapters.solidworks.visibility import DISPLAY_TOGGLES

from .solidworks.test_component_visibility import _Component
from .test_pywin32_adapter_image_options import _Extension


@pytest.fixture(autouse=True)
def _no_keepers() -> Iterator[None]:
    yield
    for keeper in list(window_module._KEEPERS.values()):
        keeper.stop()
    window_module._KEEPERS.clear()


@pytest.fixture(autouse=True)
def _plain_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cv, "_as_com", lambda _adapter, obj, _iface: obj)


class _Transform:
    def __init__(self, data: Any) -> None:
        self.ArrayData = tuple(getattr(data, "value", data))


class _Point:
    """An ``IMathPoint``: ``MultiplyTransform`` is ``p . R`` (no translation)."""

    def __init__(self, data: Any) -> None:
        self.ArrayData = tuple(getattr(data, "value", data))

    def MultiplyTransform(self, transform: Any) -> Any:  # noqa: N802
        rows = vm.rows_from_array(transform.ArrayData)
        p = self.ArrayData
        return _Point([sum(p[k] * rows[k][j] for k in range(3)) for j in range(3)])


class _View:
    def __init__(self, doc: _Doc) -> None:
        self.doc = doc
        self.Orientation3 = _Transform(
            vm.with_rows(None, vm.standard_rows("front") or [])
        )


class _Doc:
    """A document whose view turns, whose components hide, and that logs it all."""

    #: The orientation each standard view id shows.
    VIEWS = {
        view_id: vm.standard_rows(name)
        for name, view_id in vm.STANDARD_VIEW_IDS.items()
        if vm.standard_rows(name)
    }

    def __init__(self, kind: int = 1, shows_views: bool = True) -> None:
        self.kind = kind
        self.shows_views = shows_views
        self.events: list[Any] = []
        self.Extension = _Extension()
        self.ActiveView = _View(self)
        self.components = [_Component("base-1"), _Component("cover-1")]
        self.SelectionManager = SimpleNamespace(CreateSelectData=lambda: "data")
        self.at_capture: dict[str, Any] = {}
        self.writes = True

    def GetType(self) -> int:  # noqa: N802 - COM name
        return self.kind

    def GetComponents(self, _top_level_only: bool) -> list[_Component]:  # noqa: N802
        return self.components

    def ClearSelection2(self, _all: bool) -> None:  # noqa: N802
        self.events.append("clear")

    def ShowNamedView2(self, _name: str, view_id: int) -> None:  # noqa: N802
        self.events.append(("view", view_id))
        if not self.shows_views:
            raise RuntimeError("lightweight components")
        self.ActiveView.Orientation3 = _Transform(
            vm.with_rows(None, self.VIEWS.get(view_id) or self.VIEWS[1])
        )

    def GraphicsRedraw2(self) -> None:  # noqa: N802
        self.events.append("redraw")

    def ViewZoomtofit2(self) -> None:  # noqa: N802
        self.events.append("fit")

    def ViewZoomTo2(self, *corners: float) -> None:  # noqa: N802
        self.events.append(("zoom", corners))

    def SaveAs3(self, path: str, *_args: Any) -> bool:  # noqa: N802
        self.events.append("picture")
        self.at_capture = {
            "orientation": vm.rows_from_array(self.ActiveView.Orientation3.ArrayData),
            "visible": {c.Name2: c.Visible for c in self.components},
            "planes": self.Extension.toggles[DISPLAY_TOGGLES["planes"]],
        }
        if self.writes:
            Path(path).write_bytes(b"II*")
        return self.writes


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> PyWin32Adapter:
    monkeypatch.setattr(pywin32_adapter, "PYWIN32_AVAILABLE", True)
    monkeypatch.setattr(pywin32_adapter.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        pywin32_adapter, "pywintypes", SimpleNamespace(com_error=RuntimeError)
    )
    monkeypatch.setenv("SOLIDWORKS_MCP_DIALOG_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("SOLIDWORKS_MCP_KEEP_MINIMIZED", "0")
    made = PyWin32Adapter({})
    utility = SimpleNamespace(CreateTransform=_Transform, CreatePoint=_Point)
    made.swApp = SimpleNamespace(
        ActiveDoc=None, Frame=SimpleNamespace(), GetMathUtility=lambda: utility
    )
    made.currentModel = _Doc()
    return made


def _flat(rows: Any) -> list[float]:
    return [float(x) for row in rows for x in row]


async def _picture(adapter: PyWin32Adapter, tmp_path: Path, **payload: Any) -> Any:
    return await adapter.export_image({"file_path": str(tmp_path / "p.tif")} | payload)


# ---- a view from a direction --------------------------------------------------------


async def test_a_view_from_a_direction_turns_the_view_itself(
    adapter: PyWin32Adapter, tmp_path: Path
) -> None:
    doc = adapter.currentModel

    result = await _picture(adapter, tmp_path, view_direction=[1, -1, 1], up=[0, 0, 1])

    assert result.is_success, result.error
    wanted = vm.look_from([1, -1, 1], [0, 0, 1])
    assert _flat(doc.at_capture["orientation"]) == pytest.approx(_flat(wanted))
    assert not any(isinstance(e, tuple) and e[0] == "view" for e in doc.events)
    assert "fit" in doc.events  # a turned view zooms to fit, as a named one does
    assert result.data["view"] == "custom"
    assert result.data["view_axes"] == vm.axes(wanted)
    assert result.data["up_axis"] == "y"


async def test_a_zoom_box_follows_the_turned_view(
    adapter: PyWin32Adapter, tmp_path: Path
) -> None:
    """Pitfall #42: ViewZoomTo2 takes view coordinates, of the view just set."""
    doc = adapter.currentModel

    result = await _picture(
        adapter,
        tmp_path,
        view_direction=[0, -1, 0],
        up=[0, 0, 1],
        zoom_box_mm=[0, 0, 0, 10, 20, 30],
    )

    assert result.is_success, result.error
    (corners,) = [e[1] for e in doc.events if isinstance(e, tuple) and e[0] == "zoom"]
    # From -Y with Z up: screen x is model x, screen y model z, depth -y.
    assert corners == pytest.approx((0.0, 0.0, -0.02, 0.01, 0.03, 0.0))
    assert result.data["zoom_box_view_mm"] == pytest.approx([0, 0, -20, 10, 30, 0])


async def test_a_bad_direction_takes_no_picture(
    adapter: PyWin32Adapter, tmp_path: Path
) -> None:
    result = await _picture(adapter, tmp_path, view_direction=[0, 0, 2], up=[0, 0, 1])

    assert result.is_error
    assert "up lies along view_direction" in result.error
    assert adapter.currentModel.events == []


async def test_a_view_solidworks_wont_take_shows_everything_again(
    adapter: PyWin32Adapter, tmp_path: Path
) -> None:
    doc = adapter.currentModel = _Doc(kind=2)

    class _Stuck(_View):
        def __setattr__(self, name: str, value: Any) -> None:
            if name == "Orientation3" and hasattr(self, "Orientation3"):
                return
            super().__setattr__(name, value)

    doc.ActiveView = _Stuck(doc)

    result = await _picture(
        adapter,
        tmp_path,
        view_direction=[1, 0, 0],
        hide=["planes"],
        hide_components=["cover-1"],
    )

    assert result.is_error
    assert "kept a different view orientation" in result.error
    assert "picture" not in doc.events
    assert doc.components[1].Visible == cv.VISIBLE
    assert doc.Extension.toggles[DISPLAY_TOGGLES["planes"]] is True


# ---- standard views turned Z-up -----------------------------------------------------


@pytest.mark.parametrize(
    ("orientation", "view_id", "toward", "up"),
    [
        ("isometric", 7, None, None),
        ("front", 1, [0, -1, 0], [0, 0, 1]),
        ("top", 5, [0, 0, 1], [0, 1, 0]),
    ],
)
async def test_a_standard_view_is_turned_z_up(
    adapter: PyWin32Adapter,
    tmp_path: Path,
    orientation: str,
    view_id: int,
    toward: list[float] | None,
    up: list[float] | None,
) -> None:
    doc = adapter.currentModel

    result = await _picture(
        adapter, tmp_path, view_orientation=orientation, up_axis="z"
    )

    assert result.is_success, result.error
    assert ("view", view_id) in doc.events
    wanted = vm.z_up(vm.standard_rows(orientation))
    assert _flat(doc.at_capture["orientation"]) == pytest.approx(_flat(wanted))
    assert result.data["view"] == orientation
    assert result.data["up_axis"] == "z"
    if toward is not None:
        assert result.data["view_axes"]["toward_viewer"] == pytest.approx(toward)
        assert result.data["view_axes"]["up"] == pytest.approx(up)


async def test_a_standard_view_without_up_axis_is_not_turned(
    adapter: PyWin32Adapter, tmp_path: Path
) -> None:
    doc = adapter.currentModel

    result = await _picture(adapter, tmp_path, view_orientation="isometric")

    assert result.is_success, result.error
    assert _flat(doc.at_capture["orientation"]) == pytest.approx(
        _flat(vm.standard_rows("isometric"))
    )
    assert result.data["view_axes"] is None


async def test_a_view_solidworks_didnt_show_isnt_turned_z_up(
    adapter: PyWin32Adapter, tmp_path: Path
) -> None:
    doc = adapter.currentModel = _Doc(shows_views=False)

    result = await _picture(adapter, tmp_path, view_orientation="right", up_axis="z")

    assert result.is_error
    assert "didn't show the right view" in result.error
    assert "picture" not in doc.events


async def test_up_axis_z_needs_a_standard_view(
    adapter: PyWin32Adapter, tmp_path: Path
) -> None:
    result = await _picture(adapter, tmp_path, view_orientation="current", up_axis="z")

    assert "'current' isn't one" in result.error
    assert adapter.currentModel.events == []


# ---- components hidden for the picture ----------------------------------------------


async def test_components_are_hidden_for_the_picture_only(
    adapter: PyWin32Adapter, tmp_path: Path
) -> None:
    doc = adapter.currentModel = _Doc(kind=2)

    result = await _picture(
        adapter, tmp_path, view_orientation="front", hide_components=["cover-1"]
    )

    assert result.is_success, result.error
    assert doc.at_capture["visible"] == {"base-1": 1, "cover-1": 0}
    assert doc.components[1].Visible == cv.VISIBLE  # shown again
    assert result.data["hidden_components"] == [
        {"name": "cover-1", "before": True, "restored": True}
    ]


async def test_a_failed_picture_still_shows_the_components_again(
    adapter: PyWin32Adapter, tmp_path: Path
) -> None:
    doc = adapter.currentModel = _Doc(kind=2)
    doc.writes = False

    result = await _picture(adapter, tmp_path, hide_components=["cover-1", "base-1"])

    assert result.is_error
    assert doc.at_capture["visible"] == {"base-1": 0, "cover-1": 0}
    assert [c.Visible for c in doc.components] == [cv.VISIBLE, cv.VISIBLE]


@pytest.mark.parametrize(
    ("kind", "names", "words"),
    [
        (1, ["cover-1"], "hide_components needs an assembly"),
        (2, ["cover-1", "lid-1"], "Component(s) not found: lid-1"),
    ],
    ids=["a part", "a missing component"],
)
async def test_components_that_cant_be_hidden_take_no_picture(
    adapter: PyWin32Adapter, tmp_path: Path, kind: int, names: list[str], words: str
) -> None:
    doc = adapter.currentModel = _Doc(kind=kind)

    result = await _picture(adapter, tmp_path, hide_components=names)

    assert result.is_error
    assert words in result.error
    assert "picture" not in doc.events
    assert [c.Visible for c in doc.components] == [cv.VISIBLE, cv.VISIBLE]
