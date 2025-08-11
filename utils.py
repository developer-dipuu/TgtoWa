"""
Utility functions for the Telegram Sticker/Emoji to WhatsApp Sticker Converter Bot
"""

import os
import re
import tempfile
import shutil
from typing import Optional, Tuple
from PIL import Image
import io
import emoji
import regex

def ensure_directories():
    """Create necessary directories if they don't exist"""
    from config import TEMP_DIR, OUTPUT_DIR
    
    os.makedirs(TEMP_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

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

# we dont use it as images are already samll in size maybe we need it in future lets see
def optimize_image_size(image_data: bytes, max_size: int, dimensions: Tuple[int, int]) -> bytes:
    """Optimize image to meet size constraints while maintaining quality"""
    img = Image.open(io.BytesIO(image_data))
    
    # Convert to RGBA if not already
    if img.mode != 'RGBA':
        img = img.convert('RGBA')
    
    # resize to target dimensions
    img = img.resize(dimensions, Image.Resampling.LANCZOS)
    
    # Try different quality settings to meet size cap
    for quality in range(95, 10, -5):
        output = io.BytesIO()
        img.save(output, format='WEBP', quality=quality, optimize=True)
        
        if output.tell() <= max_size:
            return output.getvalue()
    
    # If still too large try with minimal quality
    output = io.BytesIO()
    img.save(output, format='WEBP', quality=10, optimize=True)
    return output.getvalue()

def create_temp_directory() -> str:
    """Create a temporary directory for processing"""
    from config import TEMP_DIR
    return tempfile.mkdtemp(dir=TEMP_DIR)

def cleanup_temp_directory(temp_dir: str):
    """Clean up temporary directory"""
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)

# to skip telegram renaming files if finds non ascii or non emoji characters
def sanitize_filename(filename: str) -> str:
    """Sanitize filename for safe file system usage"""
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


def format_file_size(size_bytes: int) -> str:
    """Format file size in human readable format"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"

def get_user_display_name(user) -> str:
    """Get user display name with fallback"""
    
    if user.username:
        return f"@{user.username}"
    elif user.first_name:
        return user.first_name
    else :
        return user.id


def is_valid_sticker_url(url: str) -> bool:
    """Check if URL is a valid Telegram sticker or emoji pack URL"""
    return extract_pack_name_from_url(url) is not None

def estimate_wait_time(sticker_documents: list, num_packs: Optional[int]) -> float:
    """
    Calculates estimated wait time in seconds based on the type of each sticker/emoji.
    """
    total_seconds = 0.0

    # Time for processing each sticker/emoji
    for doc in sticker_documents:
        if doc.mime_type == 'application/x-tgsticker':  # TGS file
            total_seconds += 2
        elif doc.mime_type == 'video/webm':  # WebM
            total_seconds += 1
        elif doc.mime_type == 'image/webp': # WebP
            total_seconds += 0.1
        else: # Others if any, we prbbly wont get any
            total_seconds += 1

    """Uncomment this part if you want to add some more logic to estimated time based on number of packs it will create
    Like if your machine has delays in downloading packs so you may add 10 sec for each pack to the estimated time"""
    # if num_packs:
    #     total_seconds += 10*num_packs
    
    return total_seconds