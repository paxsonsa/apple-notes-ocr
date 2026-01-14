"""
Configuration and filtering for Apple Notes OCR.

Supports filtering notes by folder name and title patterns via environment variables.
"""

import os
import fnmatch
from typing import Optional


class NotesFilter:
    """
    Filters notes based on environment configuration.

    Environment variables:
        APPLE_NOTES_BLOCKED_FOLDERS: Comma-separated folder names to block
        APPLE_NOTES_ALLOWED_FOLDERS: Comma-separated folder names to allow (whitelist mode)
        APPLE_NOTES_BLOCKED_TITLES: Comma-separated title patterns to block (supports glob wildcards)

    Examples:
        export APPLE_NOTES_BLOCKED_FOLDERS="Private,Work Confidential"
        export APPLE_NOTES_ALLOWED_FOLDERS="Personal,Shared"
        export APPLE_NOTES_BLOCKED_TITLES="Secret*,*password*,*confidential*"
    """

    def __init__(self) -> None:
        self.blocked_folders = self._parse_list("APPLE_NOTES_BLOCKED_FOLDERS")
        self.allowed_folders = self._parse_list("APPLE_NOTES_ALLOWED_FOLDERS")
        self.blocked_titles = self._parse_list("APPLE_NOTES_BLOCKED_TITLES")

    def _parse_list(self, env_var: str) -> list[str]:
        """Parse comma-separated environment variable into list."""
        value = os.environ.get(env_var, "")
        return [v.strip() for v in value.split(",") if v.strip()]

    def is_folder_allowed(self, folder_name: Optional[str]) -> bool:
        """
        Check if a folder is allowed based on configuration.

        Args:
            folder_name: The folder name to check (None treated as "Notes")

        Returns:
            True if the folder is allowed, False if blocked
        """
        if not folder_name:
            folder_name = "Notes"  # Default folder name

        # If allowed list is set, folder must be in it (whitelist mode)
        if self.allowed_folders:
            return folder_name in self.allowed_folders

        # Otherwise, folder must not be in blocked list (blacklist mode)
        return folder_name not in self.blocked_folders

    def is_title_allowed(self, title: str) -> bool:
        """
        Check if a note title is allowed based on pattern matching.

        Args:
            title: The note title to check

        Returns:
            True if the title is allowed, False if it matches a blocked pattern
        """
        for pattern in self.blocked_titles:
            if fnmatch.fnmatch(title.lower(), pattern.lower()):
                return False
        return True

    def should_include(self, folder_name: Optional[str], title: str) -> bool:
        """
        Check if a note should be included based on all filters.

        Args:
            folder_name: The note's folder name
            title: The note's title

        Returns:
            True if the note passes all filters
        """
        return self.is_folder_allowed(folder_name) and self.is_title_allowed(title)

    def is_configured(self) -> bool:
        """Check if any filtering is configured."""
        return bool(self.blocked_folders or self.allowed_folders or self.blocked_titles)

    def get_config_summary(self) -> dict:
        """Get a summary of current filter configuration."""
        return {
            "blocked_folders": self.blocked_folders,
            "allowed_folders": self.allowed_folders,
            "blocked_titles": self.blocked_titles,
            "is_active": self.is_configured()
        }


# Global filter instance (lazy initialized)
_filter: NotesFilter | None = None


def get_filter() -> NotesFilter:
    """Get the global NotesFilter instance."""
    global _filter
    if _filter is None:
        _filter = NotesFilter()
    return _filter
