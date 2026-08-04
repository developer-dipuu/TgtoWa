import io
import os
import shutil
import tempfile
from PIL import Image
from typing import Tuple
from src.core.config import TEMP_DIR, OUTPUT_DIR

def ensure_directories():
    """Create necessary directories if they don't exist"""
    os.makedirs(TEMP_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

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
    return tempfile.mkdtemp(dir=TEMP_DIR)

def cleanup_temp_directory(temp_dir: str):
    """Clean up temporary directory"""
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)


