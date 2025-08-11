"""
Video to WebP Converter Module supports many video formats like WEBM, MP4, GIF, MOV, MKV, etc.

A simple Python module for converting various video formats (WebM, MP4, etc.) to animated WebP while compressing it to a maximum size cap (Default is 500KB).
It will basically allow output files between [400,500]KB if SIZE_CAP_KB is 500KB (Default).
Features smart timing preservation and performance optimization.
"""

import os
import tempfile
from PIL import Image
import io
import time
import webp
import av
import math

class VideoToWebPConverter:
    """Converter class for Video to animated WebP conversion with automatic timing preservation."""
    
    def __init__(self, width: int = -1, height: int = -1, quality: int = 80,
                 keep_aspect: bool = True, allow_upscale: bool = True, pad: bool = True):
        """
        Initialize the converter.

        Args:
            width: Output width in pixels (-1 for original)
            height: Output height in pixels (-1 for original)
            quality: WebP quality (0-100)
            keep_aspect: Preserve original aspect ratio (default True).
            allow_upscale: Allow enlarging smaller sources to meet target (default True).
            pad: When keep_aspect=True, pad with transparent canvas to fill target if True (default).
                 If False, use cover+center-crop mode instead.
        """
        self.width = width
        self.height = height
        self.quality = quality
        self.keep_aspect = keep_aspect
        self.allow_upscale = allow_upscale
        self.pad = pad

    def _create_webp_buffer(self, frames, quality, fps):
        if not frames:
            return None

        # create a temp file
        tmp_file = tempfile.NamedTemporaryFile(suffix=".webp", delete=False)
        tmp_path = tmp_file.name
        tmp_file.close()  # close so webp.save_images can safely write on all platforms
        try:
            webp.save_images(frames, tmp_path, fps=fps, quality=quality)

            with Image.open(tmp_path) as img:
                buf = io.BytesIO()  # Create an empty buffer

                # Check if the image is static
                if getattr(img, 'n_frames', 1) == 1:
                    # It's a static webp strip the extra misleading metadata. (its causing problem beacsue the ouput is getting detected as animated webp)
                    # And video will usuallly be converted to animated webP, but if it has a single frame repeating again and again then why not make it static
                    # it will add to our computation but will be easier on end user's cpu 🥲
                    print("-> ✨ Stripping unnecessary animation metadata from static WebP.")
                    img.save(buf, format='WEBP', quality=quality)
                    
                else:
                    # If it's an animated webp no need to strip metadata
                    with open(tmp_path, 'rb') as fh:
                        buf.write(fh.read())
                buf.seek(0)
                return buf
        except Exception:
            return None
        finally:
            try:
                os.unlink(tmp_path)
            except:
                pass
        


    @staticmethod
    def _binary_search(frames: list, target_range: tuple, search_space: tuple, evaluator_func) -> tuple[int, int]:
        """
        Performs a binary search to find a value in search_space that results
        in an outcome within target_range.

        Args:
            target_range: A (min, max) tuple for the desired outcome (file size ).
            search_space: A (min, max) tuple for the values to search (e.g., frame count or quality).
            evaluator_func: A function that takes a value from search_space and returns an outcome.

        Returns:
            A tuple of (best_value, best_size). Returns (None, None) if no suitable value is found.
        """
        # frame range
        low, high = search_space
        best_value = None
        best_size = float('inf')

        low, high = int(low), int(high)
        if low > high:
            return None, None

        while low <= high:
            mid = (low + high) // 2
            if mid == 0:
                mid = 1
            # call either size_4_this_frames or size_4_this_quality
            current_size = evaluator_func(frames, mid)

            # size is under the range
            if target_range[0] <= current_size <= target_range[1]:
                return mid, current_size
            # size is lower than range minimum, not what we want but can be used if we dont find any under the range
            elif current_size < target_range[0]:
                best_value = mid
                best_size = current_size
                low = mid + 1
            # size is heigher than range maximum
            else:
                high = mid - 1

        # return the best frames/quality and best size if no ones fall in the size range after all iterations
        if best_value is not None and best_size <= target_range[1]:
            return best_value, best_size
        
        # if size is heigher than range max for all values
        return None, None


    @staticmethod
    def _select_indices(total_frames: int, original_duration: float, count: int) -> list[int]:
        """
        Selects a specific count of frame indices from a total number of frames.
        """
        if count <= 0:
            selected_indices = []
        elif count == 1:
            selected_indices = [0]
        elif count >= total_frames:
            selected_indices = list(range(total_frames))
        else:
            # Calculate target timestamps
            d = max(original_duration, 1e-6)
            targets = [i * (d / (count - 1)) for i in range(count)]
            # Map timestamps back to frame indices
            selected_indices = []
            for t in targets:
                idx = int(round((t / d) * (total_frames - 1)))
                if idx < 0: idx = 0
                if idx > total_frames - 1: idx = total_frames - 1
                # avoid duplicates by ensuring monotonic increasing indices
                if not selected_indices or idx > selected_indices[-1]:
                    selected_indices.append(idx)
            # If we lost some frames due to removing duplicates, fill by evenly spaced integer indices
            if len(selected_indices) < count:
                selected_indices = [int(round(i * (total_frames - 1) / (count - 1))) for i in range(count)]

        return selected_indices
    

    def _extract_frames_from_video(self, video_path: str, count: int):
        """
        Decodes only required frames from a video file into a list of PIL Images.
        """
        frames = []
        original_duration = 0.0
        try:
            with av.open(video_path) as container:
                if not container.streams.video:
                    raise ValueError("The provided file has no video streams.")
                stream = container.streams.video[0]

                original_fps = float(stream.average_rate) if getattr(stream, "average_rate", None) else 30.0

                # Decode all frames but store as av.VideoFrame with timestamps (don't convert to PIL now)
                decoded = []
                for frame in container.decode(stream):
                    # Prefer frame.time (float seconds) if available, else compute from pts & time_base
                    t = None
                    if getattr(frame, "time", None) is not None:
                        t = float(frame.time)
                    elif getattr(frame, "pts", None) is not None and getattr(frame, "time_base", None) is not None:
                        t = float(frame.pts * frame.time_base)
                    decoded.append((t, frame))
                    
                if not decoded:
                    raise ValueError("Video file appears to have no frames.")
                
                precision = False # if you want precision over reliability for calulating video duratio

                total_frames = len(decoded)
                duration_seconds = None
                
                if precision:
                    if getattr(container, "duration", None) not in (None, 0):
                        duration_seconds = float(container.duration) / 1_000_000.0
                    elif getattr(stream, "duration", None) and getattr(stream, "time_base", None):
                        duration_seconds = float(stream.duration * stream.time_base)

                # Calculating duration from actual frame timestamps, as metadata can be unreliable.
                last_frame_time = decoded[-1][0]
                
                # if the last frame's timestamp is valid
                if last_frame_time is not None and last_frame_time > 0.0:
                    # the duration is the timestamp of the last frame.
                    # We add the duration of one more frame to account for the last frame's display time.
                    avg_frame_duration = 1.0 / max(original_fps, 1.0)
                    duration_seconds = last_frame_time + avg_frame_duration
                else:
                    # Fallback: If timestamps are zero or None, estimate duration from frame count and FPS.
                    # This is a last resort for videos with broken time information.
                    print("-> ⚠️ Warning: Could not determine duration from frame timestamps. Falling back to FPS-based estimation.")
                    duration_seconds = total_frames / max(original_fps, 1.0)
                
                # Ensure we have a small, non-zero duration to prevent division-by-zero errors.
                original_duration = max(duration_seconds, 1e-6)

                original_duration = duration_seconds
                # logging
                print(f"Decoded {total_frames} frames; duration ~ {original_duration:.3f}s; avg_fps={original_fps}")
                # Logging details 
                if total_frames > count:
                    print(f"Limiting video to {count} frames for performance.")
                else:
                    print(f"Preserving all {total_frames} frames.")

                # Extract frames
                indices_to_extract = self._select_indices(total_frames, original_duration, count)
                for idx in indices_to_extract:
                    # Append frames to the frames list

                    av_frame = decoded[idx][1]
                    pil_image = av_frame.to_image()
                    orig_w, orig_h = pil_image.size
                    target_w = self.width if self.width != -1 else orig_w
                    target_h = self.height if self.height != -1 else orig_h
                    pad_mode = self.pad

                    # ========== resize logic ===========
                     
                    # if haven't given width and height or given but our image is already of that dimension, we need not to resize
                    if (orig_w, orig_h) == (target_w, target_h):
                        final_img = pil_image.convert("RGBA")
                    
                    else:# nah now we have to resize
                        if self.keep_aspect:
                            if pad_mode:
                                # Fit inside target, then pad (transparent canvas)
                                scale = min(target_w / orig_w, target_h / orig_h)
                            else:
                                # Cover the target, then crop center
                                scale = max(target_w / orig_w, target_h / orig_h)
                            # enforce allow_upscale (if manually set to Flase by user) and then we can only pad as cropping won't meet the asked dimensions
                            if not self.allow_upscale and scale > 1.0:
                                scale = 1.0
                                pad_mode = True
                            if pad_mode:
                                # fit: make sure new dims are <= target (use floor / clamp)
                                new_w = max(1, int(math.floor(orig_w * scale)))
                                new_h = max(1, int(math.floor(orig_h * scale)))
                                # clamp in case of rounding overshoot
                                new_w = min(new_w, target_w)
                                new_h = min(new_h, target_h)
                            else:
                                # cover: make sure new dims are >= target (use ceil / ensure minimum)
                                new_w = max(1, int(math.ceil(orig_w * scale)))
                                new_h = max(1, int(math.ceil(orig_h * scale)))
                                if new_w < target_w:
                                    new_w = target_w
                                if new_h < target_h:
                                    new_h = target_h
                            # scale the image
                            resized = pil_image.resize((new_w, new_h), Image.LANCZOS).convert("RGBA")
                            if pad_mode:
                                # paste centered onto transparent canvas of exact target size
                                canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
                                left = (target_w - new_w) // 2
                                top = (target_h - new_h) // 2
                                canvas.paste(resized, (left, top), resized)
                                final_img = canvas
                            else:
                                # crop center from resized image (new_w/new_h >= target)
                                left = (new_w - target_w) // 2
                                top = (new_h - target_h) // 2
                                final_img = resized.crop((left, top, left + target_w, top + target_h))
                        else:
                            # keep_aspect == False: strict stretch to target, but if upscaling disabled, clamp per-dimension
                            desired_w, desired_h = target_w, target_h
                            if not self.allow_upscale:
                                # clamp each dimension separately so we don't upscale any axis
                                desired_w = min(desired_w, orig_w)
                                desired_h = min(desired_h, orig_h)
                                
                            if (desired_w, desired_h) == (orig_w, orig_h):
                                resized = pil_image.convert("RGBA")
                            else:
                                resized = pil_image.resize((desired_w, desired_h), Image.LANCZOS).convert("RGBA")
                            # Ensure final output is exactly target size by centering resized on transparent canvas when needed
                            if desired_w == target_w and desired_h == target_h:
                                final_img = resized
                            else:
                                canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
                                left = (target_w - desired_w) // 2
                                top = (target_h - desired_h) // 2
                                canvas.paste(resized, (left, top), resized)
                                final_img = canvas
                    frames.append(final_img)

                if not frames:
                    raise ValueError("Video file appears to have no frames.")



                
                print(f"Video details: {original_duration:.2f}s duration, {stream.width}x{stream.height} resolution.")

        except Exception as e:
            print(e)
            raise ValueError(f"Could not decode video file with PyAV: {video_path}") from e

        return frames, original_duration

    
    def convert(self, video_path: str, webp_path: str) -> bool:
        """
        Convert Video file to animated WebP with a size cap of ~500KB.
        """
        start_time = time.monotonic()
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")
        

        MAX_FRAMES_CAP = 30

        # ========== Step 1: Extract Frames From Video & get other metatdeta info and all ===========
        try:
            final_frames, original_duration = self._extract_frames_from_video(video_path, MAX_FRAMES_CAP)
        except Exception as e:
            raise ValueError(f"Failed to extract frames from video: {e}")

        if not final_frames:
            raise ValueError("Could not render any frames from the video file.")

        # some important variables
        SIZE_CAP_KB = 490 # size cap (490 should be 500 but just a lil prtection for some shit file managers)
        SIZE_TARGET_RANGE = ((SIZE_CAP_KB-100) * 1024, SIZE_CAP_KB * 1024)  
        CAP_FRAMES_SiZE = len(final_frames)
        FRAME_PIVOT = CAP_FRAMES_SiZE // 2
        final_quality = self.quality
        successful_buffer = None
        
        # Helper to select a subset of frames evenly
        def select_frames(source_frames, count):
            if count <= 0: return []
            if count == 1:
                return [source_frames[0]]
            if count >= len(source_frames): return source_frames
            indices = [int(i * (len(source_frames) - 1) / (count - 1)) for i in range(count)]
            return [source_frames[i] for i in indices]

        #  helper to get size for specific number of frames from given a list of frames
        def size_4_this_frames(frames, num_frames):
            nonlocal successful_buffer
            frames_to_test = select_frames(frames, num_frames)
            if not frames_to_test: return float('inf')
            fps = len(frames_to_test) / original_duration
            
            buffer = self._create_webp_buffer(frames_to_test, final_quality, fps)
            
            if buffer:
                successful_buffer = buffer
                return buffer.getbuffer().nbytes
            return float('inf')

        # helper to get size for quality
        def size_4_this_quality(frames, quality):
            nonlocal successful_buffer
            if not frames: return float('inf')
            fps = len(frames) / original_duration
            buffer = self._create_webp_buffer(frames, quality, fps)

            if buffer:
                successful_buffer = buffer
                return buffer.getbuffer().nbytes
            return float('inf')
        
        # =======  Step 2: start searching for best size  ============

        print(f"🔊 Video file found, aiming for a file size under {SIZE_CAP_KB}KB.")

        # Stage A: Try with max frames at default quality
        print(f"[*] Stage A: Testing with {len(final_frames)} frames @ Q={final_quality}...")
        buffer = self._create_webp_buffer(final_frames, final_quality, len(final_frames) / original_duration)
        current_size = buffer.getbuffer().nbytes if buffer else float('inf')

        # If it's a success hold on to this buffer for the final save.
        if current_size <= SIZE_TARGET_RANGE[1]:
            successful_buffer = buffer
            print(f"☑️ Success! Size is {current_size / 1024:.1f}KB. No further optimization needed.")
        else:
            print(f"->👎 Too big ({current_size / 1024:.1f}KB). Starting advanced optimization...")
            
            # Define search ranges
            frame_range_1 = (FRAME_PIVOT, CAP_FRAMES_SiZE)
            frame_range_2 = (1, FRAME_PIVOT)
            fallback_frame_count = FRAME_PIVOT

            quality_range_1 = (int(self.quality / 2), self.quality)
            quality_range_2 = (1, int(self.quality / 2))
            fallback_quality = int(self.quality/2)

            # --- Start the search  ---

            # Stage B: Binary search on frame_range1
            print(f"[*] Stage B: Searching frame count in [{int(frame_range_1[0])}, {int(frame_range_1[1])}] @ Q={final_quality}...")
            best_f, best_s = self._binary_search(final_frames, SIZE_TARGET_RANGE, frame_range_1, size_4_this_frames)

            if best_f:
                print(f"-> ☑️ Found solution in Stage B: {best_f} frames, size {best_s / 1024:.1f}KB.")
            else:
                # Stage C: Binary search on quality_range_1
                print(f"[*] Stage C: Too big. Fixing at {fallback_frame_count} frames. Searching quality in [{quality_range_1[0]}, {quality_range_1[1]}]...")
                final_frames = select_frames(final_frames, fallback_frame_count)
                best_q, best_s = self._binary_search(final_frames, SIZE_TARGET_RANGE, quality_range_1, size_4_this_quality)

                if best_q:
                    print(f"-> ☑️ Found solution in Stage C: Q={best_q}, size {best_s / 1024:.1f}KB.")
                else:
                    # Stage D: Binary search on frame_range_2
                    print(f"[*] Stage D: Still too big. Fixing quality at {fallback_quality}. Searching frames in [{int(frame_range_2[0])}, {int(frame_range_2[1])}]...")
                    final_quality = fallback_quality
                    best_f, best_s = self._binary_search(final_frames, SIZE_TARGET_RANGE, frame_range_2, size_4_this_frames)

                    if best_f:
                        print(f"-> ☑️ Found solution in Stage D: {best_f} frames, size {best_s / 1024:.1f}KB.")
                    else:
                        # Stage E: Binary search on quality_range_2
                        print(f"[*] Stage E: Last resort! Fixing at {int(frame_range_2[0])} frame. Searching quality in [{quality_range_2[0]}, {quality_range_2[1]}]...")
                        final_frames = select_frames(final_frames, 1)
                        best_q, best_s = self._binary_search(final_frames, SIZE_TARGET_RANGE, quality_range_2, size_4_this_quality)
                        
                        if best_q:
                            print(f"-> ☑️ ⚠️ Extreme compression: 1 frame, Q={best_q}, size {best_s / 1024:.1f}KB.")
                        else:
                            # If it still fails set quality 1
                            final_quality = 1
                            successful_buffer = self._create_webp_buffer(final_frames, final_quality, 1/original_duration)
                            current_size = successful_buffer.getbuffer().nbytes if successful_buffer else float('inf')
                            print(f"->⚠️ Extreme compression: 1 frame, Q=1, size {current_size / 1024:.1f}KB.")

        # ======== Step 3: Final Save ==========
        try:
            if successful_buffer:
                print(f"\nSaving final WebP to '{webp_path}'...")
                with open(webp_path, 'wb') as f:
                    f.write(successful_buffer.getvalue())
                return True
            else:
                # If the buffer is STILL empty after all stages, the conversion failed.
                raise ValueError("Could not produce a WebP file under the size limit after all optimizations.")

        except Exception as e:
            raise IOError(f"Final WebP saving failed: {e}")
        
        finally:
            end_time = time.monotonic()
            duration = end_time - start_time
            print(f"⌛ Total time taken: {duration:.2f} seconds.\n")


