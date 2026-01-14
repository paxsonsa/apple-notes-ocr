"""Apple Notes OCR - Extract and OCR Apple Notes drawings on macOS."""

from .database import NotesDatabase
from .parser import NoteParser
from .attachments import AttachmentExtractor

__all__ = ['NotesDatabase', 'NoteParser', 'AttachmentExtractor']
__version__ = '0.1.0'
