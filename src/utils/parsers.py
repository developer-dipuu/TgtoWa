import re
import regex
import emoji
from typing import Optional

def extract_pack_name_from_url(url: str) -> Optional[str]:
    """Extract sticker/emoji pack name from Telegram URL"""
    patterns = [
        r't\.me/addstickers/(.+)',
        r'telegram\.me/addstickers/(.+)',
        r'addstickers/(.+)',
        r't\.me/addemoji/(.+)',
        r'telegram\.me/addemoji/(.+)',
        r'addemoji/(.+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    return None

def is_valid_sticker_url(url: str) -> bool:
    """Check if URL is a valid Telegram sticker or emoji pack URL"""
    return extract_pack_name_from_url(url) is not None

def sanitize_filename(filename: str) -> str:
    """Sanitize filename for safe file system usage
    Also to avoid telegram renaming files if finds non ascii or non emoji characters.
    """
    # Remove or replace invalid characters
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    # Remove leading/trailing spaces and dots
    filename = filename.strip(' .')

    # replace non ascii and non emoji characters by _ but only once for consecutive ones
    new_filename = ""
    for character in regex.findall(r'\X', filename):
        if character.isascii() or emoji.is_emoji(character):
            new_filename += character
        else:
            if not new_filename.endswith("_"):
                new_filename += "_"

    # combination of space and underscore to single underscore
    new_filename = re.sub(r'[_\s]*_[_\s]*', '_', new_filename).strip("_")
    
    # two or more space to single space
    new_filename = re.sub(r'\s{2,}', ' ', new_filename)

    # Limit length
    if len(new_filename) > 30:
        new_filename = new_filename[:30]

    return new_filename or "Converted_pack"