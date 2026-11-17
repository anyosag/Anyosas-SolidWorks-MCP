"""A density for a part that has no library material (``set_density``).

"The Dipper" was printed in a resin of 1.15 g/cm3 that no SolidWorks library
holds, so its mass properties meant nothing. SolidWorks has three ways to give
a part a mass:

* a **material**: a library entry (``IPartDoc.SetMaterialPropertyName2``).
  A custom one means writing a ``.sldmat`` file into the user's material
  folders, which changes their libraries, not just this part;
* a **mass override** (``IMassProperty.OverrideMass`` and
  ``SetOverrideMassValue``): a fixed mass, which stays the same when the part
  changes, so the mass would go stale;
* the **document density** (Document Properties > Material Properties >
  Density): ``IModelDocExtension.SetUserPreferenceDouble(
  swMaterialPropertyDensity = 7, swDetailingNoOptionSpecified = 0, kg/m3)``.
  SolidWorks uses it for a part with no material assigned, and it is saved
  with the part.

``set_density`` uses the document density: it follows the geometry and
changes nothing outside the part. A material, when one is assigned, takes
precedence, so the material must come off first (``remove_material``, an
empty ``SetMaterialPropertyName2``). The density is read back two ways: the
preference itself, and the density ``CreateMassProperty`` computes with
(``applied``). On SOLIDWORKS 2026 SP4.1
(``tests/scripts/probe_dipper_analysis.py``) the preference read back 1150
and the mass properties used 1.15 g/cm3; with ABS assigned the call is
refused, and ``remove_material`` takes it off.
"""

from __future__ import annotations

from typing import Any

from .. import sw_type_info as _sw_type_info
from .clearance import InvalidRequest
from .com_utils import attempt as _attempt
from .com_utils import flag as _flag

#: ``swUserPreferenceDoubleValue_e.swMaterialPropertyDensity`` (SW 2026 swconst).
MATERIAL_PROPERTY_DENSITY = 7
#: ``swUserPreferenceOption_e.swDetailingNoOptionSpecified``.
NO_OPTION = 0
#: How far (g/cm3) the density mass properties use may be from the one set.
TOLERANCE_G_CM3 = 1e-6
#: Where the value lives, for the reply.
METHOD = "document_density"
PERSISTS = (
    "saved with the part, as Document Properties > Material Properties > "
    "Density; SolidWorks uses it while no material is assigned"
)


def material_message(name: str, database: str) -> str:
    """The refusal for a part that has a material, whose density wins."""
    library = f" ({database})" if database else ""
    return (
        f"the part has the material {name}{library}, and SolidWorks uses that "
        "material's density, not a document density. Call again with "
        "remove_material true to take the material off, or assign a material "
        "that has the density with set_material."
    )


def set_density(
    adapter: Any, density_g_cm3: float, remove_material: bool = False
) -> dict[str, Any]:
    """Give the active part ``density_g_cm3`` as its document density.

    Returns:
        dict[str, Any]: ``density_g_cm3`` and ``density_kg_m3`` asked;
        ``read_back_kg_m3`` (the preference); ``mass_properties_density_g_cm3``
        (the density SolidWorks computes with); ``applied`` (they agree);
        ``volume_mm3`` and ``mass_g`` from the mass properties;
        ``material_removed`` (the material taken off, or None); ``method`` and
        ``persists``.

    Raises:
        InvalidRequest: The part has a material and ``remove_material`` is off.
        RuntimeError: The material couldn't be taken off, or SolidWorks
            refused the value.
    """
    from .documentation import _read_material
    from .plane_volume import part_density_g_cm3

    model = adapter.currentModel
    part = _sw_type_info.flagged(model, "IPartDoc")
    name, database = _read_material(part, None)
    removed = None
    if name:
        if not remove_material:
            raise InvalidRequest(material_message(name, database))
        part.SetMaterialPropertyName2("", "", "")
        left, _library = _read_material(part, None)
        if left:
            raise RuntimeError(
                f"SolidWorks kept the material {left} when asked to remove it; "
                "nothing else was changed."
            )
        removed = {"material": name, "database": database or None}
    extension = _attempt(adapter, lambda: model.Extension)
    _flag(adapter, extension, "SetUserPreferenceDouble", "GetUserPreferenceDouble")
    kg_m3 = float(density_g_cm3) * 1000.0
    accepted = _attempt(
        adapter,
        lambda: extension.SetUserPreferenceDouble(
            MATERIAL_PROPERTY_DENSITY, NO_OPTION, kg_m3
        ),
        False,
    )
    if not accepted:
        raise RuntimeError(
            "SolidWorks refused the density (SetUserPreferenceDouble "
            "swMaterialPropertyDensity returned False)."
        )
    read_back = _attempt(
        adapter,
        lambda: extension.GetUserPreferenceDouble(MATERIAL_PROPERTY_DENSITY, NO_OPTION),
    )
    _flag(adapter, model, "EditRebuild3")
    _attempt(adapter, lambda: model.EditRebuild3())
    used = part_density_g_cm3(adapter, model)
    volume, mass = _mass_facts(adapter, model)
    return {
        "density_g_cm3": float(density_g_cm3),
        "density_kg_m3": kg_m3,
        "read_back_kg_m3": (
            float(read_back) if isinstance(read_back, (int, float)) else None
        ),
        "mass_properties_density_g_cm3": used,
        "applied": used is not None
        and abs(used - float(density_g_cm3)) <= TOLERANCE_G_CM3,
        "volume_mm3": volume,
        "mass_g": mass,
        "material_removed": removed,
        "method": METHOD,
        "persists": PERSISTS,
    }


def _mass_facts(adapter: Any, model: Any) -> tuple[float | None, float | None]:
    """The part's volume (mm3) and mass (g) from ``CreateMassProperty``.

    ``Mass`` is read first: on SOLIDWORKS 2026 SP4.1, read after ``Volume``
    on the same object it gave the volume (1.6e-05 for a 16,000 mm3 block,
    which ``set_density`` reported as 0.016 g instead of 18.4 g).
    """
    extension = _attempt(adapter, lambda: model.Extension)
    _flag(adapter, extension, "CreateMassProperty")
    props = _attempt(adapter, lambda: extension.CreateMassProperty())
    mass = _attempt(adapter, lambda: props.Mass) if props is not None else None
    volume = _attempt(adapter, lambda: props.Volume) if props is not None else None
    return (
        round(float(volume) * 1e9, 4) + 0.0
        if isinstance(volume, (int, float))
        else None,
        round(float(mass) * 1000.0, 6) + 0.0
        if isinstance(mass, (int, float))
        else None,
    )
