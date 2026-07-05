"""
Export node: the file already exists on local disk (written by OfficeSkill). This
node's whole job is to hand back a reference to it.

SCOPE NOTE: no cloud storage (S3/blob) is wired up -- this returns a local filesystem
path, not a real download URL. This is the seam where storage upload + signed-URL
generation would plug in; deliberately not built until there's a real storage backend
to target rather than guessing at one.
"""
from app.state import DocGenState


def export_node(state: DocGenState) -> dict:
    if state.get("error") or not state.get("validation", {}).get("ok"):
        return {"download_path": ""}
    return {"download_path": state["file_path"]}
