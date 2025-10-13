import gzip
import logging
import os
import shutil
import tarfile
import time
import zipfile
from collections import deque
from functools import wraps
from io import BytesIO
from pathlib import Path
from threading import Lock
from typing import Union, Optional

from httpx import RequestError, Response, AsyncClient
import orjson as json
from stamina import retry
from tqdm import tqdm
from loguru import logger

from edgar.core import text_extensions, get_edgar_data_directory
from edgar.httpclient import http_client, async_http_client
from edgar.s3 import S3FileHandler

"""
This module provides functions to handle HTTP requests with retry logic, throttling, and identity management.
"""
__all__ = ["get_with_retry", "get_with_retry_async", "stream_with_retry", "post_with_retry", "post_with_retry_async",
           "download_file", "download_file_async", "download_json", "download_json_async", "stream_file",
           "download_text", "download_text_between_tags", "download_bulk_data", "download_datafile",
           "throttle_requests"]

attempts = 6
retry_timeout = 40
wait_initial = 0.1
max_requests_per_second = 8
throttle_disabled = False

class TooManyRequestsError(Exception):
    def __init__(self, url, message="Too Many Requests"):
        self.url = url
        self.message = message
        super().__init__(self.message)


class IdentityNotSetException(Exception):
    pass


class RequestRate:
    """
    A simple class to represent a request rate, i.e., the maximum number of requests for a given time window
    """

    def __init__(self, max_requests: int, time_window: int):
        if max_requests <= 0:
            raise ValueError("max_requests must be a positive integer")
        if time_window <= 0:
            raise ValueError("time_window must be a positive integer")

        self.max_requests: int = max_requests
        self.time_window: int = time_window


class Throttler:
    """
    A simple throttler that limits the number of requests per time window
    """

    def __init__(self, request_rate: RequestRate, sleep_interval=0.1):
        self.request_rate = request_rate
        self.sleep_interval = sleep_interval
        self.request_timestamps = deque()
        self.lock = Lock()
        self.decorated_functions = None
        self.total_calls = 0
        self.peak_call_rate: float = 0.0

    def get_ticket(self):
        with self.lock:
            current_time = time.monotonic()

            # Remove timestamps older than the time window
            while self.request_timestamps and self.request_timestamps[
                0] <= current_time - self.request_rate.time_window:
                self.request_timestamps.popleft()

            if len(self.request_timestamps) < self.request_rate.max_requests:
                self.request_timestamps.append(current_time)
                return True
            else:
                return False

    def wait_for_ticket(self):
        while not self.get_ticket():
            time.sleep(self.sleep_interval)

    def update_metrics(self):
        self.total_calls += 1
        current_call_rate: float = len(self.request_timestamps) / self.request_rate.time_window
        self.peak_call_rate = max(self.peak_call_rate, current_call_rate)

    def get_metrics(self):
        return {
            "decorated_functions": self.decorated_functions,  # Now it's a list
            "total_calls": self.total_calls,
            "peak_call_rate": self.peak_call_rate,
            "request_rate_limit": self.request_rate.max_requests,
        }

    def print_metrics(self):
        metrics = self.get_metrics()
        print(f"Metrics for decorated functions: {', '.join(metrics['decorated_functions'])}")
        print(f"Total calls: {metrics['total_calls']}")
        print(f"Peak call rate: {metrics['peak_call_rate']:.2f} calls per second")


_throttler_instances = {}  # Singleton instance for throttler


def throttle_requests(request_rate=None, requests_per_second=None, **kwargs):
    """
    Decorator to throttle the number of requests per second.
    """

    if requests_per_second is not None:
        request_rate = RequestRate(max_requests=requests_per_second, time_window=1)
    elif request_rate is None:
        raise ValueError("Either request_rate or requests_per_second must be provided")

    # Use a single key for all instances to create a global throttler
    key = "global_throttler"

    if key not in _throttler_instances:
        _throttler_instances[key] = Throttler(request_rate, **kwargs)

    throttler = _throttler_instances[key]

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if throttle_disabled:
                return func(*args, **kwargs)
            else:
                throttler.wait_for_ticket()
                result = func(*args, **kwargs)
                throttler.update_metrics()
                return result

        # Store the decorated function name
        if throttler.decorated_functions is None:
            throttler.decorated_functions = []
        throttler.decorated_functions.append(func.__name__)

        return wrapper

    return decorator


