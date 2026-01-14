#!/usr/bin/env python3
"""
Apple Notes OCR - MCP Server

A Model Context Protocol (MCP) server for accessing Apple Notes.
Provides read-only access to notes, drawings, and search functionality.
Designed for OCR of handwritten notes via Claude's vision capabilities.

Usage with uvx:
    uvx --from git+https://github.com/USER/apple-notes-ocr apple-notes-mcp

Add to Claude Code MCP settings (~/.claude/settings.json):
    {
        "mcpServers": {
            "apple-notes": {
                "command": "uvx",
                "args": ["--from", "git+https://github.com/USER/apple-notes-ocr", "apple-notes-mcp"]
            }
        }
    }

Environment variables for filtering:
    APPLE_NOTES_BLOCKED_FOLDERS: Comma-separated folder names to block
    APPLE_NOTES_ALLOWED_FOLDERS: Comma-separated folder names to allow (whitelist mode)
    APPLE_NOTES_BLOCKED_TITLES: Comma-separated title patterns (supports wildcards)
"""

import json
import base64
from typing import Any
from datetime import datetime

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
    ImageContent,
)

from apple_notes_ocr import NotesDatabase, NoteParser, AttachmentExtractor
from apple_notes_ocr.attachments import AttachmentType
from apple_notes_ocr.config import get_filter


# Initialize server
server = Server("apple-notes-ocr")

# Global instances (lazy initialized)
_db: NotesDatabase | None = None
_parser: NoteParser | None = None
_extractor: AttachmentExtractor | None = None


def get_db() -> NotesDatabase:
    """Get or create the NotesDatabase instance."""
    global _db
    if _db is None:
        _db = NotesDatabase()
    return _db


def get_parser() -> NoteParser:
    """Get or create the NoteParser instance."""
    global _parser
    if _parser is None:
        _parser = NoteParser()
    return _parser


def get_extractor() -> AttachmentExtractor:
    """Get or create the AttachmentExtractor instance."""
    global _extractor
    if _extractor is None:
        _extractor = AttachmentExtractor()
    return _extractor


def format_datetime(dt: datetime | None) -> str:
    """Format a datetime for JSON output."""
    if dt is None:
        return "Unknown"
    return dt.strftime("%Y-%m-%d %H:%M:%S")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="list_notes",
            description="List all Apple Notes. Returns title, folder, modification date, and whether the note has drawings.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of notes to return (default: 100)",
                        "default": 100
                    }
                }
            }
        ),
        Tool(
            name="search_notes",
            description="Search Apple Notes by title. Returns matching notes with content previews.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query to match against note titles"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results (default: 20)",
                        "default": 20
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_note",
            description="Get the full content of an Apple Note by its ID. Returns text with [DRAWING:uuid] markers showing where drawings appear. Set include_drawings=true to embed all drawing images inline for OCR (may hit size limits for notes with many drawings).",
            inputSchema={
                "type": "object",
                "properties": {
                    "note_id": {
                        "type": "integer",
                        "description": "The note ID (pk) from list_notes or search_notes"
                    },
                    "include_drawings": {
                        "type": "boolean",
                        "description": "If true, embeds all drawings as images in the response for OCR. Default: false. For notes with many drawings, use list_attachments + get_drawing instead.",
                        "default": False
                    }
                },
                "required": ["note_id"]
            }
        ),
        Tool(
            name="list_attachments",
            description="List all attachments (drawings, images) for a note WITHOUT fetching image data. Use this to discover attachments before fetching them individually with get_drawing. Recommended for notes with many drawings to avoid size limits.",
            inputSchema={
                "type": "object",
                "properties": {
                    "note_id": {
                        "type": "integer",
                        "description": "The note ID (pk) from list_notes or search_notes"
                    }
                },
                "required": ["note_id"]
            }
        ),
        Tool(
            name="get_drawing",
            description="Get a drawing/sketch from an Apple Note as a PNG image. Use the attachment UUID from get_note or list_attachments.",
            inputSchema={
                "type": "object",
                "properties": {
                    "attachment_id": {
                        "type": "string",
                        "description": "The UUID of the drawing attachment"
                    }
                },
                "required": ["attachment_id"]
            }
        ),
        Tool(
            name="list_tags",
            description="List all hashtags used across Apple Notes, with note counts. Use this to discover available tags for filtering.",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="get_notes_by_tag",
            description="Get all notes that have a specific hashtag.",
            inputSchema={
                "type": "object",
                "properties": {
                    "tag": {
                        "type": "string",
                        "description": "The tag to filter by (with or without #)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of notes to return (default: 50)",
                        "default": 50
                    }
                },
                "required": ["tag"]
            }
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent | ImageContent]:
    """Handle tool calls."""
    try:
        if name == "list_notes":
            return await handle_list_notes(arguments)
        elif name == "search_notes":
            return await handle_search_notes(arguments)
        elif name == "get_note":
            return await handle_get_note(arguments)
        elif name == "list_attachments":
            return await handle_list_attachments(arguments)
        elif name == "get_drawing":
            return await handle_get_drawing(arguments)
        elif name == "list_tags":
            return await handle_list_tags(arguments)
        elif name == "get_notes_by_tag":
            return await handle_get_notes_by_tag(arguments)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except FileNotFoundError as e:
        return [TextContent(type="text", text=f"Error: {e}")]
    except PermissionError as e:
        return [TextContent(
            type="text",
            text="Permission Error: Cannot access Apple Notes database.\n\n"
                 "Please grant Full Disk Access to your terminal:\n"
                 "System Settings → Privacy & Security → Full Disk Access\n\n"
                 f"Details: {e}"
        )]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]


