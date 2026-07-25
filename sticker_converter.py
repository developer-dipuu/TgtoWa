"""
Sticker conversion module for the Bot.

It downloads stickers from the given set, converts them to webp format using various modules, 
and creates .wastickers files with the converted stickers. 
It also creates icon and metadata files for the sticker pack.
"""

import os
import zipfile
import asyncio
from typing import List, Optional
from PIL import Image
import logging
from telethon import TelegramClient
from telethon.tl.types import Document

from config import *
from utils import *

logger = logging.getLogger(__name__)

class StickerConverter:
    def __init__(self, client: TelegramClient):
        self.client = client
        self.network_task = NetworkTask(self.client)

    
    async def _run_conversion_process(self, script_name: str, input_path: str, output_path: str) -> bool:
        """Run conversion script in a separate process with timeout."""
        import sys
        
        script_path = os.path.join(os.path.dirname(__file__), script_name)
        
        # Build command: python script.py input output --width 512 ...
        
        try:
            args = [
                script_path,
                input_path,
                output_path,
                "--width", str(STICKER_DIMENSIONS[0]),
                "--height", str(STICKER_DIMENSIONS[1]),
                "--quality", str(WEBP_QUALITY)
                ]

            if script_name in {'video_to_webp.py', 'tgs_to_webp.py'}:
                args.extend([
                    "--max-frames", str(MAX_WEBP_FRAMES),
                    "--max-size", str(MAX_WEBP_SIZE_KB),
                    "--fast"
                    ])

            process = await asyncio.create_subprocess_exec(sys.executable, *args)
            
            try:
                # Wait for the process to finish with a timeout
                await asyncio.wait_for(process.communicate(), timeout=CONVERSION_TIMEOUT)
                
                if process.returncode == 0:
                    return True
                else:
                    logger.error(f"Conversion process failed with return code {process.returncode}")
                    return False
                    
            except asyncio.TimeoutError:
                logger.error(f"Conversion process timed out after {CONVERSION_TIMEOUT}s: {input_path}")
                try:
                    process.kill()
                    await process.wait() 
                except Exception:
                    pass
                return False
                
        except Exception as e:
            logger.error(f"Failed to run conversion process: {e}")
            return False

    async def convert_to_webp(self, input_path: str, output_path: str) -> bool:
        """Convert various sticker/emoji formats to WebP."""
        try:
            file_ext = os.path.splitext(input_path)[1].lower() if input_path else ''
            
            script_path = None
            if file_ext == '.tgs':
                script_path = 'tgs_to_webp.py'
            elif file_ext in ['.webm', '.mp4', '.gif', '.mov', '.mkv']:
                script_path = 'video_to_webp.py'
            
            if script_path:
                 return await self._run_conversion_process(script_path, input_path, output_path)
            else: # Static image
                 return await self._run_conversion_process('image_to_webp.py', input_path, output_path)

        except Exception as e:
            logger.error(f"Failed to convert {input_path} to WebP: {e}")
            return False
    
    # it is the one who creates all wastickers file for a sticker pack  by calling other helpers and return to the caller _run_conversion in bot_handlers
    async def create_wastickers_pack(self, sticker_set, author_name: str, custom_title: Optional[str] = None) -> List[str]:
        """Create .wastickers file(s) from a sticker/emoji set."""
        wastickers_files = []
        temp_dir = create_temp_directory()
        
        try:
            stickers = sticker_set.documents
            total_stickers = len(stickers)
            num_packs = (total_stickers + MAX_STICKERS_PER_PACK - 1) // MAX_STICKERS_PER_PACK
            base_pack_title = custom_title if custom_title and custom_title.strip() else sticker_set.set.title

            sanitized_base_filename = sanitize_filename(custom_title if custom_title and custom_title.strip() else sticker_set.set.title)
            for pack_idx in range(num_packs):
                start_idx = pack_idx * MAX_STICKERS_PER_PACK
                end_idx = min(start_idx + MAX_STICKERS_PER_PACK, total_stickers)
                pack_stickers = stickers[start_idx:end_idx]

                pack_title = base_pack_title
                final_filename = sanitized_base_filename

                if num_packs > 1:
                    pack_title += f" (part {pack_idx + 1})"
                    final_filename += f" (part {pack_idx + 1})"
                
                wastickers_file = await self._create_single_wastickers_pack(
                    pack_stickers, 
                    pack_title,  # Use the full title for metadata (sticker title in whatsapp)
                    author_name, 
                    temp_dir, 
                    pack_idx + 1,
                    final_filename  # Pass the unique filename
                )
                if wastickers_file:
                    wastickers_files.append(wastickers_file)
            
            return wastickers_files
        except Exception as e:
            logger.error(f"Failed to create wastickers pack: {e}")
            return []
        finally:
            cleanup_temp_directory(temp_dir)

    # creates a single .wastcker file and retuen it to the caller create_wastickers_pack
    # it is the one who calls the download_sticker, convert_to_webp, _create_icon, _create_metadata_files, _create_wastickers_archive
    async def _create_single_wastickers_pack(self, stickers: List[Document], title: str, 
                                           author_name: str, temp_dir: str, pack_number: int,
                                           file_name: str) -> Optional[str]:
        """Create a single .wastickers file."""
        pack_temp_dir = os.path.join(temp_dir, f"pack_{pack_number}")
        os.makedirs(pack_temp_dir, exist_ok=True)
        
        try:
            download_tasks = [self.network_task.download_sticker(sticker, pack_temp_dir) for sticker in stickers]
            downloaded_sticker_paths = await asyncio.gather(*download_tasks)

            converted_stickers = []
            for i, sticker_file in enumerate(downloaded_sticker_paths):
                if not sticker_file:
                    continue

                webp_path = os.path.join(pack_temp_dir, f"{i+1:02d}.webp")
                
                if await self.convert_to_webp(sticker_file, webp_path):
                    converted_stickers.append(webp_path)
                
                # Clean up the original downloaded file
                if os.path.exists(sticker_file):
                    os.remove(sticker_file)
            
            if not converted_stickers:
                return None
            
            await self._create_icon(converted_stickers[0], os.path.join(pack_temp_dir, "icon.png"))
            await self._create_metadata_files(pack_temp_dir, title, author_name)
            
            output_file = os.path.join(OUTPUT_DIR, f"{file_name}.wastickers")
            await self._create_wastickers_archive(pack_temp_dir, output_file)
            return output_file
        except Exception as e:
            logger.error(f"Failed to create single wastickers pack: {e}")
            return None
        
    async def _create_icon(self, first_sticker_path: str, icon_path: str):
        """Create icon.png from the first sticker/emoji."""
        try:
            with Image.open(first_sticker_path) as img:
                if img.mode != 'RGBA':
                    img = img.convert('RGBA')
                img.thumbnail(ICON_DIMENSIONS, Image.Resampling.LANCZOS)
                img.save(icon_path, 'PNG')
        except Exception as e:
            logger.error(f"Failed to create icon: {e}")

    async def _create_metadata_files(self, pack_dir: str, title: str, author_name: str):
        """Create author.txt and title.txt files."""
        with open(os.path.join(pack_dir, "author.txt"), 'w', encoding='utf-8') as f:
            f.write(author_name)
        with open(os.path.join(pack_dir, "title.txt"), 'w', encoding='utf-8') as f:
            f.write(title)

    async def _create_wastickers_archive(self, pack_dir: str, output_file: str):
        """Create the final .wastickers archive."""
        with zipfile.ZipFile(output_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(pack_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arc_name = os.path.relpath(file_path, pack_dir)
                    zf.write(file_path, arc_name)