def is_redirect(response):
    return response.status_code in [301, 302]


def with_identity(func):
    @wraps(func)
    def wrapper(url, identity=None, identity_callable=None, *args, **kwargs):
        if identity is None:
            if identity_callable is not None:
                identity = identity_callable()
            else:
                identity = os.environ.get("EDGAR_IDENTITY")
        if identity is None:
            raise IdentityNotSetException("User-Agent identity is not set")

        headers = kwargs.get("headers", {})
        headers["User-Agent"] = identity
        kwargs["headers"] = headers

        return func(url, identity=identity, identity_callable=identity_callable, *args, **kwargs)

    return wrapper


def async_with_identity(func):
    @wraps(func)
    def wrapper(client, url, identity=None, identity_callable=None, *args, **kwargs):
        if identity is None:
            if identity_callable is not None:
                identity = identity_callable()
            else:
                identity = os.environ.get("EDGAR_IDENTITY")
        if identity is None:
            raise IdentityNotSetException("User-Agent identity is not set")

        headers = kwargs.get("headers", {})
        headers["User-Agent"] = identity
        kwargs["headers"] = headers

        return func(client, url, identity=identity, identity_callable=identity_callable, *args, **kwargs)

    return wrapper



@retry(on=RequestError, attempts=attempts, timeout=retry_timeout, wait_initial=wait_initial)
@with_identity
@throttle_requests(requests_per_second=max_requests_per_second)
def get_with_retry(url, identity=None, identity_callable=None, **kwargs):
    """
    Sends a GET request with retry functionality and identity handling.

    Args:
        url (str): The URL to send the GET request to.
        identity (str, optional): The identity to use for the request. Defaults to None.
        identity_callable (callable, optional): A callable that returns the identity. Defaults to None.
        **kwargs: Additional keyword arguments to pass to the underlying httpx.Client.get() method.

    Returns:
        httpx.Response: The response object returned by the GET request.

    Raises:
        TooManyRequestsError: If the response status code is 429 (Too Many Requests).
    """
    with http_client() as client:
        response = client.get(url, **kwargs)
        if response.status_code == 429:
            raise TooManyRequestsError(url)
        elif is_redirect(response):
            return get_with_retry(url=response.headers["Location"], identity=identity, identity_callable=identity_callable,
                                 **kwargs)
        return response


@retry(on=RequestError, attempts=attempts, timeout=retry_timeout, wait_initial=wait_initial)
@async_with_identity
@throttle_requests(requests_per_second=max_requests_per_second)
async def get_with_retry_async(client: AsyncClient, url, identity=None, identity_callable=None, **kwargs):
    """
    Sends an asynchronous GET request with retry functionality and identity handling.

    Args:
        url (str): The URL to send the GET request to.
        identity (str, optional): The identity to use for the request. Defaults to None.
        identity_callable (callable, optional): A callable that returns the identity. Defaults to None.
        **kwargs: Additional keyword arguments to pass to the underlying httpx.AsyncClient.get() method.

    Returns:
        httpx.Response: The response object returned by the GET request.

    Raises:
        TooManyRequestsError: If the response status code is 429 (Too Many Requests).
    """
    response = await client.get(url, **kwargs)
    if response.status_code == 429:
        raise TooManyRequestsError(url)
    elif is_redirect(response):
        return await get_with_retry_async(client=client, url=response.headers["Location"], identity=identity,
                                            identity_callable=identity_callable, **kwargs)
    return response


