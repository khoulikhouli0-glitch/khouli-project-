import wave_analysis as wave
import smc_analysis as smc


def collect_zones(df, direction: str) -> list:
    zones = []

    major_wave = wave.find_major_swing(df)
    if major_wave is not None:
        expected_wave_dir = "up" if direction == "bullish" else "down"
        if major_wave["direction"] == expected_wave_dir:
            fib = wave.fibonacci_zone(major_wave)
            zones.append({"top": fib["top"], "bottom": fib["bottom"], "source": "Fibonacci"})

    ob = smc.find_last_order_block(df, direction=direction)
    if ob is not None:
        zones.append({"top": ob["top"], "bottom": ob["bottom"], "source": "Order Block"})

    fvg = smc.find_recent_fvg(df, direction=direction)
    if fvg is not None:
        zones.append({"top": fvg["top"], "bottom": fvg["bottom"], "source": "Fair Value Gap"})

    return zones


def zones_overlap(zone_a: dict, zone_b: dict) -> bool:
    return zone_a["bottom"] <= zone_b["top"] and zone_b["bottom"] <= zone_a["top"]


def find_confluence(zones_a: list, zones_b: list, label_a: str, label_b: str) -> list:
    confluences = []
    for za in zones_a:
        for zb in zones_b:
            if zones_overlap(za, zb):
                top = min(za["top"], zb["top"])
                bottom = max(za["bottom"], zb["bottom"])
                confluences.append({
                    "top": top,
                    "bottom": bottom,
                    "source": f"{label_a} {za['source']} + {label_b} {zb['source']}",
                })
    return confluences


def price_in_any_zone(price: float, zones: list, tolerance_pct: float = 0.0008):
    for z in zones:
        top = z["top"] * (1 + tolerance_pct)
        bottom = z["bottom"] * (1 - tolerance_pct)
        if bottom <= price <= top:
            return z
    return None
