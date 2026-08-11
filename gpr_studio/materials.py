"""Material library for gprStudio.

Each material carries the four parameters gprMax's ``#material`` command expects:
relative permittivity (er), conductivity (sigma, S/m), relative permeability
(mu_r) and magnetic loss (sigma*).  Values are typical literature figures for
building materials at GPR frequencies -- they are approximate and site
dependent, so the UI lets the user edit them.

Steel / metal reinforcement is modelled with gprMax's built-in perfect electric
conductor ``pec`` rather than a ``#material`` line, so it is flagged separately.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# The user's working library is persisted here so edits (deletions, colours,
# property changes) survive a page refresh / new session. Overridable via an
# env var so tests / the app test harness never touch the real user file.
LIBRARY_FILE = Path(os.environ.get(
    "GPR_STUDIO_LIBRARY_FILE",
    str(Path(__file__).resolve().parent / "materials_library.json")))


@dataclass
class Material:
    """A single building material and its EM properties."""

    name: str            # gprMax identifier (no spaces)
    label: str           # human friendly name for the UI
    er: float            # relative permittivity
    sigma: float         # conductivity [S/m]
    mu_r: float = 1.0    # relative permeability
    sigma_star: float = 0.0  # magnetic loss
    color: str = "#cccccc"   # preview colour
    note: str = ""
    is_pec: bool = False     # True -> use built-in 'pec', emit no #material line

    def material_line(self) -> str | None:
        """Return the gprMax ``#material`` line, or None for built-in pec."""
        if self.is_pec:
            return None
        return (
            f"#material: {self.er:g} {self.sigma:g} {self.mu_r:g} "
            f"{self.sigma_star:g} {self.name}"
        )

    def velocity(self) -> float:
        """Approximate EM wave velocity in this material [m/s]."""
        C0 = 299792458.0
        return C0 / (self.er ** 0.5)


# Ordered library of the materials a concrete inspector meets most often.
# ``name`` values must be valid gprMax identifiers (letters, digits, underscore).
DEFAULT_MATERIALS: list[Material] = [
    Material("air", "Air / void", 1.0, 0.0, color="#eaf6ff",
             note="Empty (air-filled) void or defect"),
    Material("dry_concrete", "Dry / cured concrete", 6.0, 0.01, color="#b0b0b0",
             note="Typical background for mature concrete"),
    Material("wet_concrete", "Wet / saturated concrete", 12.0, 0.05, color="#7d8a99",
             note="High moisture content"),
    Material("fresh_concrete", "Fresh / green concrete", 9.0, 0.03, color="#9aa7a0",
             note="Recently poured / curing"),
    Material("asphalt", "Asphalt", 4.0, 0.005, color="#3a3a3a",
             note="Overlay / wearing course"),
    Material("dry_sand", "Dry sand (subbase)", 4.0, 0.001, color="#e4d5a1",
             note="Dry granular foundation"),
    Material("wet_sand", "Wet sand", 25.0, 0.02, color="#c9b06a",
             note="Saturated granular base"),
    Material("gravel", "Gravel / crushed base", 6.0, 0.005, color="#a89a86",
             note="Compacted base course"),
    Material("clay", "Clay soil", 20.0, 0.1, color="#8a6a4a",
             note="Lossy cohesive subgrade"),
    Material("pvc", "PVC (conduit)", 3.0, 0.0, color="#d8d0c0",
             note="Plastic duct / conduit wall"),
    Material("eps", "EPS (foam insulation)", 1.1, 0.0, color="#f2e8f2",
             note="Expanded polystyrene: insulation, void former, sandwich core"),
    Material("water", "Water (wet void)", 81.0, 0.05, color="#4a90d9",
             note="Water-filled defect / duct"),
    Material("steel", "Steel rebar / metal", 1.0, 0.0, color="#202020",
             note="Reinforcement, sheath, plate -> perfect conductor", is_pec=True),
]


def default_library() -> dict[str, Material]:
    """Return a fresh name -> Material mapping (copies, safe to mutate)."""
    return {m.name: Material(**vars(m)) for m in DEFAULT_MATERIALS}


def library_to_records(materials: dict[str, Material]) -> list[dict]:
    """Serialise a material library to a list of plain dicts."""
    return [{"name": m.name, "label": m.label, "er": m.er, "sigma": m.sigma,
             "mu_r": m.mu_r, "sigma_star": m.sigma_star, "color": m.color,
             "note": m.note, "is_pec": m.is_pec} for m in materials.values()]


def library_from_records(records: list[dict]) -> dict[str, Material]:
    """Rebuild a material library from serialised records (tolerant)."""
    lib: dict[str, Material] = {}
    for r in records:
        try:
            lib[r["name"]] = Material(
                name=r["name"], label=r.get("label", r["name"]),
                er=float(r["er"]), sigma=float(r["sigma"]),
                mu_r=float(r.get("mu_r", 1.0)),
                sigma_star=float(r.get("sigma_star", 0.0)),
                color=r.get("color", "#cccccc"), note=r.get("note", ""),
                is_pec=bool(r.get("is_pec", False)))
        except (KeyError, TypeError, ValueError):
            continue
    return lib


def is_pristine_default(materials: dict[str, Material]) -> bool:
    """True if the library is exactly the built-in defaults (nothing to save)."""
    return (library_to_records(materials)
            == library_to_records(default_library()))


def save_library(materials: dict[str, Material]) -> None:
    """Persist the working library to disk (atomic, best-effort).

    Refuses to write the pristine built-in defaults so a fresh/default session
    can never overwrite a user's customised file.
    """
    if is_pristine_default(materials):
        return
    try:
        tmp = LIBRARY_FILE.with_name(LIBRARY_FILE.name + ".tmp")
        tmp.write_text(json.dumps(library_to_records(materials), indent=2),
                       encoding="utf-8")
        tmp.replace(LIBRARY_FILE)
    except OSError:
        pass


def delete_library() -> None:
    """Remove the persisted library (used by an explicit reset)."""
    try:
        LIBRARY_FILE.unlink()
    except OSError:
        pass


def load_library() -> dict[str, Material] | None:
    """Load the persisted library, or None if absent/unreadable."""
    if not LIBRARY_FILE.exists():
        return None
    try:
        lib = library_from_records(
            json.loads(LIBRARY_FILE.read_text(encoding="utf-8")))
        return lib or None
    except (OSError, json.JSONDecodeError):
        return None


def material_lines(materials: dict[str, Material], used_names: set[str]) -> list[str]:
    """Return ``#material`` lines for the (non-pec) materials actually used."""
    lines: list[str] = []
    for name in used_names:
        mat = materials.get(name)
        if mat is None:
            continue
        line = mat.material_line()
        if line is not None:
            lines.append(line)
    return lines