def _stream_from_s3_cache(url, use_s3_cache: Optional[bool] = None):
    """
    Internal function to check and return content from S3 cache without throttling.
    
    Returns:
        Generator or None: Cached content as bytes generator if found, None otherwise.
    """
    from .s3_utils import should_use_s3_cache, get_s3_cache_key
    
    # Determine if S3 cache should be used
    use_cache = should_use_s3_cache(use_s3_cache)
    
    # If S3 cache is not configured or disabled, return None immediately
    if not use_cache:
        print(f"S3 cache disabled for URL: {url}")
        return None
    
    # Try to get from S3 cache
    try:
        s3_handler = S3FileHandler()
        s3_key = get_s3_cache_key(url)
        
        print(f"Checking S3 cache for URL: {url} -> S3 key: {s3_key}")
        
        # Check if file exists in S3
        if s3_handler.file_exists(s3_key):
            print(f"✅ S3 CACHE HIT: Found cached file for URL: {url} (S3 key: {s3_key})")
            cached_content = s3_handler.read_file_content(s3_key)
            if cached_content is not None:
                content_size = len(cached_content)
                print(f"✅ S3 CACHE SUCCESS: Retrieved {content_size} bytes from cache for URL: {url}")
                
                # Return cached content as bytes generator
                if isinstance(cached_content, str):
                    cached_bytes = cached_content.encode('utf-8')
                else:
                    cached_bytes = cached_content
                chunk_size = 8192
                
                def _generate_cached_chunks():
                    for i in range(0, len(cached_bytes), chunk_size):
                        yield cached_bytes[i:i + chunk_size]
                
                return _generate_cached_chunks()
            else:
                print(f"❌ S3 CACHE MISS: File exists but content is None for URL: {url}")
        else:
            print(f"❌ S3 CACHE MISS: No cached file found for URL: {url} (S3 key: {s3_key})")
    except Exception as e:
        print(f"❌ S3 CACHE ERROR: Failed to read from S3 cache for URL: {url}, error: {e}, falling back to HTTP streaming")
    
    # Return None if no cached content found or S3 operations failed
    print(f"S3 cache lookup failed for URL: {url}, will use HTTP request")
    return None


def _cache_to_s3(content: bytes, url: str, use_s3_cache: Optional[bool] = None):
    """
    Internal function to cache content to S3 without throttling.
    """
    from .s3_utils import should_use_s3_cache, get_s3_cache_key
    
    use_cache = should_use_s3_cache(use_s3_cache)
    if use_cache:
        try:
            s3_handler = S3FileHandler()
            s3_key = get_s3_cache_key(url)
            # Upload bytes content directly to S3
            s3_handler.upload_content(content, s3_key)
            print(f"Cached streamed file to S3: {s3_key}")
        except Exception as e:
            print(f"S3 cache write failed: {e}")


@retry(on=RequestError, attempts=attempts, timeout=retry_timeout, wait_initial=wait_initial)
@with_identity
@throttle_requests(requests_per_second=max_requests_per_second)
def _stream_from_http(url, identity=None, identity_callable=None, use_s3_cache: Optional[bool] = None, **kwargs):
    """
    Internal function to stream from HTTP with throttling applied.
    """
    print(f"🌐 HTTP REQUEST: Starting HTTP stream request for URL: {url}")
    
    # Stream from URL
    with http_client() as client:
        with client.stream("GET", url, **kwargs) as response:
            print(f"🌐 HTTP RESPONSE: Received response with status {response.status_code} for URL: {url}")
            
            if response.status_code == 429:
                print(f"❌ HTTP ERROR: Too many requests (429) for URL: {url}")
                raise TooManyRequestsError(url)
            elif is_redirect(response):
                # Handle redirects by calling the main function recursively
                redirect_location = response.headers["Location"]
                print(f"🔄 HTTP REDIRECT: Redirecting from {url} to {redirect_location}")
                
                # Handle relative URLs by joining with the base URL
                if redirect_location.startswith('/'):
                    redirect_url = str(response.url.copy_with(path=redirect_location))
                elif redirect_location.startswith('http'):
                    redirect_url = redirect_location
                else:
                    # Relative path, resolve against current URL
                    redirect_url = str(response.url.copy_with(path=response.url.path.rstrip('/') + '/' + redirect_location))
                
                print(f"🔄 HTTP REDIRECT: Resolved redirect URL: {redirect_url}")
                response = _stream_from_http(redirect_url,
                                        identity=identity,
                                        identity_callable=identity_callable,
                                        use_s3_cache=use_s3_cache, **kwargs)
            else:
                print(f"✅ HTTP SUCCESS: Successfully established stream for URL: {url}")
                
                # Cache content while streaming if S3 cache is enabled
                from .s3_utils import should_use_s3_cache
                use_cache = should_use_s3_cache(use_s3_cache)
                
                if use_cache:
                    print(f"S3 caching enabled for URL: {url}")
                    try:
                        cached_content = b''
                        # Stream and cache simultaneously
                        for chunk in response.iter_bytes():
                            cached_content += chunk
                        
                        # Upload to S3 cache after streaming is complete (without throttling)
                        _cache_to_s3(cached_content, url, use_s3_cache)
                        
                        # Create a generator for the cached content to avoid stream consumption issue
                        def content_generator():
                            yield cached_content
                        
                        # Create a new response object with cached content
                        from .mock_response import create_cached_response
                        cached_response = create_cached_response(content_generator(), url)
                        yield cached_response
                    except Exception as e:
                        print(f"❌ S3 CACHE ERROR: S3 cache streaming failed for URL: {url}, error: {e}, continuing without caching")
                        # Fall back to normal streaming without caching
                        yield response
                else:
                    print(f"S3 caching disabled for URL: {url}")
                    yield response


