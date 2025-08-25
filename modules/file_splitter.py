#
# This module contains functions for splitting and merging large files,
# primarily used for handling Telegram's file size limits.
#

import os

def split_file(file_path, chunk_size=2097152000): # 2 GB in bytes
    """
    Splits a single large file into smaller chunks, each up to `chunk_size`
    and returns a list of the full paths to the new parts.

    :param file_path: The full path to the large file to be split.
    :param chunk_size: The maximum size of each part in bytes.
    :return: A list of strings, where each string is the full path to a part file.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    # Remove the .mp4 extension for consistent naming
    base_name, ext = os.path.splitext(file_path)
    part_dir = os.path.dirname(base_name)
    part_base_name = os.path.basename(base_name)
    part_num = 1
    part_paths = []

    with open(file_path, 'rb') as f:
        while True:
            data = f.read(chunk_size)
            if not data:
                break
            
            # Create a more user-friendly part name
            part_path = os.path.join(part_dir, f"{part_base_name}_part{part_num}{ext}")
            with open(part_path, 'wb') as out_f:
                out_f.write(data)
            
            part_paths.append(part_path)
            part_num += 1

    return part_paths

def merge_files(file_parts):
    """
    Merges a list of file parts back into a single file.

    :param file_parts: A list of strings, where each string is the path to a part file.
    :return: The full path to the merged file.
    """
    if not file_parts:
        return None
    
    # Get the base name from the first part, by removing '_partX'
    base_name_part, ext = os.path.splitext(file_parts[0])
    base_name = base_name_part.rsplit('_part', 1)[0]
    
    merged_path = f"{base_name}{ext}"
    with open(merged_path, 'wb') as out_f:
        for part in file_parts:
            if not os.path.exists(part):
                raise FileNotFoundError(f"Part file not found: {part}")
            with open(part, 'rb') as in_f:
                out_f.write(in_f.read())

    return merged_path

# Example usage (for testing)
if __name__ == "__main__":
    test_file_path = "test_large_file.mp4"
    # Create a dummy large file for demonstration
    with open(test_file_path, 'wb') as f:
        f.write(b'\0' * (2100 * 1024 * 1024))
    
    print("Splitting large file...")
    parts = split_file(test_file_path, chunk_size=1024*1024*1024)
    print(f"File split into {len(parts)} parts.")
    print("Part paths:", parts)
    
    print("Merging parts back...")
    merged_file = merge_files(parts)
    print("Merged file path:", merged_file)

    # Cleanup test files
    os.remove(test_file_path)
    for p in parts:
        os.remove(p)
    if os.path.exists(merged_file):
        os.remove(merged_file)

