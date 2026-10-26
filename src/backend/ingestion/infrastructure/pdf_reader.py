"""PyMuPDF access to PDF pages (native text layer and rasterisation)."""


class PyMuPdfReader:
    def open(self, path):
        import pymupdf
        return pymupdf.open(path)

    @property
    def version(self):
        import pymupdf
        return pymupdf.VersionBind