async def handle_list_notes(arguments: dict[str, Any]) -> list[TextContent]:
    """List all notes with basic metadata, respecting configured filters."""
    db = get_db()
    parser = get_parser()
    notes_filter = get_filter()
    limit = arguments.get("limit", 100)

    notes = []
    filtered_count = 0

    for note in db.get_notes():
        # Apply filtering
        if not notes_filter.should_include(note.folder_name, note.title):
            filtered_count += 1
            continue

        if len(notes) >= limit:
            break

        # Check if note has drawings
        has_drawings = False
        if note.zdata and not note.is_encrypted:
            try:
                parsed = parser.parse(note.zdata)
                attachments = parser.extract_attachments(parsed)
                has_drawings = any(
                    a.get('type_uti', '') in AttachmentType.DRAWING_TYPES
                    for a in attachments
                )
            except Exception:
                pass

        # Get note's tags
        note_tags = db.get_note_tags(note.pk) if not note.is_encrypted else []

        notes.append({
            "pk": note.pk,
            "title": note.title,
            "folder": note.folder_name,
            "modified": format_datetime(note.modified),
            "is_encrypted": note.is_encrypted,
            "has_drawings": has_drawings,
            "tags": note_tags
        })

    result = {
        "count": len(notes),
        "notes": notes
    }

    # Include filter info if filtering is active
    if notes_filter.is_configured():
        result["filtered_count"] = filtered_count

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def handle_search_notes(arguments: dict[str, Any]) -> list[TextContent]:
    """Search notes by title, respecting configured filters."""
    db = get_db()
    parser = get_parser()
    notes_filter = get_filter()

    query = arguments.get("query", "")
    limit = arguments.get("limit", 20)

    if not query:
        return [TextContent(type="text", text="Error: query is required")]

    results = []
    filtered_count = 0

    for note in db.search_notes(query):
        # Apply filtering
        if not notes_filter.should_include(note.folder_name, note.title):
            filtered_count += 1
            continue

        if len(results) >= limit:
            break

        # Get note's tags
        note_tags = db.get_note_tags(note.pk) if not note.is_encrypted else []

        item = {
            "pk": note.pk,
            "title": note.title,
            "folder": note.folder_name,
            "modified": format_datetime(note.modified),
            "is_encrypted": note.is_encrypted,
            "tags": note_tags,
            "preview": None,
            "has_drawings": False
        }

        if note.zdata and not note.is_encrypted:
            try:
                parsed = parser.parse(note.zdata)
                text = parser.get_plain_text(parsed)
                item["preview"] = text[:300] + "..." if len(text) > 300 else text

                attachments = parser.extract_attachments(parsed)
                item["has_drawings"] = any(
                    a.get('type_uti', '') in AttachmentType.DRAWING_TYPES
                    for a in attachments
                )
            except Exception:
                pass

        results.append(item)

    result = {
        "query": query,
        "count": len(results),
        "results": results
    }

    if notes_filter.is_configured():
        result["filtered_count"] = filtered_count

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def handle_get_note(arguments: dict[str, Any]) -> list[TextContent | ImageContent]:
    """Get full note content, optionally with embedded drawings for OCR."""
    db = get_db()
    parser = get_parser()
    extractor = get_extractor()
    notes_filter = get_filter()

    note_id = arguments.get("note_id")
    include_drawings = arguments.get("include_drawings", False)

    if note_id is None:
        return [TextContent(type="text", text="Error: note_id is required")]

    note = db.get_note_by_pk(note_id)
    if not note:
        return [TextContent(type="text", text=f"Note with ID {note_id} not found")]

    # Check filter
    if not notes_filter.should_include(note.folder_name, note.title):
        return [TextContent(type="text", text=f"Note '{note.title}' is blocked by filter configuration")]

    if note.is_encrypted:
        return [TextContent(
            type="text",
            text=f"Note '{note.title}' is encrypted and cannot be read"
        )]

    # Get note's tags
    note_tags = db.get_note_tags(note.pk)

    result = {
        "pk": note.pk,
        "title": note.title,
        "folder": note.folder_name,
        "created": format_datetime(note.created),
        "modified": format_datetime(note.modified),
        "tags": note_tags,
        "content": None,
        "drawings": []
    }

    drawings_to_embed = []

    if note.zdata:
        try:
            parsed = parser.parse(note.zdata)
            # Use the method that shows [DRAWING:uuid] markers
            result["content"] = parser.get_text_with_attachment_markers(parsed)

            # Extract drawing attachments
            for att in parser.extract_attachments(parsed):
                if att.get('type_uti', '') in AttachmentType.DRAWING_TYPES:
                    drawing_info = {
                        "uuid": att['uuid'],
                        "type": att['type_uti'],
                        "position": att['position']
                    }
                    result["drawings"].append(drawing_info)

                    # Collect drawings to embed if requested
                    if include_drawings:
                        drawings_to_embed.append(att['uuid'])

        except Exception as e:
            result["parse_error"] = str(e)

    # Build response
    response: list[TextContent | ImageContent] = [
        TextContent(type="text", text=json.dumps(result, indent=2))
    ]

    # Embed drawings if requested
    if include_drawings and drawings_to_embed:
        for uuid in drawings_to_embed:
            drawing = extractor.get_drawing(uuid)
            if drawing.data and not drawing.error:
                b64_data = base64.b64encode(drawing.data).decode('ascii')
                # Add a text label before each image
                response.append(TextContent(type="text", text=f"\n--- Drawing: {uuid} ---"))
                response.append(ImageContent(
                    type="image",
                    data=b64_data,
                    mimeType="image/png"
                ))

    return response