def stream_with_retry(url, identity=None, identity_callable=None, use_s3_cache: Optional[bool] = None, **kwargs):
    """
    Sends a streaming GET request with retry functionality, identity handling, and S3 caching support.
    S3 cache operations are not subject to throttling, only HTTP requests are throttled.

    Args:
        url (str): The URL to send the streaming GET request to.
        identity (str, optional): The identity to use for the request. Defaults to None.
        identity_callable (callable, optional): A callable that returns the identity. Defaults to None.
        use_s3_cache (bool, optional): Whether to use S3 caching. If None, auto-detect based on S3 configuration.
        **kwargs: Additional keyword arguments to pass to the underlying httpx.Client.stream() method.

    Yields:
        httpx.Response: The response object with streaming capabilities.

    Raises:
        TooManyRequestsError: If the response status code is 429 (Too Many Requests).
    """
    print(f"📡 STREAM REQUEST: Starting stream request for URL: {url}")
    
    # Try to get from S3 cache first (not throttled)
    print(f"Checking S3 cache first for URL: {url}")
    cached_content = _stream_from_s3_cache(url, use_s3_cache)
    if cached_content is not None:
        print(f"✅ USING S3 CACHE: Serving content from S3 cache for URL: {url}")
        # Create a mock response object for cached content
        from .mock_response import create_cached_response
        cached_response = create_cached_response(cached_content, url)
        yield cached_response
        print(f"✅ S3 CACHE COMPLETE: Successfully served cached content for URL: {url}")
        return
    
    # Stream from HTTP if not in cache (throttled)
    print(f"🌐 FALLBACK TO HTTP: S3 cache miss, falling back to HTTP request for URL: {url}")
    yield from _stream_from_http(url, identity=identity, identity_callable=identity_callable, use_s3_cache=use_s3_cache, **kwargs)
    print(f"✅ HTTP STREAM COMPLETE: Finished HTTP streaming for URL: {url}")


@retry(on=RequestError, attempts=attempts, timeout=retry_timeout, wait_initial=wait_initial)
@with_identity
@throttle_requests(requests_per_second=max_requests_per_second)
def post_with_retry(url, data=None, json=None, identity=None, identity_callable=None,
                    **kwargs):
    """
    Sends a POST request with retry functionality and identity handling.

    Args:
        url (str): The URL to send the POST request to.
        data (dict, optional): The data to include in the request body. Defaults to None.
        json (dict, optional): The JSON data to include in the request body. Defaults to None.
        identity (str, optional): The identity to use for the request. Defaults to None.
        identity_callable (callable, optional): A callable that returns the identity. Defaults to None.
        **kwargs: Additional keyword arguments to pass to the underlying httpx.Client.post() method.

    Returns:
        httpx.Response: The response object returned by the POST request.

    Raises:
        TooManyRequestsError: If the response status code is 429 (Too Many Requests).
    """
    with http_client() as client:
        response = client.post(url, data=data, json=json, **kwargs)
        if response.status_code == 429:
            raise TooManyRequestsError(url)
        elif is_redirect(response):
            return post_with_retry(response.headers["Location"], data=data, json=json, identity=identity,
                                   identity_callable=identity_callable, **kwargs)
        return response


