# BOCU-1 Text Decoder

Decode byte sequences encoded with the **BOCU-1** compression scheme (Unicode Technical Note #191) into Python strings.

```python
from bocu_1_text_decoder import decode, BOCU1DecodeError

s = decode(b"\x52")            # -> " "
print(s)
try:
    decode(b"\xFC")
except BOCU1DecodeError as e:
    print("bad input:", e)
```

## Why this exists

BOCU-1 compresses Unicode text into ASCII-safe byte sequences for use in MIME-transported data and compact storage. This library provides a pure-Python decoder with zero third-party dependencies, making it suitable for environments where C extensions are unavailable. The trade-off is speed: a pure-Python implementation is slower than a native one, but it decodes correctly and predictably.

## Edge cases you will hit

- **Differential state.** Every character is encoded relative to the previously decoded one. The decoder manages this internally, so you must decode a full sequence in order; splitting and reassembling across calls is not supported by a single `decode()` invocation. Decode each chunk in a separate call only if you are willing to restart from the anchor.
- **Control bytes (0x00–0x20) pass through** as their literal code points and do **not** reset the differential state.
- **0xFF** is a signature/reset byte that restores the internal anchor to U+0020 and emits nothing.
- **Out-of-range deltas raise `BOCU1DecodeError`.** BOCU-1's branch encoding can express code points beyond U+10FFFF; this decoder rejects those as malformed rather than clamping.
- The implementation supports the full lead-byte range defined by UTN #191 §5.4 (single-byte leads 0x21–0x7E and multi-byte branch leads 0x80–0xFB) plus the 0xFF reset byte. Bytes 0xFC–0xFE are reserved and rejected.

## Public API

- `decode(data: bytes) -> str` — decode a BOCU-1 byte sequence. Accepts `bytes`, `bytearray`, or `memoryview`.
- `BOCU1DecodeError` — subclass of `ValueError`, raised on malformed input.