async def handle_list_attachments(arguments: dict[str, Any]) -> list[TextContent]:
    """List all attachments for a note without fetching image data."""
    db = get_db()
    parser = get_parser()
    notes_filter = get_filter()

    note_id = arguments.get("note_id")

    if note_id is None:
        return [TextContent(type="text", text="Error: note_id is required")]

    note = db.get_note_by_pk(note_id)
    if not note:
        return [TextContent(type="text", text=f"Note with ID {note_id} not found")]

    # Check filter
    if not notes_filter.should_include(note.folder_name, note.title):
        return [TextContent(type="text", text=f"Note '{note.title}' is blocked by filter configuration")]

    if note.is_encrypted:
        return [TextContent(
            type="text",
            text=f"Note '{note.title}' is encrypted and cannot be read"
        )]

    result = {
        "pk": note.pk,
        "title": note.title,
        "attachments": []
    }

    if note.zdata:
        try:
            parsed = parser.parse(note.zdata)
            for att in parser.extract_attachments(parsed):
                type_uti = att.get('type_uti', '')
                is_drawing = type_uti in AttachmentType.DRAWING_TYPES
                result["attachments"].append({
                    "uuid": att['uuid'],
                    "type": type_uti,
                    "position": att['position'],
                    "is_drawing": is_drawing,
                    "can_fetch": is_drawing  # Currently only drawings are fetchable
                })
        except Exception as e:
            result["parse_error"] = str(e)

    result["count"] = len(result["attachments"])
    result["drawing_count"] = sum(1 for a in result["attachments"] if a.get("is_drawing"))

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def handle_get_drawing(arguments: dict[str, Any]) -> list[TextContent | ImageContent]:
    """Get a drawing as PNG image."""
    extractor = get_extractor()

    attachment_id = arguments.get("attachment_id")
    if not attachment_id:
        return [TextContent(type="text", text="Error: attachment_id is required")]

    drawing = extractor.get_drawing(attachment_id)

    if drawing.error:
        return [TextContent(type="text", text=f"Error: {drawing.error}")]

    if drawing.data:
        b64_data = base64.b64encode(drawing.data).decode('ascii')
        return [
            ImageContent(
                type="image",
                data=b64_data,
                mimeType="image/png"
            )
        ]

    return [TextContent(type="text", text="Drawing data not available")]