def convert_video_to_webp(video_path: str, webp_path: str, 
                        width: int = -1, height: int = -1, 
                        quality: int = 80,
                        keep_aspect: bool = True,
                        allow_upscale: bool = True,
                        pad: bool = True) -> bool:
    """
    Simple function to convert a video file to animated WebP with automatic timing preservation.
    
    Args:
        video_path: Path to input video file
        webp_path: Path to output WebP file
        width: Output width in pixels (default: Original)
        height: Output height in pixels (default: Original)
        quality: WebP quality 0-100 (default: 80)
        keep_aspect: Preserve original aspect ratio (default True).
        allow_upscale: Allow enlarging smaller sources to meet target (default True).
        pad: When keep_aspect=True, pad with transparent canvas to fill target if True (default).
            If False, use cover+center-crop mode instead.

    Returns:
        True if conversion successful, False otherwise
        
    """
    converter = VideoToWebPConverter(width, height, quality, 
                                     keep_aspect, allow_upscale, pad)
    try:
        return converter.convert(video_path, webp_path)
    except Exception as e:
        print(f"Error during conversion: {e}")
        return False


if __name__ == "__main__":
    import argparse, sys

    parser = argparse.ArgumentParser(
        description="Convert video files (WebM, MP4, etc.) to animated WebP.",
        formatter_class=argparse.RawTextHelpFormatter
    )

    # Required positional arguments
    parser.add_argument("input_file", help="Path to the input video file.")
    parser.add_argument("output_file", help="Path for the output WebP file.")

    # Optional arguments
    parser.add_argument("--width", type=int, default=-1, help="Output width in pixels. Default: Original.")
    parser.add_argument("--height", type=int, default=-1, help="Output height in pixels. Default: Original.")
    parser.add_argument("--quality", type=int, default=80, help="WebP quality (0-100). Default: 80.")

    # Aspect / upscaling / padding controls
    parser.add_argument("--no-keep-aspect", dest="keep_aspect", action="store_false",
                        help="Disable preserving aspect ratio (stretch the image).")
    parser.add_argument("--no-upscale", dest="allow_upscale", action="store_false",
                        help="Disable upscaling (do not enlarge source). Default is to allow upscaling.")
    parser.add_argument("--crop", dest="pad", action="store_false",
                        help="When keeping aspect, use crop instead of padding with transparency.")

    args = parser.parse_args()

    # Call the main function with the parsed arguments
    success = convert_video_to_webp(
        video_path=args.input_file,
        webp_path=args.output_file,
        width=args.width,
        height=args.height,
        quality=args.quality,
        keep_aspect=args.keep_aspect,
        allow_upscale=args.allow_upscale,
        pad=args.pad,
    )

    if success:
        print(f"✅ Successfully converted {args.input_file} to {args.output_file}")
    else:
        print(f"❌ Failed to convert {args.input_file}")
        sys.exit(1)
