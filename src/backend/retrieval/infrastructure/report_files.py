"""Filesystem check that a bank report PDF is really served from the reports root."""
from pathlib import Path


class FilesystemReportCatalog:
    def has_pdf(self, root: Path, source_file: str) -> bool:
        path = root/source_file
        return path.resolve().parent==root and path.suffix.lower()=='.pdf' and path.is_file()