@retry(on=RequestError, attempts=attempts, timeout=retry_timeout, wait_initial=wait_initial)
@async_with_identity
@throttle_requests(requests_per_second=max_requests_per_second)
async def post_with_retry_async(client: AsyncClient, 
                                url,
                                data=None,
                                json=None,
                                identity=None,
                                identity_callable=None, **kwargs):
    """
    Sends an asynchronous POST request with retry functionality and identity handling.

    Args:
        url (str): The URL to send the POST request to.
        data (dict, optional): The data to include in the request body. Defaults to None.
        json (dict, optional): The JSON data to include in the request body. Defaults to None.
        identity (str, optional): The identity to use for the request. Defaults to None.
        identity_callable (callable, optional): A callable that returns the identity. Defaults to None.
        **kwargs: Additional keyword arguments to pass to the underlying httpx.AsyncClient.post() method.

    Returns:
        httpx.Response: The response object returned by the POST request.

    Raises:
        TooManyRequestsError: If the response status code is 429 (Too Many Requests).
    """
    response = await client.post(url, data=data, json=json, **kwargs)
    if response.status_code == 429:
        raise TooManyRequestsError(url)
    elif is_redirect(response):
        return await post_with_retry_async(client, response.headers["Location"], data=data, json=json, identity=identity,
                                            identity_callable=identity_callable, **kwargs)
    return response


def inspect_response(response: Response):
    """
    Check if the response is successful and raise an exception if not.
    """
    if response.status_code != 200:
        response.raise_for_status()


def decode_content(content: bytes) -> str:
    """
    Decode the content of a file.
    """
    try:
        return content.decode('utf-8')
    except UnicodeDecodeError:
        return content.decode('latin-1')


def save_or_return_content(content: Union[str, bytes], path: Optional[Union[str, Path]]) -> Union[
    str, bytes, None]:
    """
    Save the content to a specified path or return the content.

    Args:
        content (str or bytes): The content to save or return.
        path (str or Path, optional): The path where the content should be saved. If None, return the content.

    Returns:
        str or bytes or None: The content if not saved, or None if saved.
    """
    if path is not None:
        path = Path(path)

        # Determine if the path is a directory or a file
        if path.is_dir():
            file_name = "downloaded_file"  # Replace with logic to extract file name from URL if available
            file_path = path / file_name
        else:
            file_path = path

        # Save the file
        if isinstance(content, bytes):
            file_path.write_bytes(content)
        else:
            file_path.write_text(content)

        return None

    return content


def _get_from_s3_cache(url, as_text: bool, use_s3_cache: Optional[bool] = None):
    """
    Internal function to get content from S3 cache without throttling.
    
    Returns:
        Content or None: Cached content if found, None otherwise.
    """
    from .s3_utils import should_use_s3_cache, get_s3_cache_key
    
    # Determine if S3 cache should be used
    use_cache = should_use_s3_cache(use_s3_cache)

    # Try to get from S3 cache first
    if use_cache:
        try:
            s3_handler = S3FileHandler()
            # Create a cache key based on the URL
            s3_key = get_s3_cache_key(url)
            
            # Check if file exists in S3
            if s3_handler.file_exists(s3_key):
                print(f"Found cached file in S3: {s3_key}")
                cached_content = s3_handler.read_file_content(s3_key)
                if cached_content is not None:
                    if not as_text:
                        cached_content = cached_content.encode('utf-8')
                    return cached_content
        except Exception as e:
            print(f"S3 cache read failed: {e}, falling back to HTTP download")
    
    return None


def _cache_file_to_s3(content, url: str, use_s3_cache: Optional[bool] = None):
    """
    Internal function to cache file content to S3 without throttling.
    """
    from .s3_utils import should_use_s3_cache, get_s3_cache_key
    
    use_cache = should_use_s3_cache(use_s3_cache)
    if use_cache:
        try:
            s3_handler = S3FileHandler()
            s3_key = get_s3_cache_key(url)
            
            # Convert content to string for S3 storage
            cache_content = content
            if isinstance(content, bytes):
                cache_content = content.decode('utf-8', errors='ignore')
            
            s3_handler.upload_content(cache_content, s3_key)
            print(f"Cached file to S3: {s3_key}")
        except Exception as e:
            print(f"S3 cache write failed: {e}")


