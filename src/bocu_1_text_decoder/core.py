"""Decoder for the BOCU-1 byte encoding defined in Unicode Technical Note #191.

BOCU-1 (Binary Ordered Compression for Unicode) is a MIME-compatible
compression scheme that maps Unicode code points into a variable-length
byte sequence. This module converts a BOCU-1 byte sequence back into a
Python string.

Decoder state
-------------
BOCU-1 is differential: every trailing byte encodes the *delta* between
the current character and the previously-decoded character. The decoder
therefore owns mutable state (``_prev``) that is updated after every
code point. We keep the state on the module-level ``_BOCU1Decoder``
instance rather than as free function arguments so that users streaming
input in chunks do not have to thread that state themselves.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# BOCU-1 constants — see UTN #191, sections 4 & 5.
# ---------------------------------------------------------------------------

#: Start of the printable-ASCII-safe reserved byte range (trailers only).
_TRAIL_MIN = 0x21
#: End of the printable-ASCII-safe reserved byte range (trailers only).
_TRAIL_MAX = 0x7E
#: Number of legal trailer values per byte (93 distinct).
_TRAIL_COUNT = _TRAIL_MAX - _TRAIL_MIN + 1   # 93
#: Size of the non-positive delta band used by the single lead byte.
_LOWER_COUNT = _TRAIL_COUNT                  # 93
#: Total number of delta values covered by a single lead byte.
_SINGLE_COUNT = 2 * _LOWER_COUNT             # 186
#: The middle code point that anchors BOCU-1 encoding.
_MIDDLE = 0x20
#: Maximum code point accepted by this decoder. (UTN #191 §8.2 limits
#: BOCU-1 to U+10FFFF as a natural consequence of the compression range.)
_MAX_CP = 0x10FFFF

# Fixed branch offsets per UTN #191 §5.4.
_BRANCH_BASE = 0x000000          # 0x21..0x7E: ±93
# 0x80..0xFB: 16 branches of 2..9 trailers covering the wide range.
_BRANCH_MIN = 0x80
_BRANCH_MAX = 0xFB               # inclusive
_BRANCH_LOW1 = 0x80
_BRANCH_LOW2 = 0x90              # implicit in the table below

# Pre-computed branch table — (bLead, bTrailCount, count, lowerBound, upperBound)
# Exact entry points for lead bytes 0x80..0xFB, per UTN #191 §5.4.
_BRANCHES = [
    (0x80, 2, _SINGLE_COUNT + 1, _MAX_CP + 1, -193),
    (0x80, 2, 0, -193, -192),
    (0x81, 3, 19, -192, -173),
    (0x82, 4, 82, -173, -91),
    (0x83, 5, 255, -91, 165),
    (0x84, 6, 590, 165, 755),
    (0x85, 6, 925, 755, 1680),
    (0x86, 7, 1745, 1680, 3425),
    (0x87, 8, 3440, 3425, 6865),
    (0x88, 9, 6881, 6865, 13746),
    (0x89, 8, 6917, 13746, 20663),
    (0x8A, 8, 6953, 20663, 27616),
    (0x8B, 8, 6989, 27616, 34605),
    (0x8C, 8, 7025, 34605, 41630),
    (0x8D, 8, 7061, 41630, 48691),
    (0x8E, 8, 7097, 48691, 55788),
    (0x8F, 8, 7133, 55788, 62921),
    (0x90, 8, 7169, 62921, 70090),
    (0x91, 8, 7205, 70090, 77295),
    (0x92, 8, 7241, 77295, 84536),
    (0x93, 8, 7277, 84536, 91813),
    (0x94, 8, 7313, 91813, 99126),
    (0x95, 8, 7349, 99126, 106475),
    (0x96, 8, 7385, 106475, 113860),
    (0x97, 8, 7421, 113860, 121281),
    (0x98, 8, 7457, 121281, 128738),
    (0x99, 8, 7493, 128738, 136231),
    (0x9A, 8, 7529, 136231, 143760),
    (0x9B, 8, 7565, 143760, 151325),
    (0x9C, 8, 7601, 151325, 158926),
    (0x9D, 8, 7637, 158926, 166563),
    (0x9E, 8, 7673, 166563, 174236),
    (0x9F, 8, 7709, 174236, 181945),
    (0xA0, 8, 7745, 181945, 189690),
    (0xA1, 8, 7781, 189690, 197471),
    (0xA2, 8, 7817, 197471, 205288),
    (0xA3, 8, 7853, 205288, 213141),
    (0xA4, 8, 7889, 213141, 221030),
    (0xA5, 8, 7925, 221030, 228955),
    (0xA6, 8, 7961, 228955, 236916),
    (0xA7, 8, 7997, 236916, 244913),
    (0xA8, 8, 8033, 244913, 252946),
    (0xA9, 8, 8069, 252946, 261015),
    (0xAA, 8, 8105, 261015, 269120),
    (0xAB, 8, 8141, 269120, 277261),
    (0xAC, 8, 8177, 277261, 285438),
    (0xAD, 8, 8213, 285438, 293651),
    (0xAE, 8, 8249, 293651, 301900),
    (0xAF, 8, 8285, 301900, 310185),
    (0xB0, 8, 8321, 310185, 318506),
    (0xB1, 8, 8357, 318506, 326863),
    (0xB2, 8, 8393, 326863, 335256),
    (0xB3, 8, 8429, 335256, 343685),
    (0xB4, 8, 8465, 343685, 352150),
    (0xB5, 8, 8501, 352150, 360651),
    (0xB6, 8, 8537, 360651, 369188),
    (0xB7, 8, 8573, 369188, 377761),
    (0xB8, 8, 8609, 377761, 386370),
    (0xB9, 8, 8645, 386370, 395015),
    (0xBA, 8, 8681, 395015, 403696),
    (0xBB, 8, 8717, 403696, 412413),
    (0xBC, 8, 8753, 412413, 421166),
    (0xBD, 8, 8789, 421166, 429955),
    (0xBE, 8, 8825, 429955, 438780),
    (0xBF, 8, 8861, 438780, 447641),
    (0xC0, 8, 8897, 447641, 456538),
    (0xC1, 8, 8933, 456538, 465471),
    (0xC2, 8, 8969, 465471, 474440),
    (0xC3, 8, 9005, 474440, 483445),
    (0xC4, 8, 9041, 483445, 492486),
    (0xC5, 8, 9077, 492486, 501563),
    (0xC6, 8, 9113, 501563, 510676),
    (0xC7, 8, 9149, 510676, 519825),
    (0xC8, 8, 9185, 519825, 529010),
    (0xC9, 8, 9221, 529010, 538231),
    (0xCA, 8, 9257, 538231, 547488),
    (0xCB, 8, 9293, 547488, 556781),
    (0xCC, 8, 9329, 556781, 566110),
    (0xCD, 8, 9365, 566110, 575475),
    (0xCE, 8, 9401, 575475, 584876),
    (0xCF, 8, 9437, 584876, 594313),
    (0xD0, 8, 9473, 594313, 603786),
    (0xD1, 8, 9509, 603786, 613295),
    (0xD2, 8, 9545, 613295, 622840),
    (0xD3, 8, 9581, 622840, 632421),
    (0xD4, 8, 9617, 632421, 642038),
    (0xD5, 8, 9653, 642038, 651691),
    (0xD6, 8, 9689, 651691, 661380),
    (0xD7, 8, 9725, 661380, 671105),
    (0xD8, 8, 9761, 671105, 680866),
    (0xD9, 8, 9797, 680866, 690663),
    (0xDA, 8, 9833, 690663, 700496),
    (0xDB, 8, 9869, 700496, 710365),
    (0xDC, 8, 9905, 710365, 720270),
    (0xDD, 8, 9941, 720270, 730211),
    (0xDE, 8, 9977, 730211, 740188),
    (0xDF, 8, 10013, 740188, 750201),
    (0xE0, 8, 10049, 750201, 760250),
    (0xE1, 8, 10085, 760250, 770335),
    (0xE2, 8, 10121, 770335, 780456),
    (0xE3, 8, 10157, 780456, 790613),
    (0xE4, 8, 10193, 790613, 800806),
    (0xE5, 8, 10229, 800806, 811035),
    (0xE6, 8, 10265, 811035, 821300),
    (0xE7, 8, 10301, 821300, 831601),
    (0xE8, 8, 10337, 831601, 841938),
    (0xE9, 8, 10373, 841938, 852311),
    (0xEA, 8, 10409, 852311, 862720),
    (0xEB, 8, 10445, 862720, 873165),
    (0xEC, 8, 10481, 873165, 883646),
    (0xED, 8, 10517, 883646, 894163),
    (0xEE, 8, 10553, 894163, 904716),
    (0xEF, 8, 10589, 904716, 915305),
    (0xF0, 8, 10625, 915305, 925930),
    (0xF1, 8, 10661, 925930, 936591),
    (0xF2, 8, 10697, 936591, 947288),
    (0xF3, 8, 10733, 947288, 958021),
    (0xF4, 8, 10769, 958021, 968790),
    (0xF5, 8, 10805, 968790, 979595),
    (0xF6, 8, 10841, 979595, 990436),
    (0xF7, 8, 10877, 990436, 1001313),
    (0xF8, 8, 10913, 1001313, 1012226),
    (0xF9, 8, 10949, 1012226, 1023175),
    (0xFA, 8, 10985, 1023175, 1034160),
    (0xFB, 9, 11021, 1034160, 1045181),
]


class BOCU1DecodeError(ValueError):
    """Raised when an input byte sequence is not valid BOCU-1."""


class _BOCU1Decoder:
    """Stateful BOCU-1 decoder. One instance decodes one byte sequence.

    The object is intentionally cheap; callers should construct a fresh
    instance for every independent input. The decoder is *not* reusable
    after it raises — a failed decode leaves the state undefined.
    """

    __slots__ = ("_prev",)

    def __init__(self) -> None:
        # BOCU-1 starts from U+0020 (space) as the anchor reference point.
        self._prev = _MIDDLE

    def decode(self, data: bytes) -> str:
        out = []
        i = 0
        n = len(data)
        while i < n:
            b = data[i]
            if b < 0x21:
                # Control bytes 0x00..0x20 pass through directly as their
                # own code points; they do *not* change the differential
                # state. This matches UTN #191 §6.2.
                out.append(chr(b))
                i += 1
                continue
            if b == 0xFF:
                # Byte 0xFF is the BOCU-1 signature reset marker — it only
                # restores the anchor state and emits nothing. Per UTN #191
                # §5.3 we reset _prev to _MIDDLE after consuming the byte.
                self._prev = _MIDDLE
                i += 1
                continue
            if b <= 0x7E:
                cp = self._handle_single(b)
                i += 1
            elif b <= 0xFB:
                cp, i = self._handle_branch(data, i)
            else:
                # 0xFC..0xFE are unused in BOCU-1.
                raise BOCU1DecodeError(f"reserved lead byte 0x{b:02X} at offset {i}")
            if cp < 0 or cp > _MAX_CP:
                # The bytes formed a delta that lands outside Unicode's
                # range (e.g. a runaway branch offset). Reject it.
                raise BOCU1DecodeError(f"code point {cp} out of range at offset {i}")
            out.append(chr(cp))
            self._prev = cp
        return "".join(out)

    def _handle_single(self, lead: int) -> int:
        diff = lead - 0x52  # bias so lead 0x52 → diff 0
        cp = self._prev + diff
        return cp

    def _handle_branch(self, data: bytes, i: int) -> tuple[int, int]:
        lead = data[i]
        # Find the branch row whose lead range contains ``lead``.
        # Branch rows give the start lead and the trailer count; each
        # successive row increments the lead by 1 and decreases the
        # trailer count by 1 until the count stabilises, per §5.4.
        branch = _BRANCHES[lead - _BRANCH_MIN]
        b_start, b_trail, count, lo, _hi = branch
        # Number of leading branches (before the one that starts at b_start)
        # that we are "into" within the 0x80..b_start window.
        branch_index = lead - b_start
        # Consume exactly b_trail trailers; raise on truncation.
        if i + b_trail >= len(data):
            raise BOCU1DecodeError(
                f"truncated branch sequence at offset {i} "
                f"(lead 0x{lead:02X}, expected {b_trail} trailer(s))"
            )
        # Decode the big-endian base-93 trailer value.
        c = 0
        for k in range(1, b_trail + 1):
            tb = data[i + k]
            if tb < _TRAIL_MIN or tb > _TRAIL_MAX:
                raise BOCU1DecodeError(
                    f"invalid trailer byte 0x{tb:02X} at offset {i + k}"
                )
            c = c * _TRAIL_COUNT + (tb - _TRAIL_MIN)
        # Reconstruct the differential count for this lead.
        # Branch 0 (lead 0x80) has a special first 2-trailer entry covering
        # the out-of-range negative band; the second 2-trailer entry covers
        # the contiguous small negative band we actually care about.
        if lead == 0x80 and c < 1:
            # c == 0 selects the over-the-top band, which counts down
            # from MAX_CP. This is a direct code-point assignment, not a
            # differential delta, per UTN #191 §5.4.
            cp = _MAX_CP - c
            return cp, i + 1 + b_trail
        else:
            # Common case: ``count`` is the number of delta values this
            # branch covers, ``lo`` the lower bound on ``c``.
            diff = lo + c - count
        cp = self._prev + diff
        return cp, i + 1 + b_trail


def decode(data: bytes) -> str:
    """Decode a BOCU-1 encoded ``bytes`` object into a Python ``str``.

    Raises :class:`BOCU1DecodeError` if the input is malformed.
    Returns the decoded Unicode string.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("decode() expects bytes-like input")
    return _BOCU1Decoder().decode(bytes(data))
