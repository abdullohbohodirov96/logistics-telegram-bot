"""
Expected round-trip / unload duration per vehicle, keyed by car number.

This is the reference table the dispatcher provided: each vehicle type has
a standard expected time from "started loading" to "free again" (bo'shash).
Used by the dashboard to show an estimated free-up time / countdown next
to each car that's currently loading or en route, instead of just an
unqualified "Yuk ortyapti" / "Yo'lda" label with no sense of how long.

To add/change a vehicle: edit VEHICLE_DURATIONS below (car_number ->
(vehicle_type, expected_minutes)). Car numbers are matched after
normalization (spaces/dashes stripped, uppercased), so formatting
("01 655 OLA" vs "01655OLA") doesn't matter.
"""
import re


def _normalize(value: str) -> str:
    if not value:
        return ""
    cleaned = str(value).strip().upper()
    return re.sub(r"[\s\-]+", "", cleaned)


# car_number -> (vehicle_type, expected_minutes)
_RAW_TABLE = [
    ("ISUZU",       "01 R 169 JB", 150),
    ("GAZEL",       "01 K 276 JB", 150),
    ("GAZEL",       "01 K 278 JB", 150),
    ("GAZEL",       "01 Q 136 ZB", 150),
    ("GAZEL",       "01 X 265 GB", 150),
    ("GAZEL",       "01 W 376 KB", 150),
    ("CHANGAN BIG", "01 N 245 AD", 120),
    ("CHANGAN BIG", "01 N 274 AD", 120),
    ("CHANGAN BIG", "01 X 601 KC", 120),
    ("CHANGAN",     "01 D 974 UB", 120),
    ("CHANGAN",     "01 G 847 BC", 120),
    ("CHANGAN",     "01 N 418 AC", 120),
    ("CHANGAN",     "01 N 483 AC", 120),
    ("CHANGAN",     "01 P 054 SC", 120),
    ("CHANGAN",     "01 P 058 SC", 120),
    ("CHANGAN",     "01 S 439 TC", 120),
    ("CHANGAN",     "01 X 486 PC", 120),
    ("CHANGAN",     "01 Y 048 KC", 120),
    ("LABO",        "01 065 LMA", 120),
    ("LABO",        "01 120 LMA", 120),
    ("LABO",        "01 623 JNA", 120),
    ("LABO",        "01 660 OLA", 120),
    ("LABO",        "01 795 CMA", 120),
    ("LABO",        "01 A 366 UB", 120),
    ("LABO",        "01 V 695 RB", 120),
    ("LABO",        "01 V 698 RB", 120),
    ("LABO",        "01 322 OLA", 120),
    ("LABO",        "01 388 OLA", 120),
    ("LABO",        "01 446 OLA", 120),
    ("LABO",        "01 529 JNA", 120),
    ("LABO",        "01 556 OLA", 120),
    ("LABO",        "01 611 OLA", 120),
    ("LABO",        "01 655 OLA", 120),
    ("LABO",        "01 662 OLA", 120),
    ("LABO",        "01 669 OLA", 120),
    ("LABO",        "01 959 ZJA", 120),
    ("LABO",        "01 D 609 BD", 120),
    ("LABO",        "01 J 057 BD", 120),
    ("LABO",        "01 J 298 BD", 120),
    ("DAMAS",       "01 035 ZMA", 120),
    ("DAMAS",       "01 353 ZMA", 120),
    ("DAMAS",       "01 E 523 AD", 120),
    ("DAMAS",       "01 T 051 OB", 120),
    ("DAMAS",       "01 E 461 AD", 120),
    ("CHANGAN BIG", "01 R 118 NC", 120),
    ("CHANGAN BIG", "01 R 599 NC", 120),
    ("CHANGAN",     "01F433SC",    120),
    ("GAZEL",       "01 566 ULA", 150),
    ("GAZEL",       "01 636 ULA", 150),
    ("LABO",        "01 570 ZMA", 120),
]

VEHICLE_DURATIONS = {
    _normalize(car_number): (vehicle_type, minutes)
    for vehicle_type, car_number, minutes in _RAW_TABLE
}


def get_expected_duration(car_number: str):
    """Returns (vehicle_type, expected_minutes) or None if this car isn't in the table."""
    return VEHICLE_DURATIONS.get(_normalize(car_number))


def get_vehicle_type(car_number: str):
    """Returns just the vehicle type string (e.g. 'GAZEL', 'LABO') or None."""
    entry = VEHICLE_DURATIONS.get(_normalize(car_number))
    return entry[0] if entry else None