def download_file(url: str, as_text: bool = None, path: Optional[Union[str, Path]] = None, use_s3_cache: Optional[bool] = None) -> Union[str, bytes, None]:
    """
    Download a file from a URL with S3 caching support.
    S3 cache operations are not subject to throttling, only HTTP requests are throttled.
    JSON files are not cached to ensure fresh data.

    Args:
        url (str): The URL of the file to download.
        as_text (bool, optional): Whether to download the file as text or binary.
        path (str or Path, optional): The path where the file should be saved.
        If None, the default is determined based on the file extension. Defaults to None.
        use_s3_cache (bool, optional): Whether to use S3 caching. If None, auto-detect based on S3 configuration.

    Returns:
        str or bytes: The content of the downloaded file, either as text or binary data.
    """
    if as_text is None:
        # Set the default based on the file extension
        as_text = url.endswith(text_extensions)

    # Skip cache for JSON files to ensure fresh data
    is_json_file = url.lower().endswith('.json')
    
    # Try to get from S3 cache first (not throttled), but skip for JSON files
    if not is_json_file:
        cached_content = _get_from_s3_cache(url, as_text, use_s3_cache)
        if cached_content is not None:
            path = Path(path) if path else None
            if path and path.is_dir():
                path = path / os.path.basename(url)
            return save_or_return_content(cached_content, path)

    # Download from URL if not in cache (throttled)
    response = get_with_retry(url=url)
    inspect_response(response)

    if not as_text:
        # Set the default to true if the url ends with a text extension
        as_text = any([url.endswith(ext) for ext in text_extensions])

    # Check if the content is gzip-compressed
    if url.endswith("gz"):
        binary_file = BytesIO(response.content)
        with gzip.open(binary_file, 'rb') as f:
            file_content = f.read()
            if as_text:
                file_content = decode_content(file_content)
    else:
        # If we explicitly asked for text or there is an encoding, try to return text
        if as_text:
            file_content = response.text
            # Should get here for jpg and PDFs
        else:
            file_content = response.content

    # Cache the content to S3 (not throttled), but skip for JSON files
    if not is_json_file:
        _cache_file_to_s3(file_content, url, use_s3_cache)

    path = Path(path) if path else None
    if path and path.is_dir():
        path = path / os.path.basename(url)
    return save_or_return_content(file_content, path)


async def download_file_async(client: AsyncClient, url: str, as_text: bool = None, path: Optional[Union[str, Path]] = None, use_s3_cache: Optional[bool] = None) -> Union[
    str, bytes, None]:
    """
    Download a file from a URL asynchronously with S3 caching support.

    Args:
        url (str): The URL of the file to download.
        as_text (bool, optional): Whether to download the file as text or binary.
            If None, the default is determined based on the file extension. Defaults to None.
        path (str or Path, optional): The path where the file should be saved.
        use_s3_cache (bool, optional): Whether to use S3 caching. If None, auto-detect based on S3 configuration.

    Returns:
        str or bytes: The content of the downloaded file, either as text or binary data.
    """
    from .s3_utils import should_use_s3_cache, get_s3_cache_key
    
    if as_text is None:
        # Set the default based on the file extension
        as_text = url.endswith(text_extensions)

    # Determine if S3 cache should be used
    use_cache = should_use_s3_cache(use_s3_cache)

    # Try to get from S3 cache first
    if use_cache:
        try:
            s3_handler = S3FileHandler()
            # Create a cache key based on the URL
            s3_key = get_s3_cache_key(url)
            
            # Check if file exists in S3
            if s3_handler.file_exists(s3_key):
                print(f"Found cached file in S3: {s3_key}")
                cached_content = s3_handler.read_file_content(s3_key)
                if cached_content is not None:
                    if not as_text:
                        cached_content = cached_content.encode('utf-8')
                    path = Path(path) if path else None
                    if path and path.is_dir():
                        path = path / os.path.basename(url)
                    return save_or_return_content(cached_content, path)
        except Exception as e:
            print(f"S3 cache read failed: {e}, falling back to HTTP download")

    # Download from URL if not in cache
    response = await get_with_retry_async(client, url)
    inspect_response(response)

    if as_text:
        # Download as text
        content = response.text
    else:
        # Download as binary
        content = response.content

        # Check if the content is gzip-compressed
        if response.headers.get("Content-Encoding") == "gzip":
            content = gzip.decompress(content)

    # Cache the content to S3
    if use_cache:
        try:
            s3_handler = S3FileHandler()
            s3_key = get_s3_cache_key(url)
            
            # Convert content to string for S3 storage
            cache_content = content
            if isinstance(content, bytes):
                cache_content = content.decode('utf-8', errors='ignore')
            
            s3_handler.upload_content(cache_content, s3_key)
            print(f"Cached file to S3: {s3_key}")
        except Exception as e:
            print(f"S3 cache write failed: {e}")

    path = Path(path) if path else None
    if path and path.is_dir():
        path = path / os.path.basename(url)

    return save_or_return_content(content, path)


