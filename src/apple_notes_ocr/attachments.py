"""
Attachment extraction for Apple Notes.

Handles extraction of drawings, images, and other embedded content.
"""

from pathlib import Path
from dataclasses import dataclass
from typing import Optional
import base64


# Known attachment type UTIs
class AttachmentType:
    DRAWING = 'com.apple.drawing.2'
    DRAWING_LEGACY = 'com.apple.drawing'
    PAPER = 'com.apple.paper'  # Newer "Paper" drawings (pencil sketches)
    TABLE = 'com.apple.notes.table'
    GALLERY = 'com.apple.notes.gallery'
    IMAGE_JPEG = 'public.jpeg'
    IMAGE_PNG = 'public.png'
    IMAGE_HEIC = 'public.heic'
    IMAGE_GIF = 'com.compuserve.gif'
    PDF = 'com.adobe.pdf'
    AUDIO = 'public.audio'
    VIDEO = 'public.movie'
    VCARD = 'public.vcard'
    URL = 'public.url'
    HASHTAG = 'com.apple.notes.inlinetextattachment.hashtag'
    LINK = 'com.apple.notes.inlinetextattachment.link'

    # All drawing types
    DRAWING_TYPES = (DRAWING, DRAWING_LEGACY, PAPER)


@dataclass
class ExtractedAttachment:
    """An extracted attachment with its data."""
    identifier: str
    type_uti: str
    filename: str
    data: Optional[bytes] = None
    path: Optional[Path] = None
    error: Optional[str] = None

    @property
    def is_drawing(self) -> bool:
        return self.type_uti in AttachmentType.DRAWING_TYPES

    @property
    def is_image(self) -> bool:
        return self.type_uti in (
            AttachmentType.IMAGE_JPEG,
            AttachmentType.IMAGE_PNG,
            AttachmentType.IMAGE_HEIC,
            AttachmentType.IMAGE_GIF
        )

    @property
    def data_base64(self) -> Optional[str]:
        """Get data as base64 string."""
        if self.data:
            return base64.b64encode(self.data).decode('ascii')
        return None

    @property
    def mime_type(self) -> str:
        """Get MIME type based on UTI."""
        mime_map = {
            AttachmentType.DRAWING: 'image/png',
            AttachmentType.DRAWING_LEGACY: 'image/png',
            AttachmentType.IMAGE_JPEG: 'image/jpeg',
            AttachmentType.IMAGE_PNG: 'image/png',
            AttachmentType.IMAGE_HEIC: 'image/heic',
            AttachmentType.IMAGE_GIF: 'image/gif',
            AttachmentType.PDF: 'application/pdf',
            AttachmentType.AUDIO: 'audio/mpeg',
            AttachmentType.VIDEO: 'video/mp4',
        }
        return mime_map.get(self.type_uti, 'application/octet-stream')


