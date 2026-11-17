"""No two adapter mixins define the same method.

Two mixins of ``PyWin32Adapter`` that define the same name silently shadow
each other: the one earlier in the MRO wins. On ``dipper-gaps`` the bodies
mixin's ``_run_on_part`` hid the Save Bodies mixin's, and every
``save_body_as_part`` failed with "unexpected keyword argument" on SolidWorks
only - the unit tests of each mixin passed on their own.
"""

from __future__ import annotations

from collections import defaultdict

from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter

#: Names every mixin may define alike: the same ``self`` -> adapter cast.
_SHARED = {"_adapter"}


def test_no_two_mixins_define_the_same_method() -> None:
    owners: dict[str, list[str]] = defaultdict(list)
    for klass in PyWin32Adapter.__mro__:
        if klass.__name__.endswith("Mixin"):
            for name, value in vars(klass).items():
                if callable(value) and not name.startswith("__"):
                    owners[name].append(klass.__name__)

    clashes = {
        name: classes
        for name, classes in owners.items()
        if len(classes) > 1 and name not in _SHARED
    }

    assert not clashes, f"methods defined in more than one mixin: {clashes}"
