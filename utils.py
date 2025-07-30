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

def sanitize_filename(filename: str) -> str:
    """Sanitize filename for safe file system usage"""
    # Remove or replace invalid characters
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    # Remove leading/trailing spaces and dots
    filename = filename.strip(' .')
    # Limit length
    if len(filename) > 50:
        filename = filename[:50]
    
    return filename or "converted_pack"

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
    from config import BOT_USERNAME
    
    if user.username:
        return f"@{user.username}"
    elif user.first_name:
        return user.first_name
    elif user.id:
        return user.id
    else:
        return BOT_USERNAME

def is_valid_sticker_url(url: str) -> bool:
    """Check if URL is a valid Telegram sticker or emoji pack URL"""
    return extract_pack_name_from_url(url) is not None

def estimate_wait_time(sticker_documents: list, num_packs: Optional[int]) -> str:
    """
    Calculates a detailed estimated wait time based on the type of each sticker/emoji.
    """
    total_seconds = 0

    # Time for processing each sticker/emoji
    for doc in sticker_documents:
        if doc.mime_type == 'application/x-tgsticker':  # TGS file
            total_seconds += 2
        elif doc.mime_type == 'video/webm':  # WebM
            total_seconds += 1
        elif doc.mime_type == 'image/webp': # WebP
            total_seconds += 0.1
        else: # Others if any we prbbly wont get any
            total_seconds += 1

    if num_packs:
        total_seconds += 10*num_packs
    # Format the final string
    if total_seconds < 60:
        return f"{round(total_seconds)} seconds"
    else:
        minutes = round(total_seconds / 60)
        return f"{minutes} minute(s)"
