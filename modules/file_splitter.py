import os

def split_file(file_path, chunk_size=2097152):  # 2 GB in bytes
    base_name, ext = os.path.splitext(file_path)
    part_num = 1
    with open(file_path, 'rb') as f:
        while True:
            data = f.read(chunk_size)
            if not data:
                break
            part_path = f"{base_name}_part{part_num}{ext}"
            with open(part_path, 'wb') as out_f:
                out_f.write(data)
            part_num += 1

    return [f for f in os.listdir() if f.startswith(base_name) and f.endswith(ext)]

def merge_files(file_parts):
    base_name = file_parts[0].rsplit('_part', 1)[0]
    ext = os.path.splitext(file_parts[0])[1]
    merged_path = f"{base_name}{ext}"
    with open(merged_path, 'wb') as out_f:
        for part in file_parts:
            with open(part, 'rb') as in_f:
                out_f.write(in_f.read())

    return merged_path

if __name__ == "__main__":
    # Example usage
    file_to_split = "large_file.mp4"
    parts = split_file(file_to_split)
    print("Parts:", parts)

    # To merge parts back to the original file
    # merged_file = merge_files(parts)
    # print("Merged File:", merged_file)
