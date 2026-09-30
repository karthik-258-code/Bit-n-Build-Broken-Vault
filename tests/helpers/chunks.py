"""Chunk facts about a folder, computed with the client's own scanner."""

from brokenvault.client.scanner import scan


def tree_chunks(root, chunk_size):
    """chunk_id -> size for every distinct chunk of the folder."""
    return {cid: length for cid, (_, _, length) in scan(str(root), chunk_size).locations.items()}


def file_chunks(root, rel_path, chunk_size):
    """Ordered chunk IDs of one file."""
    for entry in scan(str(root), chunk_size).manifest["entries"]:
        if entry["path"] == rel_path:
            return [c["id"] for c in entry["chunks"]]
    raise KeyError(rel_path)


def total_bytes(root, chunk_size):
    return sum(e.get("size", 0) for e in scan(str(root), chunk_size).manifest["entries"])
