#!/usr/bin/env python3
"""
Apple Notes Extraction Tool

Extracts notes content and drawings from Apple Notes database.

Usage:
    python notes.py                     # List all notes
    python notes.py --format json       # Output as JSON
    python notes.py --note-id 123       # Get specific note
    python notes.py --export ./output   # Export all notes with attachments
    python notes.py --search "keyword"  # Search notes
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

from apple_notes_ocr import NotesDatabase, NoteParser, AttachmentExtractor
from apple_notes_ocr.attachments import AttachmentType


def format_datetime(dt: Optional[datetime]) -> str:
    """Format datetime for display."""
    if dt is None:
        return "Unknown"
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def list_notes(db: NotesDatabase, args: argparse.Namespace) -> None:
    """List all notes."""
    notes = list(db.get_notes())

    if args.format == 'json':
        output = []
        for note in notes:
            output.append({
                'pk': note.pk,
                'identifier': note.identifier,
                'title': note.title,
                'folder': note.folder_name,
                'account': note.account_name,
                'created': format_datetime(note.created),
                'modified': format_datetime(note.modified),
                'is_encrypted': note.is_encrypted
            })
        print(json.dumps(output, indent=2))
    else:
        print(f"Found {len(notes)} notes:\n")
        for note in notes:
            encrypted = " [ENCRYPTED]" if note.is_encrypted else ""
            folder = f" ({note.folder_name})" if note.folder_name else ""
            print(f"[{note.pk}] {note.title}{folder}{encrypted}")
            print(f"    Modified: {format_datetime(note.modified)}")
            print()


def get_note(db: NotesDatabase, parser: NoteParser, args: argparse.Namespace) -> None:
    """Get and display a specific note."""
    note = db.get_note_by_pk(args.note_id)

    if not note:
        print(f"Note with ID {args.note_id} not found", file=sys.stderr)
        sys.exit(1)

    if note.is_encrypted:
        print(f"Note '{note.title}' is encrypted and cannot be read", file=sys.stderr)
        sys.exit(1)

    if not note.zdata:
        print(f"Note '{note.title}' has no content data", file=sys.stderr)
        sys.exit(1)

    try:
        parsed = parser.parse(note.zdata)
        attachments = parser.extract_attachments(parsed)
    except Exception as e:
        print(f"Failed to parse note: {e}", file=sys.stderr)
        sys.exit(1)

    if args.format == 'json':
        output = {
            'pk': note.pk,
            'identifier': note.identifier,
            'title': note.title,
            'folder': note.folder_name,
            'account': note.account_name,
            'created': format_datetime(note.created),
            'modified': format_datetime(note.modified),
            'text': parsed.text,
            'plain_text': parser.get_plain_text(parsed),
            'attachments': attachments
        }
        print(json.dumps(output, indent=2))
    elif args.format == 'markdown':
        print(f"# {note.title}\n")
        print(f"*Modified: {format_datetime(note.modified)}*\n")
        if note.folder_name:
            print(f"*Folder: {note.folder_name}*\n")
        print("---\n")
        print(parser.get_plain_text(parsed))
        if attachments:
            print("\n## Attachments\n")
            for att in attachments:
                print(f"- [{att['type_uti']}] {att['uuid']}")
    else:
        print(f"=== {note.title} ===")
        print(f"Modified: {format_datetime(note.modified)}")
        if note.folder_name:
            print(f"Folder: {note.folder_name}")
        print()
        print(parser.get_plain_text(parsed))
        if attachments:
            print("\nAttachments:")
            for att in attachments:
                print(f"  - {att['type_uti']}: {att['uuid']}")


def search_notes(db: NotesDatabase, parser: NoteParser, args: argparse.Namespace) -> None:
    """Search notes by title."""
    notes = list(db.search_notes(args.search))

    if args.format == 'json':
        output = []
        for note in notes:
            item = {
                'pk': note.pk,
                'identifier': note.identifier,
                'title': note.title,
                'folder': note.folder_name,
                'modified': format_datetime(note.modified),
                'is_encrypted': note.is_encrypted
            }

            # Try to get preview of content
            if note.zdata and not note.is_encrypted:
                try:
                    parsed = parser.parse(note.zdata)
                    text = parser.get_plain_text(parsed)
                    item['preview'] = text[:200] + '...' if len(text) > 200 else text
                except Exception:
                    item['preview'] = None

            output.append(item)
        print(json.dumps(output, indent=2))
    else:
        print(f"Found {len(notes)} notes matching '{args.search}':\n")
        for note in notes:
            encrypted = " [ENCRYPTED]" if note.is_encrypted else ""
            print(f"[{note.pk}] {note.title}{encrypted}")
            print(f"    Modified: {format_datetime(note.modified)}")

            # Show preview
            if note.zdata and not note.is_encrypted:
                try:
                    parsed = parser.parse(note.zdata)
                    text = parser.get_plain_text(parsed)
                    preview = text[:100].replace('\n', ' ')
                    if len(text) > 100:
                        preview += '...'
                    print(f"    Preview: {preview}")
                except Exception:
                    pass
            print()


def export_notes(
    db: NotesDatabase,
    parser: NoteParser,
    extractor: AttachmentExtractor,
    args: argparse.Namespace
) -> None:
    """Export notes to a directory. Filters by --note-id or --search if provided."""
    output_dir = Path(args.export)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Filter notes based on arguments
    if args.note_id:
        note = db.get_note_by_pk(args.note_id)
        if not note:
            print(f"Note with ID {args.note_id} not found", file=sys.stderr)
            sys.exit(1)
        notes = [note]
    elif args.search:
        notes = list(db.search_notes(args.search))
    else:
        notes = list(db.get_notes())

    exported = 0
    skipped = 0

    print(f"Exporting {len(notes)} note(s) to {output_dir}...")

    for note in notes:
        if note.is_encrypted:
            print(f"  Skipping encrypted note: {note.title}")
            skipped += 1
            continue

        if not note.zdata:
            print(f"  Skipping note with no content: {note.title}")
            skipped += 1
            continue

        try:
            parsed = parser.parse(note.zdata)
            attachments = parser.extract_attachments(parsed)
        except Exception as e:
            print(f"  Failed to parse '{note.title}': {e}")
            skipped += 1
            continue

        # Create safe filename
        safe_title = "".join(c for c in note.title if c.isalnum() or c in ' -_').strip()
        safe_title = safe_title[:50] or f"note_{note.pk}"

        # Create note directory if it has attachments
        if attachments and args.include_drawings:
            note_dir = output_dir / safe_title
            note_dir.mkdir(exist_ok=True)
            attachments_dir = note_dir / "attachments"
        else:
            note_dir = output_dir
            attachments_dir = None

        # Export based on format
        if args.format == 'json':
            output_file = note_dir / f"{safe_title}.json"
            data = {
                'pk': note.pk,
                'identifier': note.identifier,
                'title': note.title,
                'folder': note.folder_name,
                'account': note.account_name,
                'created': format_datetime(note.created),
                'modified': format_datetime(note.modified),
                'text': parsed.text,
                'plain_text': parser.get_plain_text(parsed),
                'attachments': []
            }

            # Export attachments
            if attachments and args.include_drawings:
                for att in attachments:
                    att_data = {'uuid': att['uuid'], 'type_uti': att['type_uti']}
                    if att['type_uti'] in AttachmentType.DRAWING_TYPES:
                        extracted = extractor.extract_to_directory(
                            att['uuid'],
                            att['type_uti'],
                            attachments_dir,
                            account_id=note.account_identifier
                        )
                        if not extracted.error:
                            att_data['file'] = str(extracted.path.relative_to(note_dir))
                        else:
                            att_data['error'] = extracted.error
                    data['attachments'].append(att_data)

            output_file.write_text(json.dumps(data, indent=2))

        elif args.format == 'markdown':
            output_file = note_dir / f"{safe_title}.md"
            content = f"# {note.title}\n\n"
            content += f"*Modified: {format_datetime(note.modified)}*\n\n"
            if note.folder_name:
                content += f"*Folder: {note.folder_name}*\n\n"
            content += "---\n\n"
            content += parser.get_plain_text(parsed)

            # Export attachments
            if attachments and args.include_drawings:
                content += "\n\n## Attachments\n\n"
                for att in attachments:
                    if att['type_uti'] in AttachmentType.DRAWING_TYPES:
                        extracted = extractor.extract_to_directory(
                            att['uuid'],
                            att['type_uti'],
                            attachments_dir,
                            account_id=note.account_identifier
                        )
                        if not extracted.error:
                            rel_path = extracted.path.relative_to(note_dir)
                            content += f"![Drawing]({rel_path})\n\n"
                        else:
                            content += f"*Drawing {att['uuid']}: {extracted.error}*\n\n"
                    else:
                        content += f"*Attachment: {att['type_uti']} - {att['uuid']}*\n\n"

            output_file.write_text(content)

        else:  # text
            output_file = note_dir / f"{safe_title}.txt"
            content = f"Title: {note.title}\n"
            content += f"Modified: {format_datetime(note.modified)}\n"
            if note.folder_name:
                content += f"Folder: {note.folder_name}\n"
            content += "\n" + "=" * 50 + "\n\n"
            content += parser.get_plain_text(parsed)
            output_file.write_text(content)

        exported += 1
        print(f"  Exported: {note.title}")

    print(f"\nDone! Exported {exported} notes, skipped {skipped}")


def list_drawings(extractor: AttachmentExtractor, args: argparse.Namespace) -> None:
    """List all available drawings."""
    drawings = extractor.list_all_fallback_images()

    if args.format == 'json':
        print(json.dumps(drawings, indent=2))
    else:
        print(f"Found {len(drawings)} fallback images:\n")
        for d in drawings:
            print(f"  Account: {d['account_id'][:8]}...")
            print(f"  Note: {d['note_id']}")
            print(f"  Attachment: {d['attachment_id']}")
            print(f"  Path: {d['path']}")
            print()


def main():
    parser = argparse.ArgumentParser(
        description="Extract content from Apple Notes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument(
        '--format', '-f',
        choices=['text', 'json', 'markdown'],
        default='text',
        help='Output format (default: text)'
    )

    parser.add_argument(
        '--note-id', '-n',
        type=int,
        help='Get specific note by database ID'
    )

    parser.add_argument(
        '--search', '-s',
        type=str,
        help='Search notes by title'
    )

    parser.add_argument(
        '--export', '-e',
        type=str,
        help='Export all notes to directory'
    )

    parser.add_argument(
        '--include-drawings', '-d',
        action='store_true',
        help='Include drawings when exporting'
    )

    parser.add_argument(
        '--list-drawings',
        action='store_true',
        help='List all available drawings/fallback images'
    )

    parser.add_argument(
        '--db-path',
        type=str,
        help='Custom path to NoteStore.sqlite'
    )

    args = parser.parse_args()

    # Initialize components
    try:
        db_path = Path(args.db_path) if args.db_path else None
        db = NotesDatabase(db_path)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except PermissionError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    note_parser = NoteParser()
    extractor = AttachmentExtractor()

    # Route to appropriate handler
    if args.list_drawings:
        list_drawings(extractor, args)
    elif args.export:
        # Export can work with --note-id or --search to filter
        export_notes(db, note_parser, extractor, args)
    elif args.note_id:
        get_note(db, note_parser, args)
    elif args.search:
        search_notes(db, note_parser, args)
    else:
        list_notes(db, args)


if __name__ == "__main__":
    main()
