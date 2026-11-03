"""Portable validation for user-controlled output names."""

from pathlib import PureWindowsPath


def safe_output_parts(filename: str, directory: str) -> tuple[str, str]:
    filename = str(filename).strip()
    directory = str(directory or "").replace("\\", "/")
    reserved = {"CON", "PRN", "AUX", "NUL"} | {
        f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(1, 10)
    }

    def valid_part(part):
        if (
            not part
            or part in (".", "..")
            or part.endswith((".", " "))
            or any(c in part for c in '<>:"/\\|?*')
            or any(ord(c) < 32 for c in part)
            or part.split(".")[0].upper() in reserved
        ):
            raise ValueError(
                "Use a simple filename and relative subfolders; paths cannot escape the export directory"
            )

    valid_part(filename)
    if PureWindowsPath(directory).is_absolute() or directory.startswith("/"):
        raise ValueError("Export subfolder must be relative")
    if directory:
        for part in directory.split("/"):
            valid_part(part)
    if not filename.lower().endswith(".png"):
        filename += ".png"
    return filename, directory
