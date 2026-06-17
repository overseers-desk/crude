"""Xero Projects API (projects.xro/2.0) method group over a XeroSession.

One method group for the Projects product: projects, their nested tasks and time
entries, and the read-only project-users list. Projects is REST-shaped, so it
departs from Accounting in three ways the rest of the file leans on: a collection
comes back as ``{"pagination": {...}, "items": [...]}`` (unwrap the ``items`` key,
not Accounting's single-list-key heuristic); a single GET returns the bare object
rather than a one-element list; and the verbs are conventional — POST creates,
task/time updates are PUT, and a project's status change is a PATCH. Paging is the
API's own ``page``/``pageSize`` query params, surfaced on the list methods rather
than auto-walked.
"""

from __future__ import annotations

BASE = "projects"


def _items(data):
    """Return a Projects list response's ``items`` payload.

    Projects wraps a collection as ``{"pagination": {...}, "items": [...]}`` rather
    than under a plural resource key, so pull ``items`` directly. A bare list passes
    through; anything else yields [].
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        items = data.get("items")
        return items if isinstance(items, list) else []
    return []


class ProjectsAPI:
    def __init__(self, session):
        self.session = session

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _list(self, path, *, page=None, page_size=None, **params):
        """GET a Projects collection (one page) and return its ``items``.

        Projects pages via its own ``page``/``pageSize`` query params; pass them
        through with any filters, dropping the unset ones.
        """
        query = {"page": page, "pageSize": page_size}
        query.update(params)
        query = {k: v for k, v in query.items() if v is not None}
        return _items(self.session._get(BASE, path, params=query or None))

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    def list_projects(self, page=None, page_size=None, states=None, contact_id=None):
        return self._list("Projects", page=page, page_size=page_size,
                          states=states, contactID=contact_id)

    def get_project(self, project_id):
        return self.session._get(BASE, f"Projects/{project_id}")

    def create_project(self, body):
        return self.session._post(BASE, "Projects", json=body)

    def update_project(self, project_id, body):
        """PATCH a project (Projects uses PATCH for status changes, e.g. CLOSED).

        The session has no PATCH wrapper, so go through `_request` directly.
        """
        return self.session._request("PATCH", BASE, f"Projects/{project_id}", json=body)

    # ------------------------------------------------------------------
    # Tasks (nested under a project)
    # ------------------------------------------------------------------

    def list_tasks(self, project_id, page=None, page_size=None):
        return self._list(f"Projects/{project_id}/Tasks", page=page, page_size=page_size)

    def get_task(self, project_id, task_id):
        return self.session._get(BASE, f"Projects/{project_id}/Tasks/{task_id}")

    def create_task(self, project_id, body):
        return self.session._post(BASE, f"Projects/{project_id}/Tasks", json=body)

    def update_task(self, project_id, task_id, body):
        return self.session._put(BASE, f"Projects/{project_id}/Tasks/{task_id}", json=body)

    def delete_task(self, project_id, task_id):
        return self.session._delete(BASE, f"Projects/{project_id}/Tasks/{task_id}")

    # ------------------------------------------------------------------
    # Time entries (nested under a project)
    # ------------------------------------------------------------------

    def list_time(self, project_id, page=None, page_size=None):
        return self._list(f"Projects/{project_id}/Time", page=page, page_size=page_size)

    def get_time(self, project_id, time_id):
        return self.session._get(BASE, f"Projects/{project_id}/Time/{time_id}")

    def create_time(self, project_id, body):
        return self.session._post(BASE, f"Projects/{project_id}/Time", json=body)

    def update_time(self, project_id, time_id, body):
        return self.session._put(BASE, f"Projects/{project_id}/Time/{time_id}", json=body)

    def delete_time(self, project_id, time_id):
        return self.session._delete(BASE, f"Projects/{project_id}/Time/{time_id}")

    # ------------------------------------------------------------------
    # Project users (read-only)
    # ------------------------------------------------------------------

    def list_project_users(self):
        return self._list("ProjectUsers")
