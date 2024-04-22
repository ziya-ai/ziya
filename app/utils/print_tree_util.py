import os

def print_file_tree(file_paths):
    # Create a dictionary to store files and directories
    file_tree = {}

    # Populate the file tree dictionary
    for path in file_paths:
        dir_path, file_name = os.path.split(path)
        if dir_path not in file_tree:
            file_tree[dir_path] = []
        file_tree[dir_path].append(file_name)

    # Sort directories and files
    sorted_dirs = sorted(file_tree.keys())
    for dir_path in sorted_dirs:
        file_tree[dir_path].sort()

    # Print the file tree recursively
    def print_dir(dir_path, indent):
        nonlocal printed_dirs
        if dir_path in printed_dirs:
            return

        printed_dirs.add(dir_path)
        print(f"{indent}{os.path.basename(dir_path)}")
        if file_tree[dir_path]:
            for i, file_name in enumerate(file_tree[dir_path]):
                if i == len(file_tree[dir_path]) - 1:
                    print(f"{indent}    └── {file_name}")
                else:
                    print(f"{indent}    ├── {file_name}")
        else:
            print(f"{indent}    (empty)")

        # Recursively print subdirectories
        subdirs = [subdir for subdir in sorted_dirs if subdir.startswith(dir_path + os.sep)]
        for subdir in subdirs:
            print_dir(subdir, indent + "    ")

    printed_dirs = set()
    for dir_path in sorted_dirs:
        print_dir(dir_path, "")