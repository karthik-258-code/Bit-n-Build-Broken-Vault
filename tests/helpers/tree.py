"""Build and compare folder trees for tests."""

import hashlib
import os
import random


def rand_bytes(n, seed):
    return random.Random(seed).randbytes(n)


def build_tree(root, spec):
    """Create a tree. spec maps relative path -> bytes (file) or None (folder)."""
    os.makedirs(root, exist_ok=True)
    for rel, content in spec.items():
        target = os.path.join(root, *rel.split("/"))
        if content is None:
            os.makedirs(target, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as f:
                f.write(content)
    return root


def sample_tree(root, chunk=1024, seed=1):
    """Nested paths, an empty file, an empty folder, a multi-chunk file, two identical files."""
    big = rand_bytes(chunk * 20 + 300, seed)
    twin = rand_bytes(chunk * 3, seed + 1)
    return build_tree(str(root), {
        "big.bin": big,
        "empty.txt": b"",
        "empty-dir": None,
        "docs/readme.txt": b"hello vault\n",
        "docs/deep/nested/note.txt": rand_bytes(chunk + 17, seed + 2),
        "copies/a.bin": twin,
        "copies/b.bin": twin,
    })


def _file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def snapshot(root):
    """rel path -> ('dir', mtime_ns) or ('file', size, sha256, mtime_ns). Root itself excluded."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            out[rel] = ("dir", os.stat(full).st_mtime_ns)
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            st = os.stat(full)
            out[rel] = ("file", st.st_size, _file_hash(full), st.st_mtime_ns)
    return out


def compare_trees(a, b, mtime_tolerance_ns=1_000_000_000):
    """Return a list of differences between two trees (empty list = identical)."""
    sa, sb = snapshot(a), snapshot(b)
    diffs = []
    for rel in sorted(set(sa) | set(sb)):
        if rel not in sa:
            diffs.append(f"only in second: {rel}")
        elif rel not in sb:
            diffs.append(f"only in first: {rel}")
        elif sa[rel][:-1] != sb[rel][:-1]:
            diffs.append(f"content or type differs: {rel}")
        elif abs(sa[rel][-1] - sb[rel][-1]) > mtime_tolerance_ns:
            diffs.append(f"mtime differs: {rel} ({sa[rel][-1]} vs {sb[rel][-1]})")
    return diffs
