import os
import asyncio
import logging
from typing import List, Optional, Any
from telethon import TelegramClient
from telethon.tl.types import InputStickerSetShortName, InputStickerSetID, TypeInputStickerSet, Document
from telethon.errors.rpcerrorlist import StickersetInvalidError
from telethon.tl.functions.messages import GetStickerSetRequest
from src.core.exceptions import FileUploadTimeoutError, FileUploadWrapperError
from src.utils.formatters import format_file_size
from src.core.config import (DOWNLOAD_TIMEOUT, UPLOAD_TIMEOUT, MAX_DOWNLOAD_RETRIES, MAX_UPLOAD_RETRIES)

logger = logging.getLogger(__name__)

class NetworkTask:
    def __init__(self, client: TelegramClient):
        self.client = client

    # to get the sticker object (information, its not downloading the pack), it is called in the handle_message
    async def get_sticker_set(self, pack_input: Any, access_hash: int | None = None):
        """
        Get sticker/emoji set from Telegram using either a short name (str) 
        or a concrete InputStickerSet type (like InputStickerSetID).
        """
        try:
            input_set = None
            if isinstance(pack_input, str):
                # If we get a string, we assume it's a short_name
                input_set = InputStickerSetShortName(short_name=pack_input)
            elif isinstance(pack_input, int) and access_hash:
                input_set = InputStickerSetID(id=pack_input, access_hash=access_hash)
            elif isinstance(pack_input, TypeInputStickerSet):
                # If we get a valid InputStickerSet object, use it directly
                input_set = pack_input
            else:
                logger.error(f"Invalid type provided for pack: {type(pack_input)}")
                return None
            sticker_set = await self.client(GetStickerSetRequest(
                stickerset=input_set,
                hash=0
            ))
            return sticker_set
        except StickersetInvalidError:
            logger.error(f"The set '{pack_input}' is invalid or does not exist.")
            return None
        except Exception as e:
            logger.error(f"Failed to get set {pack_input}: {e}")
            return None


    async def download_sticker(self, sticker: Document, temp_dir: str) -> Optional[str]:
        """
        Download a single sticker or emoji file with a retry mechanism.
        """
        file_path = os.path.join(temp_dir, f"sticker_{sticker.id}")
        max_retries = MAX_DOWNLOAD_RETRIES
        
        for attempt in range(max_retries):
            try:
                downloaded_path = await asyncio.wait_for(
                    self.client.download_media(sticker, file=file_path),
                    timeout=DOWNLOAD_TIMEOUT
                )
                if downloaded_path:
                    logger.info(f"Successfully downloaded item {sticker.id} to {downloaded_path} on attempt {attempt + 1}")
                    return downloaded_path
            except asyncio.TimeoutError:
                logger.warning(f"Timeout on attempt {attempt + 1}/{max_retries} while downloading sticker {sticker.id}.")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 * (attempt + 1))  # Wait 2, 4 seconds before retrying
                else:
                    logger.error(f"Failed to download sticker {sticker.id} after {max_retries} attempts due to timeout.")
                    return None
            except Exception as e:
                logger.error(f"Failed to download item {sticker.id} on attempt {attempt + 1} with error: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1) # 1 sec wait for other error 
                else:
                    logger.error(f"All {max_retries} retries failed for sticker {sticker.id}.")
                    return None
        return None

    async def _upload_worker(self, file_path: str, index: int, timeout: int):
        """
        Worker task that uploads a file's bytes with a dedicated timeout and returns a handle.
        """
        try:
            for attempt in range(MAX_UPLOAD_RETRIES):
                try:
                    # upload temporarily with timeout
                    return await asyncio.wait_for(
                        self.client.upload_file(file_path),
                        timeout=timeout
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"Upload of {file_path} timed out on attempt {attempt + 1}/{MAX_UPLOAD_RETRIES}.")
                    if attempt == MAX_UPLOAD_RETRIES -1:
                        raise
                    wait_time = 2 * (attempt + 1)
                    await asyncio.sleep(wait_time)
                    pass
        except asyncio.TimeoutError:
            # raise our detailed one
            raise FileUploadTimeoutError(
                f"Timeout during the initial upload phase of {file_path}",
                index=index,
                file_path=file_path
            )
        except Exception as e:
            # our detailed one again :)
            raise FileUploadWrapperError(
                f"Error during the initial upload phase of {file_path}: {e}",
                index=index,
                file_path=file_path,
                original_exception=e
            ) from e
        
    async def upload_files(self, file_paths: List[str], pack_url: str, pack_title: str, chat_id: int):
        """
        Uploads files using a two-phase approach with robust, dynamic timeouts and a TaskGroup.
        """
        num_files = len(file_paths)
        timeout = (num_files * UPLOAD_TIMEOUT)

        file_handles = [None] * num_files # this will hold results in order
        tasks = []

        try:
            # ------ Phase 1: Parallel Upload  --------------
            async with asyncio.TaskGroup() as tg:
                for i, file_path in enumerate(file_paths):
                    # Create a task for each file upload
                    task = tg.create_task(self._upload_worker(file_path, i, timeout=timeout))
                    tasks.append(task)
        except* (FileUploadTimeoutError, FileUploadWrapperError) as eg:
            # We re raise the group to be handled by bot_handlers.py
            raise

        # If done successfully, get results from the ordered task list
        for i, task in enumerate(tasks):
            file_handles[i] = task.result()


        # ----- Phase 2: Sequential Sending (to maintain order yk)--------------------
        messages = []
        for i, handle in enumerate(file_handles):
            if handle is None: return [] # extra safety, but there shouldnt be any, but nvm lets do it
            file_path = file_paths[i]
            caption = f"<tg-emoji emoji-id='5785045099142450328'>📦</tg-emoji> <a href=\"{pack_url}\">{pack_title}</a> - Part {i+1}/{num_files}\nSize: {format_file_size(os.path.getsize(file_path))}"
            try:
                message = await asyncio.wait_for(
                    self.client.send_file(chat_id, handle, caption=caption, link_preview=False, parse_mode='html'),
                    timeout=10
                )
                messages.append(message)
            except asyncio.TimeoutError:
                raise FileUploadTimeoutError(
                    f"Timeout during the final send phase of file: {file_path}",
                    index=i,
                    file_path=file_path
                )
            except Exception as e:
                raise FileUploadWrapperError(
                    f"Error during the final send phase of file {file_path}: {e}",
                    index=i,
                    file_path=file_path,
                    original_exception=e
                ) from e
        return messages

