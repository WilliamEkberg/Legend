"""
SCIP protobuf loading with per-document error recovery.
Returns None on failure instead of raising.
"""

import logging
import os

from google.protobuf.message import DecodeError

from component_discovery.scip_pb2 import Index, Document

logger = logging.getLogger(__name__)

MAX_SCIP_SIZE = 512 * 1024 * 1024  # 512 MB

# Protobuf wire types
_VARINT = 0
_FIXED64 = 1
_LENGTH_DELIMITED = 2
_FIXED32 = 5


def load_scip_index(path: str) -> Index | None:
    """Load a SCIP protobuf file. Returns None on failure."""
    if not os.path.exists(path):
        logger.warning("SCIP file not found: %s", path)
        return None

    size = os.path.getsize(path)
    if size == 0:
        logger.warning("SCIP file is empty: %s", path)
        return None
    if size > MAX_SCIP_SIZE:
        logger.warning("SCIP file too large (%d MB > %d MB limit): %s",
                        size // (1024 * 1024), MAX_SCIP_SIZE // (1024 * 1024), path)
        return None

    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        logger.warning("Failed to read SCIP file %s: %s", path, e)
        return None

    index = Index()
    try:
        index.ParseFromString(raw)
        return index
    except DecodeError:
        pass
    except Exception as e:
        logger.warning("Unexpected error parsing %s: %s", path, e)

    logger.info("Attempting per-document recovery for %s (%d MB)",
                path, size // (1024 * 1024))
    try:
        return _parse_with_recovery(raw, path)
    except Exception as e:
        logger.warning("Failed to recover %s: %s", path, e)
        return None


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Read a protobuf varint, return (value, new_pos)."""
    result = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        result |= (b & 0x7F) << shift
        pos += 1
        if (b & 0x80) == 0:
            return result, pos
        shift += 7
    raise DecodeError("Truncated varint")


def _parse_with_recovery(raw: bytes, path: str) -> Index | None:
    """Walk Index wire format, parse each Document individually, skip corrupted ones."""
    index = Index()
    length = len(raw)
    pos = 0
    total_docs = 0
    skipped_docs = 0

    while pos < length:
        try:
            tag, new_pos = _read_varint(raw, pos)
        except DecodeError:
            break

        field_number = tag >> 3
        wire_type = tag & 0x07

        if wire_type == _VARINT:
            _, pos = _read_varint(raw, new_pos)

        elif wire_type == _FIXED64:
            pos = new_pos + 8

        elif wire_type == _FIXED32:
            pos = new_pos + 4

        elif wire_type == _LENGTH_DELIMITED:
            try:
                field_len, data_start = _read_varint(raw, new_pos)
            except DecodeError:
                break

            field_end = data_start + field_len
            if field_end > length:
                break

            field_bytes = raw[data_start:field_end]

            if field_number == 2:
                total_docs += 1
                doc = _parse_document_safe(field_bytes)
                if doc is not None:
                    index.documents.append(doc)
                else:
                    skipped_docs += 1
            elif field_number == 1:
                try:
                    index.metadata.ParseFromString(field_bytes)
                except Exception:
                    pass
            elif field_number == 3:
                try:
                    sym = index.external_symbols.add()
                    sym.ParseFromString(field_bytes)
                except Exception:
                    del index.external_symbols[-1]

            pos = field_end
        else:
            break

    if total_docs == 0:
        logger.warning("No documents found in %s", path)
        return None

    if skipped_docs:
        logger.info("Recovered %s: %d/%d documents (%d skipped due to corruption)",
                     path, total_docs - skipped_docs, total_docs, skipped_docs)
    else:
        logger.info("Successfully parsed %s: %d documents", path, total_docs)

    return index


def _parse_document_safe(data: bytes) -> Document | None:
    """Parse a single Document; sanitize string fields on UTF-8 errors."""
    doc = Document()
    try:
        doc.ParseFromString(data)
        return doc
    except DecodeError:
        pass
    except Exception:
        pass

    try:
        sanitized = _sanitize_message_strings(data, _DOC_STRING_FIELDS)
        doc = Document()
        doc.ParseFromString(sanitized)
        return doc
    except Exception:
        return None


_DOC_STRING_FIELDS = {
    # Document: field -> True if string, False if submessage
    1: True,   # relative_path
    2: False,  # occurrences (submessage)
    3: False,  # symbols (submessage)
    4: True,   # language
    5: True,   # text
    6: True,   # position_encoding
}

_SYM_INFO_STRING_FIELDS = {
    1: True,   # symbol
    2: True,   # documentation
    3: False,  # relationships (submessage)
    4: None,   # kind (varint, won't appear as length-delimited)
    5: True,   # display_name
    6: False,  # signature_documentation (submessage)
    7: True,   # enclosing_symbol
}

_OCCURRENCE_STRING_FIELDS = {
    1: None,   # range (packed varint)
    2: True,   # symbol
    3: None,   # symbol_roles (varint)
    4: False,  # diagnostics (submessage)
    5: True,   # override_documentation
    6: None,   # syntax_kind (varint)
    7: False,  # diagnostics (submessage)
    8: True,   # enclosing_range
}


def _sanitize_message_strings(data: bytes, string_fields: dict) -> bytes:
    """Walk a protobuf message, sanitize string fields, recurse into submessages."""
    result = bytearray(data)
    pos = 0
    length = len(data)

    while pos < length:
        try:
            tag, new_pos = _read_varint(data, pos)
        except DecodeError:
            break

        field_number = tag >> 3
        wire_type = tag & 0x07

        if wire_type == _VARINT:
            _, pos = _read_varint(data, new_pos)
        elif wire_type == _FIXED64:
            pos = new_pos + 8
        elif wire_type == _FIXED32:
            pos = new_pos + 4
        elif wire_type == _LENGTH_DELIMITED:
            try:
                field_len, data_start = _read_varint(data, new_pos)
            except DecodeError:
                break

            field_end = data_start + field_len
            if field_end > length:
                break

            is_string = string_fields.get(field_number)
            if is_string is True:
                _sanitize_utf8_inplace(result, data_start, field_end)
            elif is_string is False:
                # Submessage — determine which schema to use
                sub_schema = _get_sub_schema(string_fields, field_number)
                if sub_schema is not None:
                    sub_data = bytes(result[data_start:field_end])
                    sanitized = _sanitize_message_strings(sub_data, sub_schema)
                    result[data_start:field_end] = sanitized

            pos = field_end
        else:
            break

    return bytes(result)


def _get_sub_schema(parent_fields: dict, field_number: int) -> dict | None:
    """Map parent field number to child message's string field schema."""
    # Document children
    if parent_fields is _DOC_STRING_FIELDS:
        if field_number == 3:
            return _OCCURRENCE_STRING_FIELDS
        if field_number == 4:
            return _SYM_INFO_STRING_FIELDS
    # SymbolInformation children
    elif parent_fields is _SYM_INFO_STRING_FIELDS:
        if field_number == 3:
            return _RELATIONSHIP_STRING_FIELDS
        # field 6 (Diagnostic) — just sanitize all strings
        if field_number == 6:
            return _DIAGNOSTIC_STRING_FIELDS
    # Occurrence children
    elif parent_fields is _OCCURRENCE_STRING_FIELDS:
        if field_number in (4, 7):
            return _DIAGNOSTIC_STRING_FIELDS
    return None


_RELATIONSHIP_STRING_FIELDS = {
    1: True,   # symbol
    2: None,   # is_reference
    3: None,   # is_implementation
    4: None,   # is_type_definition
    5: None,   # is_definition
}

_DIAGNOSTIC_STRING_FIELDS = {
    1: None,   # severity
    2: True,   # code
    3: True,   # message
    4: True,   # source
    5: False,  # tags (submessage, no strings)
}


def _sanitize_utf8_inplace(data: bytearray, start: int, end: int) -> None:
    """Replace invalid UTF-8 bytes with '?' (0x3F) in a bytearray slice."""
    pos = start
    while pos < end:
        b = data[pos]

        if b <= 0x7F:
            pos += 1
            continue

        if (b & 0xE0) == 0xC0:
            expected = 2
            min_code = 0x80
        elif (b & 0xF0) == 0xE0:
            expected = 3
            min_code = 0x800
        elif (b & 0xF8) == 0xF0:
            expected = 4
            min_code = 0x10000
        else:
            data[pos] = 0x3F
            pos += 1
            continue

        if pos + expected > end:
            for i in range(pos, end):
                data[i] = 0x3F
            break

        valid = True
        codepoint = b & (0x7F >> expected)
        for i in range(1, expected):
            cb = data[pos + i]
            if (cb & 0xC0) != 0x80:
                valid = False
                break
            codepoint = (codepoint << 6) | (cb & 0x3F)

        if valid:
            if codepoint < min_code:
                valid = False
            elif 0xD800 <= codepoint <= 0xDFFF:
                valid = False
            elif codepoint > 0x10FFFF:
                valid = False

        if valid:
            pos += expected
        else:
            for i in range(expected):
                if pos + i < end:
                    data[pos + i] = 0x3F
            pos += expected
