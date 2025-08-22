import os

def split_file(file_path, part_size=1.9 * 1024 * 1024 * 1024):
    """
    Splits a large file into smaller chunks.

    Args:
        file_path (str): Path to the original file.
        part_size (int): Size of each chunk in bytes (default ~1.9GB for Telegram).

    Returns:
        list: List of chunk file paths.
    """
    file_size = os.path.getsize(file_path)
    parts = []
    base_name = os.path.basename(file_path)
    dir_name = os.path.dirname(file_path)

    with open(file_path, "rb") as f:
        i = 1
        while True:
            chunk = f.read(int(part_size))
            if not chunk:
                break
            part_file = os.path.join(dir_name, f"{base_name}.part{i}")
            with open(part_file, "wb") as pf:
                pf.write(chunk)
            parts.append(part_file)
            i += 1

    return parts
