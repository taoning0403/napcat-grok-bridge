import ipaddress
import socket
from urllib.parse import urlsplit


def validate_image_url(url: str) -> str:
    url = url.strip()
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
    except ValueError as error:
        raise ValueError("image must use a public HTTPS URL") from error
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise ValueError("image must use a public HTTPS URL")
    try:
        addresses = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    except (OSError, ValueError) as error:
        raise ValueError("image URL host could not be resolved") from error
    if not addresses:
        raise ValueError("image URL must resolve only to public addresses")
    for item in addresses:
        try:
            address = ipaddress.ip_address(item[4][0])
        except ValueError as error:
            raise ValueError("image URL must resolve only to public addresses") from error
        if not address.is_global:
            raise ValueError("image URL must resolve only to public addresses")
    return url
