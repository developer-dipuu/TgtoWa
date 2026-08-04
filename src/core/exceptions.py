import asyncio

class FileUploadTimeoutError(asyncio.TimeoutError):
    """A custom timeout error that includes which file failed."""
    def __init__(self, message, *, index, file_path):
        super().__init__(message)
        self.index = index
        self.file_path = file_path

class FileUploadWrapperError(Exception):
    """Wraps a general exception with context about which file failed."""
    def __init__(self, message, *, index, file_path, original_exception):
        super().__init__(message)
        self.index = index
        self.file_path = file_path
        self.original_exception = original_exception