class AttachmentExtractor:
    """
    Extracts attachments from Apple Notes storage.

    Attachments are stored in the FallbackImages directory structure:
    ~/Library/Group Containers/group.com.apple.notes/Accounts/{account_id}/FallbackImages/{note_id}/{attachment_id}/FallbackImage.png

    For drawings, the FallbackImage.png is a pre-rendered PNG of the drawing.
    """

    NOTES_CONTAINER = Path.home() / "Library/Group Containers/group.com.apple.notes"
    ACCOUNTS_DIR = NOTES_CONTAINER / "Accounts"

    def __init__(self, container_path: Optional[Path] = None):
        """
        Initialize attachment extractor.

        Args:
            container_path: Custom path to Notes container (for testing)
        """
        if container_path:
            self.container = container_path
            self.accounts_dir = container_path / "Accounts"
        else:
            self.container = self.NOTES_CONTAINER
            self.accounts_dir = self.ACCOUNTS_DIR

    def _find_accounts(self) -> list[str]:
        """Find all account UUIDs in the Accounts directory."""
        if not self.accounts_dir.exists():
            return []

        accounts = []
        try:
            for item in self.accounts_dir.iterdir():
                if item.is_dir() and len(item.name) == 36:  # UUID format
                    accounts.append(item.name)
        except PermissionError:
            pass

        return accounts

    def _find_fallback_image(
        self,
        account_id: str,
        attachment_id: str,
        note_id: Optional[str] = None
    ) -> Optional[Path]:
        """
        Find a FallbackImage for an attachment.

        The directory structure can vary, so we search for the image.

        Args:
            account_id: Account UUID
            attachment_id: Attachment UUID
            note_id: Optional note UUID to narrow search

        Returns:
            Path to FallbackImage.png if found
        """
        account_path = self.accounts_dir / account_id / "FallbackImages"

        if not account_path.exists():
            return None

        try:
            # For com.apple.paper: the attachment_id IS the folder name
            # Structure: FallbackImages/{paper_uuid}/{index}_{sub_uuid}/FallbackImage.png
            paper_folder = account_path / attachment_id
            if paper_folder.exists() and paper_folder.is_dir():
                # Return the first FallbackImage found in subfolders
                for subfolder in paper_folder.iterdir():
                    if subfolder.is_dir():
                        fallback = subfolder / "FallbackImage.png"
                        if fallback.exists():
                            return fallback

            # If we have note_id, look in that folder first
            if note_id:
                note_path = account_path / note_id
                if note_path.exists():
                    # Look for attachment subfolder
                    for subfolder in note_path.iterdir():
                        if attachment_id in subfolder.name:
                            fallback = subfolder / "FallbackImage.png"
                            if fallback.exists():
                                return fallback

            # Search all folders for attachment_id in subfolder name
            for note_folder in account_path.iterdir():
                if not note_folder.is_dir():
                    continue
                for attachment_folder in note_folder.iterdir():
                    if attachment_id in attachment_folder.name:
                        fallback = attachment_folder / "FallbackImage.png"
                        if fallback.exists():
                            return fallback

        except PermissionError:
            pass

        return None

    def get_drawing(
        self,
        attachment_id: str,
        account_id: Optional[str] = None,
        note_id: Optional[str] = None
    ) -> ExtractedAttachment:
        """
        Get a drawing attachment by ID.

        Args:
            attachment_id: The attachment UUID
            account_id: Optional account UUID (searches all if not provided)
            note_id: Optional note UUID

        Returns:
            ExtractedAttachment with drawing data
        """
        accounts = [account_id] if account_id else self._find_accounts()

        for acc_id in accounts:
            path = self._find_fallback_image(acc_id, attachment_id, note_id)
            if path:
                try:
                    data = path.read_bytes()
                    return ExtractedAttachment(
                        identifier=attachment_id,
                        type_uti=AttachmentType.DRAWING,
                        filename=f"{attachment_id}.png",
                        data=data,
                        path=path
                    )
                except PermissionError:
                    return ExtractedAttachment(
                        identifier=attachment_id,
                        type_uti=AttachmentType.DRAWING,
                        filename=f"{attachment_id}.png",
                        error="Permission denied - grant Full Disk Access to terminal"
                    )
                except Exception as e:
                    return ExtractedAttachment(
                        identifier=attachment_id,
                        type_uti=AttachmentType.DRAWING,
                        filename=f"{attachment_id}.png",
                        error=str(e)
                    )

        return ExtractedAttachment(
            identifier=attachment_id,
            type_uti=AttachmentType.DRAWING,
            filename=f"{attachment_id}.png",
            error=f"FallbackImage not found for {attachment_id}"
        )

    def get_image(
        self,
        attachment_id: str,
        type_uti: str,
        account_id: Optional[str] = None,
        note_id: Optional[str] = None
    ) -> ExtractedAttachment:
        """
        Get an image attachment by ID.

        Images may be in FallbackImages or the Media folder.
        """
        # Try FallbackImages first (works for many image types)
        accounts = [account_id] if account_id else self._find_accounts()

        for acc_id in accounts:
            path = self._find_fallback_image(acc_id, attachment_id, note_id)
            if path:
                try:
                    data = path.read_bytes()
                    ext = {
                        AttachmentType.IMAGE_JPEG: '.jpg',
                        AttachmentType.IMAGE_PNG: '.png',
                        AttachmentType.IMAGE_HEIC: '.heic',
                        AttachmentType.IMAGE_GIF: '.gif',
                    }.get(type_uti, '.png')

                    return ExtractedAttachment(
                        identifier=attachment_id,
                        type_uti=type_uti,
                        filename=f"{attachment_id}{ext}",
                        data=data,
                        path=path
                    )
                except PermissionError:
                    return ExtractedAttachment(
                        identifier=attachment_id,
                        type_uti=type_uti,
                        filename=f"{attachment_id}.png",
                        error="Permission denied"
                    )

        return ExtractedAttachment(
            identifier=attachment_id,
            type_uti=type_uti,
            filename=f"{attachment_id}.png",
            error=f"Image not found for {attachment_id}"
        )

    def extract_to_directory(
        self,
        attachment_id: str,
        type_uti: str,
        output_dir: Path,
        account_id: Optional[str] = None,
        note_id: Optional[str] = None
    ) -> ExtractedAttachment:
        """
        Extract an attachment to a directory.

        Args:
            attachment_id: The attachment UUID
            type_uti: The attachment type UTI
            output_dir: Directory to save the file
            account_id: Optional account UUID
            note_id: Optional note UUID

        Returns:
            ExtractedAttachment with path to extracted file
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        if type_uti in AttachmentType.DRAWING_TYPES:
            attachment = self.get_drawing(attachment_id, account_id, note_id)
        elif type_uti in (
            AttachmentType.IMAGE_JPEG,
            AttachmentType.IMAGE_PNG,
            AttachmentType.IMAGE_HEIC,
            AttachmentType.IMAGE_GIF
        ):
            attachment = self.get_image(attachment_id, type_uti, account_id, note_id)
        else:
            return ExtractedAttachment(
                identifier=attachment_id,
                type_uti=type_uti,
                filename=f"{attachment_id}",
                error=f"Unsupported attachment type: {type_uti}"
            )

        if attachment.error:
            return attachment

        # Save to output directory
        output_path = output_dir / attachment.filename
        try:
            output_path.write_bytes(attachment.data)
            attachment.path = output_path
            return attachment
        except Exception as e:
            attachment.error = f"Failed to save: {e}"
            return attachment

    def list_all_fallback_images(self) -> list[dict]:
        """
        List all available fallback images across all accounts.

        Useful for debugging and discovering available drawings.

        Returns:
            List of dicts with account_id, note_id, attachment_id, and path
        """
        results = []

        for account_id in self._find_accounts():
            fallback_dir = self.accounts_dir / account_id / "FallbackImages"
            if not fallback_dir.exists():
                continue

            try:
                for note_folder in fallback_dir.iterdir():
                    if not note_folder.is_dir():
                        continue
                    for attachment_folder in note_folder.iterdir():
                        if not attachment_folder.is_dir():
                            continue
                        fallback = attachment_folder / "FallbackImage.png"
                        if fallback.exists():
                            # Parse attachment ID from folder name
                            # Format is often: {index}_{uuid}
                            folder_name = attachment_folder.name
                            parts = folder_name.split('_', 1)
                            attachment_id = parts[1] if len(parts) > 1 else folder_name

                            results.append({
                                'account_id': account_id,
                                'note_id': note_folder.name,
                                'attachment_id': attachment_id,
                                'folder_name': folder_name,
                                'path': str(fallback)
                            })
            except PermissionError:
                continue

        return results
