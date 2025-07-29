"""
Video to WebP Converter Module supports many video formats like WEBM, MP4, GIF, MOV, MKV, etc.

A simple Python module for converting various video formats (WebM, MP4, etc.) to animated WebP while compressing it to a maximum size cap (Default is 500KB).
It will basically allow output files between [400,500]KB if SIZE_CAP_KB is 500KB (Default).
Features smart timing preservation and performance optimization.
"""

import os
import tempfile
from PIL import Image, ImageDraw
import argparse
import sys
import io
import time
import webp
import av

class VideoToWebPConverter:
    """Converter class for Video to animated WebP conversion with automatic timing preservation."""
    
    def __init__(self, width: int = -1, height: int = -1, quality: int = 80):
        """
        Initialize the converter.
        
        Args:
            width: Output width in pixels (-1 for original)
            height: Output height in pixels (-1 for original)
            quality: WebP quality (0-100)
        """
        self.width = width
        self.height = height
        self.quality = quality

    def _create_webp_buffer(self, frames, quality, fps):
        if not frames:
            return None

        # write to a temp file
        tmp_file = tempfile.NamedTemporaryFile(suffix=".webp", delete=False)
        try:
            webp.save_images(frames, tmp_file.name, fps=fps, quality=quality)
            tmp_file.seek(0)
            buf = io.BytesIO(tmp_file.read())
        finally:
            tmp_file.close()
            os.unlink(tmp_file.name)  # delete the file-for-good
        return buf

    
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
    def _select_indices(total_frames: int, count: int) -> list[int]:
        """
        Selects a specific count of frame indices from a total number of frames.
        """
        if count <= 0 or total_frames <= 0:
            return []
        if count == 1:
            return [0]
        if count >= total_frames:
            return list(range(total_frames))

        indices = [int(i * (total_frames - 1) / (count - 1)) for i in range(count)]
        return indices
    

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

                original_fps = stream.average_rate or 30.0

                # Calculate total frames using the stream's duration
                if stream.frames > 0:
                    total_frames = stream.frames
                else:
                    total_frames = round((container.duration / 1_000_000) * original_fps)
                # Logging details 
                if total_frames > count:
                    print(f"Video has {total_frames} frames. Limiting video to {count} frames for performance.")
                else:
                    print(f"Preserving all {total_frames} frames.")
                # Extract frames
                _count = 0
                indices_to_extract = set(self._select_indices(total_frames, count))
                for frame in container.decode(stream):
                    # Append frames to the frames list
                    if _count in indices_to_extract:
                        pil_image = frame.to_image()
                        # resize if needed
                        if (self.width != -1 and self.height != -1) and (pil_image.size != (self.width, self.height)):
                            pil_image = pil_image.resize((self.width, self.height), Image.LANCZOS)
                        frames.append(pil_image)

                    _count += 1
                if not frames:
                    raise ValueError("Video file appears to have no frames.")

                # Calculate video duration 
                if container.duration:
                    original_duration = float(container.duration / av.time_base)
                elif stream.duration and stream.time_base:
                    original_duration = float(stream.duration * stream.time_base)
                
                # Fallback if duration metadata is still missing
                if original_duration == 0:
                    original_duration = total_frames / float(original_fps)
                
                print(f"Video details: {original_duration:.2f}s duration, {stream.width}x{stream.height} resolution.")

        except Exception as e:
            print(e)
            raise ValueError(f"Could not decode video file with PyAV: {video_path}") from e

        return frames, original_duration

    
    def _create_fallback_frame(self, width: int, height: int, frame_num: int, total_frames: int) -> Image.Image:
        """Create a simple fallback frame when video processing fails."""
        img = Image.new('RGB', (width, height), (128, 128, 128))
        draw = ImageDraw.Draw(img)
        
        # Calculate animation progress
        progress = frame_num / max(total_frames - 1, 1)
        
        # Create a simple animated element
        center_x = int(width * (0.2 + 0.6 * progress))
        center_y = int(height * 0.5)
        radius = int(min(width, height) * 0.1)
        
        # Draw a circle
        color = (255, 100, 100)  # Red
        draw.ellipse([center_x - radius, center_y - radius, 
                     center_x + radius, center_y + radius], fill=color)
        
        return img
    
    def convert(self, video_path: str, webp_path: str) -> bool:
        """
        Convert Video file to animated WebP with a size cap of ~500KB.
        """
        start_time = time.monotonic()
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")
        

        MAX_FRAMES_CAP = 30

        # --- Stage 1: Extract Frames From Video while maintaining max frame cap---
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
        
        # ---  Step 2: start searching for best size  ---

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

        # --- Stage 3: Final Save ---
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
                        quality: int = 80) -> bool:
    """
    Simple function to convert a video file to animated WebP with automatic timing preservation.
    
    Args:
        video_path: Path to input video file
        webp_path: Path to output WebP file
        width: Output width in pixels (default: Original)
        height: Output height in pixels (default: Original)
        quality: WebP quality 0-100 (default: 80)
        
    Returns:
        True if conversion successful, False otherwise
        
    """
    converter = VideoToWebPConverter(width, height, quality)
    try:
        return converter.convert(video_path, webp_path)
    except Exception as e:
        print(f"Error during conversion: {e}")
        return False


if __name__ == "__main__":
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

    args = parser.parse_args()

    # Call the main function with the parsed arguments
    success = convert_video_to_webp(
        video_path=args.input_file,
        webp_path=args.output_file,
        width=args.width,
        height=args.height,
        quality=args.quality,
    )

    if success:
        print(f"✅ Successfully converted {args.input_file} to {args.output_file}")
    else:
        print(f"❌ Failed to convert {args.input_file}")
        sys.exit(1)
