"""
Database access layer for Apple Notes.

Provides queries against the NoteStore.sqlite database.
"""

import sqlite3
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, Optional


# Apple's CoreData epoch is 2001-01-01, not 1970-01-01
# This is the offset in seconds between Unix epoch and CoreData epoch
COREDATA_EPOCH_OFFSET = 978307200


@dataclass
class NoteRecord:
    """Raw note record from database."""
    pk: int
    title: str
    folder_name: Optional[str]
    folder_pk: Optional[int]
    account_pk: Optional[int]
    account_name: Optional[str]
    account_identifier: Optional[str]
    created: Optional[datetime]
    modified: Optional[datetime]
    zdata: Optional[bytes]
    is_encrypted: bool = False
    identifier: Optional[str] = None


@dataclass
class AttachmentRecord:
    """Attachment record from database."""
    pk: int
    note_pk: int
    identifier: str
    type_uti: str
    filename: Optional[str]
    account_identifier: Optional[str]
    generation: Optional[str] = None


@dataclass
class FolderRecord:
    """Folder record from database."""
    pk: int
    name: str
    account_pk: Optional[int]
    parent_pk: Optional[int]
    identifier: Optional[str] = None


class NotesDatabase:
    """
    Interface to Apple Notes SQLite database.

    The database is located at:
    ~/Library/Group Containers/group.com.apple.notes/NoteStore.sqlite
    """

    DEFAULT_PATH = Path.home() / "Library/Group Containers/group.com.apple.notes/NoteStore.sqlite"

    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize database connection.

        Args:
            db_path: Path to NoteStore.sqlite. Uses default if not specified.

        Raises:
            FileNotFoundError: If database doesn't exist
            PermissionError: If Full Disk Access is not granted
        """
        self.db_path = db_path or self.DEFAULT_PATH

        if not self.db_path.exists():
            raise FileNotFoundError(
                f"Notes database not found at {self.db_path}. "
                "Make sure Apple Notes has been used on this device."
            )

        # Test connection
        try:
            conn = self._connect()
            conn.close()
        except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
            error_msg = str(e).lower()
            if "unable to open" in error_msg or "authorization denied" in error_msg:
                raise PermissionError(
                    "Cannot access Notes database. "
                    "Please grant Full Disk Access to your terminal app in "
                    "System Settings > Privacy & Security > Full Disk Access"
                ) from e
            raise

    def _connect(self) -> sqlite3.Connection:
        """Create a new database connection."""
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _coredata_to_datetime(self, timestamp: Optional[float]) -> Optional[datetime]:
        """Convert CoreData timestamp to datetime."""
        if timestamp is None:
            return None
        return datetime.fromtimestamp(timestamp + COREDATA_EPOCH_OFFSET)

    def get_accounts(self) -> list[dict]:
        """Get all Notes accounts (iCloud, local, etc.)."""
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT Z_PK, ZNAME, ZIDENTIFIER, ZACCOUNTTYPE
                FROM ZICCLOUDSYNCINGOBJECT
                WHERE ZNAME IS NOT NULL AND ZACCOUNTTYPE IS NOT NULL
            """)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_folders(self) -> Iterator[FolderRecord]:
        """Get all folders."""
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    Z_PK,
                    ZTITLE2 as name,
                    ZPARENT as parent_pk,
                    ZACCOUNT4 as account_pk,
                    ZIDENTIFIER as identifier
                FROM ZICCLOUDSYNCINGOBJECT
                WHERE ZTITLE2 IS NOT NULL
                  AND ZMARKEDFORDELETION != 1
                ORDER BY ZTITLE2
            """)

            for row in cursor:
                yield FolderRecord(
                    pk=row['Z_PK'],
                    name=row['name'] or 'Untitled Folder',
                    account_pk=row['account_pk'],
                    parent_pk=row['parent_pk'],
                    identifier=row['identifier']
                )
        finally:
            conn.close()

    def get_notes(self, folder_pk: Optional[int] = None) -> Iterator[NoteRecord]:
        """
        Get all notes, optionally filtered by folder.

        Args:
            folder_pk: Optional folder primary key to filter by

        Yields:
            NoteRecord for each note
        """
        conn = self._connect()
        try:
            cursor = conn.cursor()

            query = """
                SELECT
                    note.Z_PK as pk,
                    note.ZTITLE1 as title,
                    note.ZFOLDER as folder_pk,
                    folder.ZTITLE2 as folder_name,
                    note.ZACCOUNT4 as account_pk,
                    account.ZNAME as account_name,
                    account.ZIDENTIFIER as account_identifier,
                    note.ZCREATIONDATE1 as created,
                    note.ZMODIFICATIONDATE1 as modified,
                    note.ZIDENTIFIER as identifier,
                    data.ZDATA as zdata,
                    note.ZCRYPTOTAG as crypto_tag
                FROM ZICCLOUDSYNCINGOBJECT AS note
                LEFT JOIN ZICNOTEDATA AS data ON data.ZNOTE = note.Z_PK
                LEFT JOIN ZICCLOUDSYNCINGOBJECT AS folder ON folder.Z_PK = note.ZFOLDER
                LEFT JOIN ZICCLOUDSYNCINGOBJECT AS account ON account.Z_PK = note.ZACCOUNT4
                WHERE note.ZTITLE1 IS NOT NULL
                  AND note.ZMARKEDFORDELETION != 1
            """

            params = []
            if folder_pk is not None:
                query += " AND note.ZFOLDER = ?"
                params.append(folder_pk)

            query += " ORDER BY note.ZMODIFICATIONDATE1 DESC"

            cursor.execute(query, params)

            for row in cursor:
                yield NoteRecord(
                    pk=row['pk'],
                    title=row['title'] or 'Untitled',
                    folder_pk=row['folder_pk'],
                    folder_name=row['folder_name'],
                    account_pk=row['account_pk'],
                    account_name=row['account_name'],
                    account_identifier=row['account_identifier'],
                    created=self._coredata_to_datetime(row['created']),
                    modified=self._coredata_to_datetime(row['modified']),
                    zdata=row['zdata'],
                    is_encrypted=row['crypto_tag'] is not None,
                    identifier=row['identifier']
                )
        finally:
            conn.close()

    def get_note_by_pk(self, pk: int) -> Optional[NoteRecord]:
        """Get a specific note by primary key."""
        for note in self.get_notes():
            if note.pk == pk:
                return note
        return None

    def get_note_by_identifier(self, identifier: str) -> Optional[NoteRecord]:
        """Get a specific note by UUID identifier."""
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    note.Z_PK as pk,
                    note.ZTITLE1 as title,
                    note.ZFOLDER as folder_pk,
                    folder.ZTITLE2 as folder_name,
                    note.ZACCOUNT4 as account_pk,
                    account.ZNAME as account_name,
                    account.ZIDENTIFIER as account_identifier,
                    note.ZCREATIONDATE1 as created,
                    note.ZMODIFICATIONDATE1 as modified,
                    note.ZIDENTIFIER as identifier,
                    data.ZDATA as zdata,
                    note.ZCRYPTOTAG as crypto_tag
                FROM ZICCLOUDSYNCINGOBJECT AS note
                LEFT JOIN ZICNOTEDATA AS data ON data.ZNOTE = note.Z_PK
                LEFT JOIN ZICCLOUDSYNCINGOBJECT AS folder ON folder.Z_PK = note.ZFOLDER
                LEFT JOIN ZICCLOUDSYNCINGOBJECT AS account ON account.Z_PK = note.ZACCOUNT4
                WHERE note.ZIDENTIFIER = ?
                  AND note.ZMARKEDFORDELETION != 1
            """, (identifier,))

            row = cursor.fetchone()
            if not row:
                return None

            return NoteRecord(
                pk=row['pk'],
                title=row['title'] or 'Untitled',
                folder_pk=row['folder_pk'],
                folder_name=row['folder_name'],
                account_pk=row['account_pk'],
                account_name=row['account_name'],
                account_identifier=row['account_identifier'],
                created=self._coredata_to_datetime(row['created']),
                modified=self._coredata_to_datetime(row['modified']),
                zdata=row['zdata'],
                is_encrypted=row['crypto_tag'] is not None,
                identifier=row['identifier']
            )
        finally:
            conn.close()

    def get_attachments(self, note_pk: Optional[int] = None) -> Iterator[AttachmentRecord]:
        """
        Get attachments, optionally filtered by note.

        Args:
            note_pk: Optional note primary key to filter by

        Yields:
            AttachmentRecord for each attachment
        """
        conn = self._connect()
        try:
            cursor = conn.cursor()

            query = """
                SELECT
                    att.Z_PK as pk,
                    att.ZNOTE as note_pk,
                    att.ZIDENTIFIER as identifier,
                    att.ZTYPEUTI as type_uti,
                    att.ZFILENAME as filename,
                    account.ZIDENTIFIER as account_identifier,
                    att.ZGENERATION1 as generation
                FROM ZICCLOUDSYNCINGOBJECT AS att
                LEFT JOIN ZICCLOUDSYNCINGOBJECT AS note ON note.Z_PK = att.ZNOTE
                LEFT JOIN ZICCLOUDSYNCINGOBJECT AS account ON account.Z_PK = note.ZACCOUNT4
                WHERE att.ZTYPEUTI IS NOT NULL
                  AND att.ZMARKEDFORDELETION != 1
            """

            params = []
            if note_pk is not None:
                query += " AND att.ZNOTE = ?"
                params.append(note_pk)

            cursor.execute(query, params)

            for row in cursor:
                yield AttachmentRecord(
                    pk=row['pk'],
                    note_pk=row['note_pk'] or 0,
                    identifier=row['identifier'] or '',
                    type_uti=row['type_uti'] or '',
                    filename=row['filename'],
                    account_identifier=row['account_identifier'],
                    generation=row['generation']
                )
        finally:
            conn.close()

    def search_notes(self, query: str) -> Iterator[NoteRecord]:
        """
        Search notes by title or content.

        This is a basic LIKE search - for full-text search,
        the content would need to be parsed first.

        Args:
            query: Search string

        Yields:
            Matching NoteRecord objects
        """
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    note.Z_PK as pk,
                    note.ZTITLE1 as title,
                    note.ZFOLDER as folder_pk,
                    folder.ZTITLE2 as folder_name,
                    note.ZACCOUNT4 as account_pk,
                    account.ZNAME as account_name,
                    account.ZIDENTIFIER as account_identifier,
                    note.ZCREATIONDATE1 as created,
                    note.ZMODIFICATIONDATE1 as modified,
                    note.ZIDENTIFIER as identifier,
                    data.ZDATA as zdata,
                    note.ZCRYPTOTAG as crypto_tag
                FROM ZICCLOUDSYNCINGOBJECT AS note
                LEFT JOIN ZICNOTEDATA AS data ON data.ZNOTE = note.Z_PK
                LEFT JOIN ZICCLOUDSYNCINGOBJECT AS folder ON folder.Z_PK = note.ZFOLDER
                LEFT JOIN ZICCLOUDSYNCINGOBJECT AS account ON account.Z_PK = note.ZACCOUNT4
                WHERE note.ZTITLE1 IS NOT NULL
                  AND note.ZMARKEDFORDELETION != 1
                  AND note.ZTITLE1 LIKE ?
                ORDER BY note.ZMODIFICATIONDATE1 DESC
            """, (f'%{query}%',))

            for row in cursor:
                yield NoteRecord(
                    pk=row['pk'],
                    title=row['title'] or 'Untitled',
                    folder_pk=row['folder_pk'],
                    folder_name=row['folder_name'],
                    account_pk=row['account_pk'],
                    account_name=row['account_name'],
                    account_identifier=row['account_identifier'],
                    created=self._coredata_to_datetime(row['created']),
                    modified=self._coredata_to_datetime(row['modified']),
                    zdata=row['zdata'],
                    is_encrypted=row['crypto_tag'] is not None,
                    identifier=row['identifier']
                )
        finally:
            conn.close()