CHUNK_SIZE = 4 * 1024 * 1024  # 4MB


@retry(on=RequestError, attempts=attempts, timeout=retry_timeout, wait_initial=wait_initial)
@with_identity
@throttle_requests(requests_per_second=max_requests_per_second)
async def stream_file(url: str,
                      as_text: bool = None,
                      path: Optional[Union[str, Path]] = None,
                      client: Optional[AsyncClient]=None,
                      **kwargs) -> Union[
    str, bytes, None]:
    """
    Download a file from a URL asynchronously with progress bar using httpx.

    Args:
        url (str): The URL of the file to download.
        as_text (bool, optional): Whether to download the file as text or binary.
            If None, the default is determined based on the file extension. Defaults to None.
        path (str or Path, optional): The path where the file should be saved.
        client: The httpx.AsyncClient instance

    Returns:
        str or bytes: The content of the downloaded file, either as text or binary data.
    """
    if as_text is None:
        # Set the default based on the file extension
        as_text = url.endswith(text_extensions)

    async with async_http_client(client) as async_client:
        async with async_client.stream('GET', url) as response:
            inspect_response(response)
            total_size = int(response.headers.get('Content-Length', 0))

            if as_text:
                # Download as text
                content = await response.text()
                return content
            else:
                # Download as binary
                content = b''
                progress_bar = tqdm(
                    total=total_size / (1024 * 1024),
                    unit='MB',
                    unit_scale=True,
                    unit_divisor=1024,
                    leave=False,  # Force horizontal display
                    position=0,  # Lock the position
                    dynamic_ncols=True,  # Adapt to terminal width
                    bar_format='{l_bar}{bar}| {n:.2f}/{total:.2f}MB [{elapsed}<{remaining}, {rate_fmt}]',
                    desc=f"Downloading {os.path.basename(url)}",
                    ascii=False
                )
                downloaded = 0
                async for chunk in response.aiter_bytes(chunk_size=CHUNK_SIZE):
                    content += chunk
                    downloaded += len(chunk)
                    progress_bar.update(len(chunk) / (1024 * 1024))  # Update in MB
                progress_bar.close()

                # Check if the content is gzip-compressed
                if response.headers.get("Content-Encoding") == "gzip":
                    content = gzip.decompress(content)

            if path:
                if isinstance(path, str):
                    path = Path(path)
                if path.is_dir():
                    path = path / os.path.basename(url)

            return save_or_return_content(content, path)


def download_json(data_url: str, use_s3_cache: Optional[bool] = None) -> dict:
    """
    Download JSON data from a URL with S3 caching support.

    Args:
        data_url (str): The URL of the JSON data to download.
        use_s3_cache (bool, optional): Whether to use S3 caching. If None, auto-detect based on S3 configuration.

    Returns:
        dict: The parsed JSON data.
    """
    content = download_file(data_url, as_text=True, use_s3_cache=use_s3_cache)
    return json.loads(content)


def download_text(url: str, use_s3_cache: Optional[bool] = None) -> Optional[str]:
    """
    Download text content from a URL with S3 caching support.

    Args:
        url (str): The URL to download text from.
        use_s3_cache (bool, optional): Whether to use S3 caching. If None, auto-detect based on S3 configuration.

    Returns:
        Optional[str]: The text content or None if failed.
    """
    return download_file(url, as_text=True, use_s3_cache=use_s3_cache)


async def download_json_async(client: AsyncClient, data_url: str, use_s3_cache: Optional[bool] = None) -> dict:
    """
    Download JSON data from a URL asynchronously with S3 caching support.

    Args:
        client (AsyncClient): The HTTP client to use.
        data_url (str): The URL of the JSON data to download.
        use_s3_cache (bool, optional): Whether to use S3 caching. If None, auto-detect based on S3 configuration.

    Returns:
        dict: The parsed JSON data.
    """
    content = await download_file_async(client=client, url=data_url, as_text=True, use_s3_cache=use_s3_cache)
    return json.loads(content)


def download_text_between_tags(url: str, tag: str):
    """
    Download the content of a URL and extract the text between the tags
    This is mainly for reading the header of a filing

    :param url: The URL to download
    :param tag: The tag to extract the content from

    """
    tag_start = f'<{tag}>'
    tag_end = f'</{tag}>'
    is_header = False
    content = ""

    for response in stream_with_retry(url):
        for line in response.iter_lines():
            if line:
                # If line matches header_start, start capturing
                if line.startswith(tag_start):
                    is_header = True
                    continue  # Skip the current line as it's the opening tag

                # If line matches header_end, stop capturing
                elif line.startswith(tag_end):
                    break

                # If within header lines, add to header_content
                elif is_header:
                    content += line + '\n'  # Add a newline to preserve original line breaks
    return content




