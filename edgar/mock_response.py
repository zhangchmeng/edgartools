"""Mock response utilities for cached content."""

from typing import Iterator, Generator
import io


class MockResponse:
    """Mock response object that mimics httpx.Response for cached content."""
    
    def __init__(self, content_generator: Generator[bytes, None, None], url: str):
        self.content_generator = content_generator
        self.url = url
        self.status_code = 200
        self.headers = {}
        self._content_buffer = None
        
    def iter_lines(self) -> Iterator[bytes]:
        """Iterate over lines in the cached content."""
        if self._content_buffer is None:
            # Collect all content from generator
            self._content_buffer = b''.join(self.content_generator)
        
        # Split content into lines
        lines = self._content_buffer.split(b'\n')
        for line in lines:
            if line:  # Skip empty lines
                yield line
                
    def iter_bytes(self, chunk_size: int = 8192) -> Iterator[bytes]:
        """Iterate over bytes in the cached content."""
        if self._content_buffer is None:
            # Collect all content from generator
            self._content_buffer = b''.join(self.content_generator)
            
        # Yield content in chunks
        for i in range(0, len(self._content_buffer), chunk_size):
            yield self._content_buffer[i:i + chunk_size]


def create_cached_response(content_generator: Generator[bytes, None, None], url: str) -> MockResponse:
    """Create a mock response object for cached content."""
    return MockResponse(content_generator, url)