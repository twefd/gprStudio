"""GPR equipment presets for gprStudio.

gprMax is a time-domain (FDTD) solver.  The Proceq GP8000 is a stepped-frequency
continuous-wave (SFCW) system spanning 0.2-4 GHz.  We approximate its response
with an equivalent broadband Ricker pulse whose centre frequency the user can
tune -- this is a standard, clearly-labelled approximation, not the literal SFCW
sweep.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Equipment:
    """A GPR antenna/system preset."""

    key: str
    label: str
    waveform: str            # gprMax waveform type
    center_freq_hz: float    # Ricker centre frequency
    freq_min_hz: float       # informational: usable band
    freq_max_hz: float
    tx_rx_offset_m: float    # transmitter-receiver separation
    trace_step_m: float      # default distance between traces (encoder step)
    max_depth_m: float       # nominal max useful penetration
    polarisation: str = "z"  # out-of-plane dipole for a 2D cross-section
    note: str = ""

    def waveform_line(self, amplitude: float = 1.0, name: str = "gpr_pulse") -> str:
        return (
            f"#waveform: {self.waveform} {amplitude:g} "
            f"{self.center_freq_hz:g} {name}"
        )


# Centre frequency for the GP8000 equivalent pulse.  Its band is 0.2-4 GHz; a
# ~2 GHz Ricker gives a broadband pulse well matched to concrete imaging while
# keeping the cell count reasonable.  Fully adjustable in the UI.
PROCEQ_GP8000 = Equipment(
    key="gp8000",
    label="Proceq GP8000 (SFCW 0.2-4 GHz)",
    waveform="ricker",
    center_freq_hz=2.0e9,
    freq_min_hz=0.2e9,
    freq_max_hz=4.0e9,
    tx_rx_offset_m=0.04,
    trace_step_m=0.005,
    max_depth_m=0.6,
    note="SFCW system modelled as an equivalent Ricker pulse (approximation).",
)

GENERIC_1500 = Equipment(
    key="gc1500", label="Generic 1.5 GHz ground-coupled", waveform="ricker",
    center_freq_hz=1.5e9, freq_min_hz=0.75e9, freq_max_hz=3.0e9,
    tx_rx_offset_m=0.04, trace_step_m=0.005, max_depth_m=0.5,
    note="Common concrete antenna.")

GENERIC_2000 = Equipment(
    key="gc2000", label="Generic 2.0 GHz ground-coupled", waveform="ricker",
    center_freq_hz=2.0e9, freq_min_hz=1.0e9, freq_max_hz=4.0e9,
    tx_rx_offset_m=0.035, trace_step_m=0.004, max_depth_m=0.4,
    note="High-resolution concrete antenna.")

GENERIC_2600 = Equipment(
    key="gc2600", label="Generic 2.6 GHz ground-coupled", waveform="ricker",
    center_freq_hz=2.6e9, freq_min_hz=1.3e9, freq_max_hz=5.0e9,
    tx_rx_offset_m=0.03, trace_step_m=0.004, max_depth_m=0.35,
    note="Shallow, very high resolution.")

EQUIPMENT_PRESETS: dict[str, Equipment] = {
    e.key: e for e in (PROCEQ_GP8000, GENERIC_1500, GENERIC_2000, GENERIC_2600)
}

DEFAULT_EQUIPMENT_KEY = PROCEQ_GP8000.key