logger = logging.getLogger(__name__)


@retry(on=RequestError, attempts=attempts, timeout=retry_timeout, wait_initial=wait_initial)
async def download_bulk_data(url: str,
                             data_directory: Path = get_edgar_data_directory(),
                             client: Optional[AsyncClient] = None) -> Path:
    """
    Download and extract bulk data from zip or tar.gz archives

    Args:
        client: The httpx.AsyncClient instance
        url: URL to download from (e.g. "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip")
        data_directory: Base directory for downloads

    Returns:
        Path to the directory containing the extracted data

    Raises:
        ValueError: If the URL or filename is invalid
        IOError: If there are file system operation failures
        zipfile.BadZipFile: If the downloaded zip file is corrupted
        tarfile.TarError: If the downloaded tar.gz file is corrupted
    """
    if not url:
        raise ValueError("Data URL cannot be empty")

    filename = os.path.basename(url)
    if not filename:
        raise ValueError("Invalid URL - cannot extract filename")

    local_dir = filename.split('.')[0]
    download_path = data_directory / local_dir
    download_filename = download_path / filename

    try:
        # Create the directory with parents=True and exist_ok=True to avoid race conditions
        download_path.mkdir(parents=True, exist_ok=True)

        # Download the file
        try:
            await stream_file(url, client=client, path=download_path)
        except Exception as e:
            raise IOError(f"Failed to download file: {e}")

        # Extract based on file extension
        try:
            if filename.endswith(".zip"):
                with zipfile.ZipFile(download_filename, 'r') as z:
                    # Calculate total size for progress bar
                    total_size = sum(info.file_size for info in z.filelist)
                    extracted_size = 0
                    
                    with tqdm(total=total_size, unit='B', unit_scale=True, desc="Extracting") as pbar:
                        for info in z.filelist:
                            z.extract(info, download_path)
                            extracted_size += info.file_size
                            pbar.update(info.file_size)
                            
            elif any(filename.endswith(ext) for ext in (".tar.gz", ".tgz")):
                with tarfile.open(download_filename, 'r:gz') as tar:
                    # Security check for tar files to prevent path traversal
                    def is_within_directory(directory: Path, target: Path) -> bool:
                        try:
                            return os.path.commonpath([directory, target]) == str(directory)
                        except ValueError:
                            return False

                    members = tar.getmembers()
                    total_size = sum(member.size for member in members)
                    
                    with tqdm(total=total_size, unit='B', unit_scale=True, desc="Extracting") as pbar:
                        for member in members:
                            # Check for path traversal
                            member_path = os.path.join(str(download_path), member.name)
                            if not is_within_directory(Path(str(download_path)), Path(member_path)):
                                raise ValueError(f"Attempted path traversal in tar file: {member.name}")
                            
                            # Extract file and update progress
                            tar.extract(member, str(download_path))
                            pbar.update(member.size)
            else:
                raise ValueError(f"Unsupported file format: {filename}")

        except (zipfile.BadZipFile, tarfile.TarError) as e:
            raise type(e)(f"Failed to extract archive {filename}: {e}")

        finally:
            # Always try to clean up the archive file, but don't fail if we can't
            try:
                if download_filename.exists():
                    download_filename.unlink()
            except Exception as e:
                print(f"Failed to delete archive file {download_filename}: {e}")

        return download_path

    except Exception:
        # Clean up the download directory in case of any errors
        try:
            if download_path.exists():
                shutil.rmtree(download_path)
        except Exception as cleanup_error:
            logger.error(f"Failed to clean up after error: {cleanup_error}")
        raise



def download_datafile(data_url: str, local_directory:Path=None) -> Path:
    """Download a file to the local storage directory"""
    filename = os.path.basename(data_url)
    # Create the directory if it doesn't exist
    local_directory = local_directory or get_edgar_data_directory()
    if not local_directory.exists():
        local_directory.mkdir()

    download_filename = local_directory / filename
    download_file(data_url, path=download_filename)
    return download_filename
