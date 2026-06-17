"""Xero Files API (files.xro/1.0) method group over a XeroSession.

The Files product is its own service, so its shapes differ from Accounting: a
file listing wraps its rows under an ``Items`` envelope (alongside
TotalCount/Page/PerPage), not Accounting's plural resource key, so this module
unwraps with its own ``_items`` rather than the shared ``_extract_list``;
folders and associations come back as a bare array, and a single file/folder is
the bare object (no list wrapper to unwrap). Uploading a file is the one verb a
JSON body cannot carry: it goes out as multipart/form-data, encoded here and
routed through the session's normal ``_request`` so it keeps the token refresh,
429 back-off, and Xero error handling, with an explicit multipart Content-Type.
"""

from __future__ import annotations

from urllib3.filepost import encode_multipart_formdata

from crude_xero.client import PAGE_SIZE

BASE = "files"


def _items(data):
    """Return a Files list response's rows: the ``Items`` array, or a bare array.

    A file page is ``{"TotalCount":N,"Page":1,"PerPage":100,"Items":[...]}`` —
    the Files product's own envelope, not Accounting's plural resource key — so
    pull ``Items`` by name. Folders and associations come back as a bare list,
    returned unchanged. Falls back to [] for any other shape.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        items = data.get("Items")
        if isinstance(items, list):
            return items
    return []


class FilesAPI:
    def __init__(self, session):
        self.session = session

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    def list_files(self, *, pagesize=PAGE_SIZE, all_pages=False, limit=None):
        """List files, paging via the ``pagesize``/``page`` query (the Items envelope).

        First page by default; all_pages walks every page to the end, and limit
        caps the total records (paging as needed, then truncating).
        """
        results = []
        page = 1
        while True:
            params = {"pagesize": pagesize, "page": page}
            chunk = _items(self.session._get(BASE, "Files", params=params))
            results.extend(chunk)
            if limit is not None and len(results) >= limit:
                break
            if len(chunk) < pagesize:
                break
            if limit is None and not all_pages:
                break
            page += 1
        return results[:limit] if limit is not None else results

    def get_file(self, file_id):
        return self.session._get(BASE, f"Files/{file_id}")

    def upload_file(self, name, content: bytes, mime, folder_id=None):
        """Upload a file as multipart/form-data (the one verb JSON cannot carry).

        The body is multipart-encoded here (a single ``file`` part: name, bytes,
        mime) and sent through the session's normal ``_request`` with an explicit
        multipart Content-Type, so token refresh, 429 back-off, and error
        handling all still apply. A folder_id targets that folder via the
        ``Files/{folder_id}`` path; absent, the file lands in the root.
        """
        path = f"Files/{folder_id}" if folder_id else "Files"
        body, content_type = encode_multipart_formdata({"file": (name, content, mime)})
        return self.session._request(
            "POST", BASE, path, data=body, headers={"Content-Type": content_type})

    def update_file(self, file_id, body):
        """Rename or move a file (PUT, e.g. ``{Name, FolderId}``)."""
        return self.session._put(BASE, f"Files/{file_id}", json=body)

    def delete_file(self, file_id):
        return self.session._delete(BASE, f"Files/{file_id}")

    def get_file_content(self, file_id):
        """Download the file's raw bytes."""
        return self.session._get(
            BASE, f"Files/{file_id}/Content", accept="application/octet-stream")

    # ------------------------------------------------------------------
    # Folders
    # ------------------------------------------------------------------

    def list_folders(self, *, all_pages=False, limit=None):
        """List folders (a bare array; Folders is not paged). limit truncates."""
        folders = _items(self.session._get(BASE, "Folders"))
        return folders[:limit] if limit is not None else folders

    def get_folder(self, folder_id):
        return self.session._get(BASE, f"Folders/{folder_id}")

    def create_folder(self, body):
        """Create a folder (POST, e.g. ``{Name}``)."""
        return self.session._post(BASE, "Folders", json=body)

    def update_folder(self, folder_id, body):
        return self.session._put(BASE, f"Folders/{folder_id}", json=body)

    def delete_folder(self, folder_id):
        return self.session._delete(BASE, f"Folders/{folder_id}")

    # ------------------------------------------------------------------
    # Associations (a file <-> an accounting object)
    # ------------------------------------------------------------------

    def list_file_associations(self, file_id):
        """List the objects a file is associated with."""
        return _items(self.session._get(BASE, f"Files/{file_id}/Associations"))

    def list_object_associations(self, object_id):
        """List the files associated with an accounting object."""
        return _items(self.session._get(BASE, f"Associations/{object_id}"))

    def create_association(self, file_id, body):
        """Associate a file with an object (POST, e.g. ``{ObjectId, ObjectType, ObjectGroup}``)."""
        return self.session._post(BASE, f"Files/{file_id}/Associations", json=body)

    def delete_association(self, file_id, object_id):
        return self.session._delete(BASE, f"Files/{file_id}/Associations/{object_id}")

    # ------------------------------------------------------------------
    # Inbox (the read-only drop folder)
    # ------------------------------------------------------------------

    def list_inbox(self):
        """The Inbox folder (Xero's default drop folder)."""
        return self.session._get(BASE, "Inbox")
