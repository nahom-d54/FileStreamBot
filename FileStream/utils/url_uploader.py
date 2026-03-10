import mimetypes
import os
import re
from urllib.parse import unquote, urlparse

import aiofiles
import aiohttp

MAX_TG_UPLOAD_SIZE = 1.98 * 1024 * 1024 * 1024  # 1.98 GB in bytes


def _sanitize_filename(name: str) -> str:
    """Remove unsafe characters from filename."""
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    return name[:200] if name else "file"


def _extract_filename(
    url: str, content_disposition: str | None, content_type: str | None
) -> str:
    if content_disposition:
        match = re.search(
            r'filename\*?=["\']?(?:UTF-8\'\')?([^"\';]+)',
            content_disposition,
            re.IGNORECASE,
        )
        if match:
            return _sanitize_filename(unquote(match.group(1).strip()))

    path = urlparse(url).path
    basename = os.path.basename(path)
    if basename and "." in basename:
        return _sanitize_filename(unquote(basename))

    ext = mimetypes.guess_extension(content_type or "") or ""
    return f"download{ext}" if ext else "download"


async def download_from_url(url: str, download_dir: str, progress_callback=None):
    """
    Download a file from a URL with size validation.
    Returns (file_path, file_name, file_size, mime_type) or raises an exception.
    """
    os.makedirs(download_dir, exist_ok=True)
    timeout = aiohttp.ClientTimeout(total=3600, connect=30)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, allow_redirects=True) as resp:
            if resp.status != 200:
                raise ValueError(f"Failed to download: HTTP {resp.status}")

            content_length = resp.content_length
            if content_length and content_length > MAX_TG_UPLOAD_SIZE:
                size_gb = content_length / (1024**3)
                raise ValueError(
                    f"File too large ({size_gb:.2f} GB). Telegram max upload is 1.98 GB."
                )

            content_type = resp.content_type
            content_disposition = resp.headers.get("Content-Disposition")
            file_name = _extract_filename(url, content_disposition, content_type)
            file_path = os.path.join(download_dir, file_name)

            downloaded = 0
            async with aiofiles.open(file_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 1024):
                    downloaded += len(chunk)
                    if downloaded > MAX_TG_UPLOAD_SIZE:
                        await f.close()
                        os.remove(file_path)
                        raise ValueError(
                            "File too large (>1.98 GB). Telegram max upload is 1.98 GB."
                        )
                    await f.write(chunk)
                    if progress_callback:
                        await progress_callback(downloaded, content_length)

            mime = (
                content_type
                or mimetypes.guess_type(file_name)[0]
                or "application/octet-stream"
            )
            return file_path, file_name, downloaded, mime
