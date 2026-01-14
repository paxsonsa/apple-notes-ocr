"""
Protobuf parser for Apple Notes ZDATA field.

Apple Notes stores content as gzip-compressed protobuf data.
This module provides parsing without requiring compiled .proto files.
"""

import gzip
from dataclasses import dataclass, field
from typing import Any, Optional
from io import BytesIO


@dataclass
class AttributeRun:
    """Represents formatting applied to a range of text."""
    length: int = 0
    paragraph_style: Optional[dict] = None
    font: Optional[dict] = None
    font_weight: Optional[int] = None
    underlined: bool = False
    strikethrough: bool = False
    superscript: Optional[int] = None
    link: Optional[str] = None
    color: Optional[dict] = None
    attachment_info: Optional[dict] = None


@dataclass
class ParsedNote:
    """Parsed note content."""
    text: str = ""
    attribute_runs: list[AttributeRun] = field(default_factory=list)
    version: int = 0


class ProtobufParser:
    """
    Manual protobuf parser for Apple Notes data.

    Handles the wire format without needing compiled schemas.
    Based on protobuf wire format specification.
    """

    WIRE_VARINT = 0
    WIRE_64BIT = 1
    WIRE_LENGTH_DELIMITED = 2
    WIRE_32BIT = 5

    def __init__(self, data: bytes):
        self.data = BytesIO(data)

    def read_varint(self) -> int:
        """Read a variable-length integer."""
        result = 0
        shift = 0
        while True:
            byte = self.data.read(1)
            if not byte:
                raise EOFError("Unexpected end of data")
            b = byte[0]
            result |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
        return result

    def read_signed_varint(self) -> int:
        """Read a signed (zigzag encoded) varint."""
        n = self.read_varint()
        return (n >> 1) ^ -(n & 1)

    def read_field_header(self) -> tuple[int, int]:
        """Read field tag and wire type. Returns (field_number, wire_type)."""
        tag = self.read_varint()
        field_number = tag >> 3
        wire_type = tag & 0x07
        return field_number, wire_type

    def read_length_delimited(self) -> bytes:
        """Read length-delimited data (strings, bytes, embedded messages)."""
        length = self.read_varint()
        return self.data.read(length)

    def read_fixed64(self) -> bytes:
        """Read 64-bit fixed value."""
        return self.data.read(8)

    def read_fixed32(self) -> bytes:
        """Read 32-bit fixed value."""
        return self.data.read(4)

    def skip_field(self, wire_type: int):
        """Skip a field based on its wire type."""
        if wire_type == self.WIRE_VARINT:
            self.read_varint()
        elif wire_type == self.WIRE_64BIT:
            self.data.read(8)
        elif wire_type == self.WIRE_LENGTH_DELIMITED:
            length = self.read_varint()
            self.data.read(length)
        elif wire_type == self.WIRE_32BIT:
            self.data.read(4)
        else:
            raise ValueError(f"Unknown wire type: {wire_type}")

    def parse_message(self) -> dict[int, list[Any]]:
        """
        Parse a protobuf message into a dict of field_number -> list of values.

        Values are kept as raw bytes for length-delimited fields,
        integers for varints, etc.
        """
        fields: dict[int, list[Any]] = {}

        while True:
            pos = self.data.tell()
            remaining = self.data.read(1)
            if not remaining:
                break
            self.data.seek(pos)

            try:
                field_num, wire_type = self.read_field_header()
            except EOFError:
                break

            if wire_type == self.WIRE_VARINT:
                value = self.read_varint()
            elif wire_type == self.WIRE_64BIT:
                value = self.read_fixed64()
            elif wire_type == self.WIRE_LENGTH_DELIMITED:
                value = self.read_length_delimited()
            elif wire_type == self.WIRE_32BIT:
                value = self.read_fixed32()
            else:
                raise ValueError(f"Unknown wire type: {wire_type}")

            if field_num not in fields:
                fields[field_num] = []
            fields[field_num].append(value)

        return fields

    def at_end(self) -> bool:
        """Check if we've reached the end of data."""
        pos = self.data.tell()
        byte = self.data.read(1)
        if byte:
            self.data.seek(pos)
            return False
        return True