async def handle_list_tags(arguments: dict[str, Any]) -> list[TextContent]:
    """List all tags with their note counts."""
    db = get_db()

    tags = db.get_tags_with_counts()

    result = {
        "count": len(tags),
        "tags": tags
    }

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def handle_get_notes_by_tag(arguments: dict[str, Any]) -> list[TextContent]:
    """Get notes filtered by a specific tag."""
    db = get_db()
    parser = get_parser()
    notes_filter = get_filter()

    tag = arguments.get("tag", "")
    limit = arguments.get("limit", 50)

    if not tag:
        return [TextContent(type="text", text="Error: tag is required")]

    results = []
    filtered_count = 0

    for note in db.get_notes_by_tag(tag):
        # Apply configured filters
        if not notes_filter.should_include(note.folder_name, note.title):
            filtered_count += 1
            continue

        if len(results) >= limit:
            break

        # Get note's tags
        note_tags = db.get_note_tags(note.pk)

        item = {
            "pk": note.pk,
            "title": note.title,
            "folder": note.folder_name,
            "modified": format_datetime(note.modified),
            "is_encrypted": note.is_encrypted,
            "tags": note_tags,
            "preview": None,
            "has_drawings": False
        }

        if note.zdata and not note.is_encrypted:
            try:
                parsed = parser.parse(note.zdata)
                text = parser.get_plain_text(parsed)
                item["preview"] = text[:300] + "..." if len(text) > 300 else text

                attachments = parser.extract_attachments(parsed)
                item["has_drawings"] = any(
                    a.get('type_uti', '') in AttachmentType.DRAWING_TYPES
                    for a in attachments
                )
            except Exception:
                pass

        results.append(item)

    # Normalize tag for display
    tag_display = tag.lstrip('#').upper()

    result = {
        "tag": tag_display,
        "count": len(results),
        "results": results
    }

    if notes_filter.is_configured():
        result["filtered_count"] = filtered_count

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _main():
    """Run the MCP server (async implementation)."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )


def main():
    """Entry point for uvx apple-notes-mcp."""
    import asyncio
    asyncio.run(_main())


if __name__ == "__main__":
    main()
