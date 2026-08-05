"""
Static Image to WebP Converter Module

Converts static images (PNG, JPG, etc.) to WebP format.
"""

import argparse
import sys
import logging
from PIL import Image

logger = logging.getLogger(__name__)

def convert_image_to_webp(input_path: str, output_path: str, 
                         width: int = 512, height: int = 512, 
                         quality: int = 80) -> bool:
    """
    Convert a static image to WebP with resizing and padding.
    """
    try:
        with Image.open(input_path) as img:
            if img.mode != 'RGBA':
                img = img.convert('RGBA')
            
            # Calculate target dimensions
            target_size = (width, height)
            
            # Resize image maintaining aspect ratio
            img.thumbnail(target_size, Image.Resampling.LANCZOS)
            
            # Create a new transparent image with the target dimensions
            new_img = Image.new('RGBA', target_size, (0, 0, 0, 0))
            
            # Calculate position to center the image
            x = (target_size[0] - img.width) // 2
            y = (target_size[1] - img.height) // 2
            
            # Paste the resized image onto the center of the canvas
            new_img.paste(img, (x, y), img)
            
            # Save as WebP
            new_img.save(output_path, 'WEBP', quality=quality)
            
        logger.info("Successfully converted %s to %s", input_path, output_path)
        return True
    except Exception as e:
        logger.error("Failed to convert %s: %s", input_path, e)
        return False

if __name__ == "__main__":
    import pathlib
    
    # Configure logging
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO,
        stream=sys.stdout
    )
    logger_name = pathlib.Path(__file__).stem
    logger = logging.getLogger(logger_name)

    parser = argparse.ArgumentParser(
        description="Convert static images to WebP.",
        formatter_class=argparse.RawTextHelpFormatter
    )

    # Required positional arguments
    parser.add_argument("input_file", help="Path to the input image file.")
    parser.add_argument("output_file", help="Path for the output WebP file.")

    # Optional arguments with default values
    parser.add_argument("--width", type=int, default=512, help="Output width in pixels. Default: 512.")
    parser.add_argument("--height", type=int, default=512, help="Output height in pixels. Default: 512.")
    parser.add_argument("--quality", type=int, default=80, help="WebP quality (0-100). Default: 80.")

    args = parser.parse_args()

    success = convert_image_to_webp(
        input_path=args.input_file,
        output_path=args.output_file,
        width=args.width,
        height=args.height,
        quality=args.quality
    )

    if not success:
        sys.exit(1)