class NoteParser:
    """
    Parser for Apple Notes content.

    Handles the NoteStoreProto structure:
    - Field 2: Document
      - Field 2: version
      - Field 3: Note
        - Field 2: note_text (string)
        - Field 5: attribute_run (repeated)
    """

    # NoteStoreProto schema (based on notestore.proto)
    # Field numbers from the proto file
    NOTESTORE_DOCUMENT = 2
    DOCUMENT_VERSION = 2
    DOCUMENT_NOTE = 3
    NOTE_TEXT = 2
    NOTE_ATTRIBUTE_RUN = 5

    # AttributeRun fields
    ATTR_LENGTH = 1
    ATTR_PARAGRAPH_STYLE = 2
    ATTR_FONT = 3
    ATTR_FONT_WEIGHT = 5
    ATTR_UNDERLINED = 6
    ATTR_STRIKETHROUGH = 7
    ATTR_SUPERSCRIPT = 8
    ATTR_LINK = 9
    ATTR_COLOR = 10
    ATTR_ATTACHMENT_INFO = 12

    # AttachmentInfo fields
    ATTACHMENT_UUID = 1
    ATTACHMENT_TYPE_UTI = 2

    def decompress(self, zdata: bytes) -> bytes:
        """Decompress gzip-compressed ZDATA."""
        if not zdata:
            raise ValueError("Empty ZDATA")

        # Check for gzip magic number
        if zdata[:2] == b'\x1f\x8b':
            return gzip.decompress(zdata)
        # Check for zlib header (some older notes)
        elif zdata[:2] == b'\x78\x9c':
            import zlib
            return zlib.decompress(zdata)
        else:
            # Assume raw protobuf
            return zdata

    def parse_attachment_info(self, data: bytes) -> dict:
        """Parse AttachmentInfo embedded message."""
        parser = ProtobufParser(data)
        fields = parser.parse_message()

        result = {}
        if self.ATTACHMENT_UUID in fields:
            result['uuid'] = fields[self.ATTACHMENT_UUID][0].decode('utf-8', errors='replace')
        if self.ATTACHMENT_TYPE_UTI in fields:
            result['type_uti'] = fields[self.ATTACHMENT_TYPE_UTI][0].decode('utf-8', errors='replace')

        return result

    def parse_attribute_run(self, data: bytes) -> AttributeRun:
        """Parse a single AttributeRun message."""
        parser = ProtobufParser(data)
        fields = parser.parse_message()

        attr = AttributeRun()

        if self.ATTR_LENGTH in fields:
            attr.length = fields[self.ATTR_LENGTH][0]
        if self.ATTR_FONT_WEIGHT in fields:
            attr.font_weight = fields[self.ATTR_FONT_WEIGHT][0]
        if self.ATTR_UNDERLINED in fields:
            attr.underlined = bool(fields[self.ATTR_UNDERLINED][0])
        if self.ATTR_STRIKETHROUGH in fields:
            attr.strikethrough = bool(fields[self.ATTR_STRIKETHROUGH][0])
        if self.ATTR_SUPERSCRIPT in fields:
            attr.superscript = fields[self.ATTR_SUPERSCRIPT][0]
        if self.ATTR_LINK in fields:
            attr.link = fields[self.ATTR_LINK][0].decode('utf-8', errors='replace')
        if self.ATTR_ATTACHMENT_INFO in fields:
            attr.attachment_info = self.parse_attachment_info(fields[self.ATTR_ATTACHMENT_INFO][0])

        return attr

    def parse(self, zdata: bytes) -> ParsedNote:
        """
        Parse ZDATA blob into a ParsedNote.

        Args:
            zdata: The compressed protobuf data from ZICNOTEDATA.ZDATA

        Returns:
            ParsedNote with text content and formatting/attachment info
        """
        # Decompress
        try:
            data = self.decompress(zdata)
        except Exception as e:
            raise ValueError(f"Failed to decompress ZDATA: {e}")

        # Parse NoteStoreProto
        parser = ProtobufParser(data)
        notestore = parser.parse_message()

        result = ParsedNote()

        # Get Document (field 2)
        if self.NOTESTORE_DOCUMENT not in notestore:
            raise ValueError("Missing Document in NoteStoreProto")

        doc_parser = ProtobufParser(notestore[self.NOTESTORE_DOCUMENT][0])
        document = doc_parser.parse_message()

        # Get version
        if self.DOCUMENT_VERSION in document:
            result.version = document[self.DOCUMENT_VERSION][0]

        # Get Note (field 3)
        if self.DOCUMENT_NOTE not in document:
            raise ValueError("Missing Note in Document")

        note_parser = ProtobufParser(document[self.DOCUMENT_NOTE][0])
        note = note_parser.parse_message()

        # Get text content (field 2)
        if self.NOTE_TEXT in note:
            result.text = note[self.NOTE_TEXT][0].decode('utf-8', errors='replace')

        # Get attribute runs (field 5, repeated)
        if self.NOTE_ATTRIBUTE_RUN in note:
            for attr_data in note[self.NOTE_ATTRIBUTE_RUN]:
                attr = self.parse_attribute_run(attr_data)
                result.attribute_runs.append(attr)

        return result

    def extract_attachments(self, parsed: ParsedNote) -> list[dict]:
        """
        Extract attachment info from parsed note.

        Returns list of dicts with 'uuid', 'type_uti', and 'position' (char offset).
        """
        attachments = []
        position = 0

        for attr in parsed.attribute_runs:
            if attr.attachment_info:
                attachments.append({
                    'uuid': attr.attachment_info.get('uuid'),
                    'type_uti': attr.attachment_info.get('type_uti'),
                    'position': position
                })
            position += attr.length

        return attachments

    def get_plain_text(self, parsed: ParsedNote) -> str:
        """
        Get plain text with attachment placeholders replaced.

        The Object Replacement Character (U+FFFC) marks embedded objects.
        """
        # Replace object replacement characters with placeholder
        text = parsed.text.replace('\ufffc', '[attachment]')
        return text

    def get_text_with_attachment_markers(self, parsed: ParsedNote) -> str:
        """
        Get text with attachment placeholders replaced by identifiable markers.

        Each U+FFFC is replaced with [ATTACHMENT:uuid:type] so you know
        which attachment goes where.
        """
        attachments = self.extract_attachments(parsed)
        att_index = 0
        result = []

        for char in parsed.text:
            if char == '\ufffc':
                if att_index < len(attachments):
                    att = attachments[att_index]
                    uuid = att.get('uuid', 'unknown')
                    type_uti = att.get('type_uti', 'unknown')
                    # Short type name for readability
                    short_type = type_uti.split('.')[-1] if type_uti else 'unknown'
                    result.append(f'[DRAWING:{uuid}]' if 'drawing' in type_uti or 'paper' in type_uti else f'[ATTACHMENT:{uuid}:{short_type}]')
                    att_index += 1
                else:
                    result.append('[attachment]')
            else:
                result.append(char)

        return ''.join(result)
