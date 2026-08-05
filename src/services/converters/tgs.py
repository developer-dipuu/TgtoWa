"""
TGS to WebP Converter Module

A simple Python module for converting TGS (Telegram animated stickers) to WebP format.
TGS files are gzip-compressed Lottie JSON animations.
"""

import os
import math
import logging
import io
import webp
import time
from PIL import Image
import rlottie_python as rlottie

logger = logging.getLogger(__name__)

class TGSToWebPConverter:
    """Converter class for TGS to WebP conversion with automatic timing preservation."""
    
    def __init__(self, width: int = -1, height: int = -1, quality: int = 40,
                frame_cap: bool = True, max_frames: int = 180, max_size: int = None,
                keep_aspect: bool = True, allow_upscale: bool = True, pad: bool = True,
                fps: float = 30.0, preserve_timing: bool = True, compress_faster: bool = False):
        """
        Initialize the converter with input validation.
        """

        # ---- Input Validation ----
        
        # height and width, can be -1 or 1 to 8192(8k)
        if not isinstance(width, int) or (width <= 0 and width != -1) or width > 8192:
            raise ValueError(f"Width must be -1 or a positive integer up to 8192. Got: {width}")
        if not isinstance(height, int) or (height <= 0 and height != -1) or height > 8192:
            raise ValueError(f"Height must be -1 or a positive integer up to 8192. Got: {height}")
        
        # quality (0-100)
        if not isinstance(quality, int) or not (0 <= quality <= 100):
            raise ValueError(f"Quality must be an integer between 0 and 100. Got: {quality}")
        
        # max_frames (1 to inf)
        if not isinstance(max_frames, int) or max_frames <= 0:
            raise ValueError(f"Max frames must be a positive integer. Got: {max_frames}")
        
        # max_size (1 to inf)
        if max_size is not None and (not isinstance(max_size, int) or max_size <= 0):
            raise ValueError(f"Max size must be a positive number or None. Got: {max_size}")
            
        # fps (0 to 120), greater than 120 leads to some issues
        if not isinstance(fps, (int, float)) or fps <= 0 or fps > 120:
            raise ValueError(f"FPS must be a positive number up to 120. Got: {fps}")

        # Booleans
        for name, val in [('frame_cap', frame_cap), ('keep_aspect', keep_aspect), 
                          ('allow_upscale', allow_upscale), ('pad', pad), 
                          ('preserve_timing', preserve_timing), ('compress_faster', compress_faster)]:
            if not isinstance(val, bool):
                raise TypeError(f"'{name}' must be strictly a boolean. Got: {type(val).__name__}")

        # ---- storing validated values ----
        
        self.width = width
        self.height = height
        self.quality = quality
        self.frame_cap = frame_cap
        self.max_frames = max_frames
        self.max_size = max_size
        self.keep_aspect = keep_aspect
        self.allow_upscale = allow_upscale
        self.pad = pad
        self.fps = fps
        self.preserve_timing = preserve_timing
        self.compress_faster = compress_faster

    @staticmethod
    def _select_indices(total_frames: int, count: int) -> list[int]:
        """
        Selects a specific count of frame indices from a total number of frames.
        """
        if total_frames <= 0 or count <= 0:
            selected_indices = []
        elif count == 1:
            selected_indices = [0]
        elif count >= total_frames:
            selected_indices = list(range(total_frames))
        else:
            selected_indices = [round(i * (total_frames - 1) / (count - 1)) for i in range(count)]
        return selected_indices
    
    def _render_lottie_frame(self, lottie_animation: rlottie.LottieAnimation, frame_num: int) -> Image.Image:
        """
        Renders a single frame from a Lottie animation and resizes it if needed.
        """
        try:
            # render a PIL Image object for current frame
            pil_image = lottie_animation.render_pillow_frame(frame_num=frame_num)
            
            # !!!!!!!!!!!!!!!! Resize if needed !!!!!!!!!!!!!!!!

            orig_w, orig_h = pil_image.size
            target_w = self.width if self.width != -1 else orig_w
            target_h = self.height if self.height != -1 else orig_h
            pad_mode = self.pad

            # if haven't given width and height or given but our image is already of that dimension, we need not to resize
            if (orig_w, orig_h) == (target_w, target_h):
                return pil_image
            
            # =========== Resize with keep aspect ratio ===========
            if self.keep_aspect:
                # ---- calculate scale factor ----
                if pad_mode:
                    # Use min() to fit the entire image inside target box, later we'll pad with transparent pixels
                    scale = min(target_w / orig_w, target_h / orig_h)
                else:
                    # Use max() to cover the whole target box with image even if it overflows, later we'll crop upto target box
                    scale = max(target_w / orig_w, target_h / orig_h)
                
                if not self.allow_upscale and scale > 1.0:
                    scale = 1.0
                    pad_mode = True # Force padding if we can't upscale to crop

                # ----- calculate new height and width using scale factor -----
                if pad_mode:
                    # Make sure new dims are <= target using floor & using min() for rounding overshoot
                    new_w = max(1, int(math.floor(orig_w * scale)))
                    new_h = max(1, int(math.floor(orig_h * scale)))
                    # Using min() for rounding overshoot
                    new_w = min(new_w, target_w)
                    new_h = min(new_h, target_h)
                else:
                    # cover: make sure new dims are >= target using ceil and using max() for rounding overshoot
                    new_w = max(1, int(math.ceil(orig_w * scale)))
                    new_h = max(1, int(math.ceil(orig_h * scale)))
                    # Using max() for rounding overshoot
                    new_w = max(new_w, target_w)
                    new_h = max(new_h, target_h)
                
                # Resize using calculated new height and width
                resized = pil_image.resize((new_w, new_h), Image.LANCZOS)

                # ---------- Finally, Pad or Crop ----------
                if pad_mode:
                    # create a canvas and paste resized image on it
                    canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
                    left = (target_w - new_w) // 2
                    top = (target_h - new_h) // 2
                    # paste the resized image on the canvas starting at (left, top) coordinates
                    canvas.paste(resized, (left, top), resized)
                    final_img = canvas
                else:
                    # Crop mode: crop the resized (overflowed) image to target size
                    left = (new_w - target_w) // 2
                    top = (new_h - target_h) // 2
                    # (left, top, right, bottom) defining the boundaries of the crop box.
                    final_img = resized.crop((left, top, left + target_w, top + target_h))
            
            # ============ resize without keeping aspect ratio ============
            else:
                desired_w, desired_h = target_w, target_h
                if not self.allow_upscale:
                    # clamp each dimension separately so we don't upscale any axis
                    desired_w = min(desired_w, orig_w)
                    desired_h = min(desired_h, orig_h)
                    
                if (desired_w, desired_h) == (orig_w, orig_h):
                    resized = pil_image
                else:
                    resized = pil_image.resize((desired_w, desired_h), Image.LANCZOS)
                
                # Ensure final output is exactly target size by centering resized on transparent canvas when needed
                if (desired_w, desired_h) == (target_w, target_h):
                    final_img = resized
                else:
                    # create a canvas and paste resized image on it
                    canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
                    left = (target_w - desired_w) // 2
                    top = (target_h - desired_h) // 2
                    # paste the resized image on the canvas starting at (left, top) coordinates
                    canvas.paste(resized, (left, top), resized)
                    final_img = canvas
            
            return final_img
                
        except Exception as e:
            logger.warning("Failed to render frame number %d, using fallback: %s", frame_num, e)
            # create a fallback frame with same dimensions as other rendered frames
            try:
                orig_w, orig_h = lottie_animation.lottie_animation_get_size()
                fallback_width = self.width if self.width != -1 else orig_w
                fallback_height = self.height if self.height != -1 else orig_h
                return Image.new('RGBA', (fallback_width, fallback_height), (0, 0, 0, 0))
            except Exception as e2:
                logger.error("Failed to create fallback frame for frame number %d: %s", frame_num, e2)
                raise RuntimeError(f"Failed to create fallback frame for frame number {frame_num}: {e2}")
            

    def _create_webp_buffer(self, frames: list[Image.Image], quality: int, fps: float) -> io.BytesIO:
        """
        Create a WebP animated image buffer from a list of PIL Image frames.

        Args:
            frames: List of PIL Image frames to convert
            quality: WebP quality level (0-100)
            fps: Frames per second for the animation

        Returns:
            io.BytesIO: Buffer containing the animated WebP image
        """
        if not frames:
            raise ValueError("No frames provided to create WebP buffer")
        if quality < 0 or quality > 100:
            raise ValueError("Quality must be between 0 and 100")
        if fps <= 0:
            raise ValueError("FPS must be positive")

        buf = io.BytesIO()  # Create empty buffer

        try:

            w, h = frames[0].size

            if any(img.size != (w, h) for img in frames):
                raise ValueError("All frames must have the same size")

            # Initialize Encoder Options and Encoder
            enc_opts = webp.WebPAnimEncoderOptions.new()
            enc = webp.WebPAnimEncoder.new(w, h, enc_opts)
            
            # Set up quality config
            config = webp.WebPConfig.new(quality=quality)
            
            for i, img in enumerate(frames):
                # Convert PIL image to WebPPicture object
                pic = webp.WebPPicture.from_pil(img)
                # Encode frame with its calculated timestamp
                t = round((i * 1000) / fps)
                enc.encode_frame(pic, t, config)
            
            # Assemble the final animated data
            end_t = round((len(frames) * 1000) / fps)
            anim_data = enc.assemble(end_t)
            
            # Write the raw bytes to buffer
            buf.write(anim_data.buffer())
            
            buf.seek(0)
            return buf
            
        except Exception as e:
            raise RuntimeError(f"Failed to create WebP buffer: {e}") from e

    # Helper to select a subset of frames evenly
    @staticmethod
    def select_frames(frames: list[Image.Image], count: int) -> list[Image.Image]:
        """
        Returns a list of frames selected evenly from the given frames.
        """
        if count <= 0 or len(frames) <= 0:
            return []
        if count == 1:
            return [frames[0]]
        if count >= len(frames):
            return frames
        
        indices = [round(i * (len(frames) - 1) / (count - 1)) for i in range(count)]
        return [frames[i] for i in indices]

    def _binary_search(self, frames: list[Image.Image], target_range: tuple, search_space: tuple, search_type: str) -> tuple[int, int, io.BytesIO] | tuple[None, None, None]:
        """
        Performs a binary search on the given range of frames or quality to find a value that results
        in a size within target range.
        It uses helper functions based on the 'search_type' parameter to evaluate the size for a given value in search_space.

        Args:
            frames: List of frames to search on.
            target_range: A (min, max) tuple for the desired file size range (inclusive).
            search_space: A (min, max) tuple for the frame count or quality range to search in (inclusive).
            search_type: Type of search to be performed. It can be 'frame' or 'quality'.
        Returns:
            A tuple of (best_value, best_size, best_buffer). Returns (None, None, None) if no suitable value is found.
        """

        # helper to get size for specific number of frames from given a list of frames
        def size_4_these_frames(count: int) -> tuple[io.BytesIO, int]:
            """
            Creates webp buffer by selecting a targetted number of frames evenly and returns the buffer and its size in bytes.
            It uses the 'self.final_quality' and 'frames' passed to _binary_search function.
            """
            frames_to_test = self.select_frames(frames, count)
            _fps = len(frames_to_test) / self.original_duration if self.preserve_timing else self.fps
            
            # self.final_quality is updated accordingly inside convert() before calling binary search on frames
            # so we need not to worry about that here
            buffer = self._create_webp_buffer(frames_to_test, self.final_quality, _fps)
            
            return buffer, buffer.getbuffer().nbytes
        
        # helper to get size for quality
        def size_4_this_quality(quality: int) -> tuple[io.BytesIO, int]:
            """
            Creates webp buffer using given quality and returns the buffer and its size in bytes.
            It uses the 'frames' passed to _binary_search function.
            """
            _fps = len(frames) / self.original_duration if self.preserve_timing else self.fps

            buffer = self._create_webp_buffer(frames, quality, _fps)
            
            return buffer, buffer.getbuffer().nbytes

        # search boundaries and best found value
        low, high = search_space
        best_value = None
        best_size = float('inf')
        best_buffer = None

        low, high = int(low), int(high)
        if low > high:
            return None, None, None

        while low <= high:
            mid = (low + high) // 2

            # call either size_4_these_frames or size_4_this_quality
            evaluator_func = size_4_these_frames if search_type == "frame" else size_4_this_quality
            buffer, current_size = evaluator_func(mid)
            # size is within the range
            if target_range[0] <= current_size <= target_range[1]:
                return mid, current_size, buffer
            # size is lower than range minimum, not what we want but can be used if we dont find any under the range
            elif current_size < target_range[0]:
                best_value = mid 
                best_size = current_size
                best_buffer = buffer
                low = mid + 1
            # size is higher than range maximum
            else:
                high = mid - 1
        
        # return the best frames/quality and best size if no ones fall in the size range after all iterations
        if best_value is not None and best_buffer is not None:
            return best_value, best_size, best_buffer
        # if size is higher than range max for all values
        return None, None, None
    


    def convert(self, tgs_path: str, webp_path: str) -> bool:
        """
        Main function to handle the conversion logic
        
        Args:
            tgs_path: Path to input TGS file
            webp_path: Path to output WebP file
            
        Returns:
            True if conversion successful, False otherwise
            
        Raises:
            FileNotFoundError: If TGS file doesn't exist
            ValueError: If failed to extract frames or create WebP buffer
            IOError: If output file cannot be written
        """
        start_time = time.monotonic()
        if not os.path.exists(tgs_path):
            raise FileNotFoundError(f"TGS file not found: {tgs_path}")

        # ========= Parse and Render Original Frames & collect metadata ==========

        try: # Use the rlottie loader
            with rlottie.LottieAnimation.from_tgs(tgs_path) as lottie_animation:

                # Fetch metadata
                original_frame_count = lottie_animation.lottie_animation_get_totalframe()
                if not original_frame_count:
                    raise ValueError("The TGS appears to have no frame or corrupted.")
                
                original_fps = float(lottie_animation.lottie_animation_get_framerate())
                if original_fps <= 0:
                    original_fps = 1.0
                
                self.original_duration = original_frame_count / original_fps

                # ===================== Calculate final frames =====================

                # Frames cap
                max_frames = self.max_frames if self.frame_cap else original_frame_count  

                # select which frames to render
                indices_to_render = self._select_indices(original_frame_count, max_frames)

                # Render all selected frames
                try:
                    final_frames = [self._render_lottie_frame(lottie_animation, i) for i in indices_to_render]
                except Exception as e:
                    raise ValueError(f"Failed to extract frames from video: {e}")

                if not final_frames:
                    raise ValueError("No frames could be rendered from TGS file")
        except ValueError:
            raise
        except Exception as e:
            logger.error("Error while parsing TGS file or unexpected rlottie error: %s", e)
            raise
        
        capped_frames = len(final_frames)

        if self.preserve_timing:
            logger.info("Preserving original duration")
        else:
            logger.info("Original duration will not be preserved. Using custom FPS: %s", self.fps)
        

        # ===================== Create buffer with max frames at given quality =====================
        self.final_quality = self.quality
        _fps = capped_frames / self.original_duration if self.preserve_timing else self.fps
        if _fps <= 0.0: _fps = 1 # Avoid zero
        logger.info("Started processing... Frames: %s, Quality: %s", capped_frames, self.final_quality)
        buffer = self._create_webp_buffer(final_frames, self.final_quality, _fps)


        # ================== if compression is enabled search for best frames and quality ====================
        compress = self.max_size is not None

        if compress:
            size_cap_kb = self.max_size
                
            if self.compress_faster:
                size_target_range = (int(size_cap_kb * 0.75 * 1024) , size_cap_kb * 1024)  # Target [75% of max_size, max_size]
            else:
                size_target_range = (size_cap_kb * 1024, size_cap_kb * 1024)  # Target [max_size, max_size]
            
            logger.info("Aiming for a file size under %sKB.", size_cap_kb)

            current_size = buffer.getbuffer().nbytes

            
            if current_size <= size_target_range[1]: # If it's already under the size limit, no need to compress
                logger.info("Success! Size is %.1fKB. No further optimization needed.", current_size / 1024)
            else:
                logger.info("Too big (%.1fKB). Starting advanced optimization...", current_size / 1024)
                
                # Define search ranges
                frame_range_1 = (max(1, capped_frames //2), capped_frames)
                frame_range_2 = (1, max(1, capped_frames // 2))
                fallback_frame_count = max(1, capped_frames // 2)

                quality_range_1 = (max(0, int(self.quality / 2)), self.quality)
                quality_range_2 = (0, max(0, int(self.quality / 2)))
                fallback_quality = max(0, int(self.quality/2))

                # --- Start the search  ---

                # Stage A: Binary search on frame_range1
                logger.info("Stage A: Searching frame count in [%s, %s] @ Q=%s...", frame_range_1[0], frame_range_1[1], self.final_quality)
                best_f, best_s, best_buff = self._binary_search(final_frames, size_target_range, frame_range_1, 'frame')

                if best_f is not None:
                    buffer = best_buff
                    logger.info("Found solution in Stage A: %s frames, size %.1fKB.", best_f, best_s / 1024)
                else:
                    # Stage B: Binary search on quality_range_1
                    logger.info("Stage B: Too big. Fixing at %s frames. Searching quality in [%s, %s]...", fallback_frame_count, quality_range_1[0], quality_range_1[1])
                    stage_b_frames = self.select_frames(final_frames, fallback_frame_count) # fixed frames to search for quality
                    best_q, best_s, best_buff = self._binary_search(stage_b_frames, size_target_range, quality_range_1, 'quality')

                    if best_q is not None:
                        buffer = best_buff
                        logger.info("Found solution in Stage B: Q=%s, size %.1fKB.", best_q, best_s / 1024)
                    else:
                        # Stage C: Binary search on frame_range_2
                        logger.info("Stage C: Still too big. Fixing quality at %s. Searching frames in [%s, %s]...", fallback_quality, frame_range_2[0], frame_range_2[1])
                        self.final_quality = fallback_quality # update global final quality (binary search uses this, so needs to be updated)
                        best_f, best_s, best_buff = self._binary_search(final_frames, size_target_range, frame_range_2, 'frame')
                        
                        if best_f is not None:
                            buffer = best_buff
                            logger.info("Found solution in Stage C: %s frames, size %.1fKB.", best_f, best_s / 1024)
                        else:
                            # Stage D: Binary search on quality_range_2
                            logger.info("Stage D: Last resort! Fixing at %s frame. Searching quality in [%s, %s]...", frame_range_2[0], quality_range_2[0], quality_range_2[1])
                            stage_d_frames = self.select_frames(final_frames, frame_range_2[0]) # fixed frames to search for quality
                            best_q, best_s, best_buff = self._binary_search(stage_d_frames, size_target_range, quality_range_2, 'quality')
                            
                            if best_q is not None:
                                buffer = best_buff
                                logger.info("Found solution in Stage D: Q=%s, size %.1fKB.", best_q, best_s / 1024)
                            else:
                                # If it still fails, log error and return
                                logger.error("Could not produce a WebP file under the size limit after all optimizations.")
                                return False

        # ========== Save the webp file =========
        try:  
            # Ensure output directory exists
            output_dir = os.path.dirname(webp_path)
            if output_dir and not os.path.exists(output_dir):
                os.makedirs(output_dir, exist_ok=True)

            # Save the final buffer to the output file
            logger.info("Writing final WebP to '%s'... ", webp_path)
            with open(webp_path, 'wb') as f:
                f.write(buffer.getvalue())
            return True
            
        except Exception as e:
            raise IOError(f"Final WebP saving failed: {e}")
        
        finally:
            end_time = time.monotonic()
            duration = end_time - start_time
            logger.info("Total time taken: %.2f seconds.", duration)

def convert_tgs_to_webp(tgs_path: str, webp_path: str, 
                       width: int = -1, height: int = -1, 
                       quality: int = 40,
                       frame_cap: bool = True,
                       max_frames: int = 180,
                       max_size: int = None,
                       keep_aspect: bool = True,
                       allow_upscale: bool = True,
                       pad: bool = True,
                       fps: float = 30.0, 
                       preserve_timing: bool = True,
                       compress_faster: bool = False
                       ) -> bool:
    """
    Simple function to convert TGS to WebP with automatic timing preservation.
    
    Args:
        tgs_path: Path to input TGS file
        webp_path: Path to output WebP file
        width: Output width in pixels (default: Original)
        height: Output height in pixels (default: Original)
        quality: WebP quality 0-100 (default: 40)
        frame_cap: Whether to cap the number of frames (default: True)
        max_frames: Maximum number of frames to render (default: 180). Ignored if frame cap is disabled.
        max_size: Maximum file size in kilobytes (default: None). This will compress the WebP by reducing quality and number of frames to meet the target size.
        keep_aspect: Preserve original aspect ratio (default True).
        allow_upscale: Allow enlarging smaller sources to meet target (default True).
        pad: When keep_aspect=True, pad with transparent canvas to fill target if True (default).
            If False, use cover+center-crop mode instead.
        fps: Target frames per second (ignored if preserve_timing=True, default: 30)
        preserve_timing: Automatically preserve original animation timing (default: True)
        compress_faster: Compress faster by using a range max cap of 75% to max size for faster processing rather than rigid max_cap (default False).
        
    Returns:
        True if conversion successful, False otherwise       
    """
    converter = TGSToWebPConverter(width, height, quality, frame_cap, max_frames, max_size, keep_aspect, allow_upscale, pad, fps, preserve_timing, compress_faster)
    try:
        return converter.convert(tgs_path, webp_path)
    except Exception as e:
        logger.error("Error during conversion: %s", e)
        return False

# CLI usage
if __name__ == "__main__":
    import sys, argparse, pathlib

    # Configure logging
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO,
        stream=sys.stdout
    )
    logger_name = pathlib.Path(__file__).stem
    logger = logging.getLogger(logger_name)
    
    parser = argparse.ArgumentParser(
        description="Convert TGS (Telegram animated stickers) to animated WebP.",
        # This helps in formatting the help text nicely
        formatter_class=argparse.RawTextHelpFormatter
    )

    # Required positional arguments
    parser.add_argument("input_file", help="Path to the input TGS file.")
    parser.add_argument("output_file", help="Path for the output WebP file.")

    # Optional arguments with default values
    parser.add_argument("--width", type=int, default=-1, help="Output width in pixels. Default: Original.")
    parser.add_argument("--height", type=int, default=-1, help="Output height in pixels. Default: Original.")
    parser.add_argument("--quality", type=int, default=40, help="WebP quality (0-100). Default: 40.")
    parser.add_argument("--max-frames", type=int, default=180, 
                        help="Maximum number of frames to render. Default: 180.\n(Note: This is ignored if you disable frame capping).")
    parser.add_argument("--max-size", type=int, default=None,
                        help="Maximum file size in kilobytes. Default: None.\n(Note: This will compress the WebP file to meet the target size by reducing quality and number of frames).")
    parser.add_argument("--fps", type=float, default=30.0,
                        help="Frames per second. \n(Note: This is ignored by default unless you use --no-preserve-timing).")

    # arguments with a default a boolean flag
    parser.add_argument("--no-frame-cap", dest="frame_cap", action="store_false",
                        help="Disable capping the number of frames. By default frames are capped at 180.")
    parser.add_argument("--no-keep-aspect", dest="keep_aspect", action="store_false",
                        help="Disable preserving aspect ratio (stretch the image).")
    parser.add_argument("--no-upscale", dest="allow_upscale", action="store_false",
                        help="Disable upscaling (do not enlarge source).")
    parser.add_argument("--crop", dest="pad", action="store_false",
                        help="When keeping aspect, use crop instead of padding.")
    parser.add_argument("--no-preserve-timing", action="store_false", dest="preserve_timing",
                        help="Disable automatic timing preservation to use the manual FPS value.")
    parser.add_argument("--fast", dest="compress_faster", action="store_true",
                        help="Enable faster compression by using a range target of 75%% to max size. Disabled by default.")

    # Let argparse handle the arguments
    args = parser.parse_args()

    # Call the main function with the parsed arguments
    success = convert_tgs_to_webp(
        tgs_path=args.input_file,
        webp_path=args.output_file,
        width=args.width,
        height=args.height,
        quality=args.quality,
        frame_cap=args.frame_cap,
        max_frames=args.max_frames,
        max_size=args.max_size,
        keep_aspect=args.keep_aspect,
        allow_upscale=args.allow_upscale,
        pad=args.pad,
        fps=args.fps,
        preserve_timing=args.preserve_timing,
        compress_faster=args.compress_faster
    )

    if success:
        logger.info("Successfully converted %s to %s", args.input_file, args.output_file)
    else:
        logger.error("Failed to convert %s", args.input_file)
        sys.exit(1)
