"""Projects API resource sub-apps for crude-xero.

`register(app)` attaches four sub-`Typer`s: `project`, `task`, `time-entry`, and
the read-only `project-user`. Tasks and time entries are nested under a project,
so their verbs take `--project <id>` (the parent) plus the child id as an argument
where one is needed; reads render with the shared `_emit_list`/`_emit_record` and
writes go through `_do_write`/`_merge_update` with confirm-before-write. Two shapes
differ from Accounting: `project update` is a PATCH status change (`--status` or a
JSON `--data` body), and `cli_accounting._resource` — which is bound to
`.accounting` — does not fit these nested, parent-scoped resources, so the verbs
are written out against `_projects()` (the `XeroClient.projects` facade group).
"""

from __future__ import annotations

from typing import Optional

import typer

from crude_common.cliutil import _do_write, _emit_list, _emit_record, _merge_update, _read_data


def _client(*args, **kwargs):
    """The configured Xero client (lazily, to avoid an import cycle with cli)."""
    from crude_xero.cli import _client as _impl

    return _impl(*args, **kwargs)


def _projects(*args, **kwargs):
    """The Projects method group off the configured client facade (`.projects`)."""
    return _client(*args, **kwargs).projects


# Table columns per resource (Projects fields are lower-camelCase, not Accounting's).
_PROJECT_COLS = [
    ("ID", "projectId"), ("Name", "name"), ("Contact", "contactId"),
    ("Status", "status"), ("Deadline", "deadlineUtc"),
]
_TASK_COLS = [
    ("ID", "taskId"), ("Name", "name"), ("Charge", "chargeType"),
    ("EstMins", "estimateMinutes"), ("Status", "status"),
]
_TIME_COLS = [
    ("ID", "timeEntryId"), ("Task", "taskId"), ("User", "userId"),
    ("Date", "dateUtc"), ("Duration", "duration"), ("Status", "status"),
]
_PROJECT_USER_COLS = [("ID", "userId"), ("Name", "name"), ("Email", "email")]


# ----------------------------------------------------------------------
# register
# ----------------------------------------------------------------------


def register(app: typer.Typer) -> None:
    """Attach the Projects resource sub-apps to the root app."""
    _register_project(app)
    _register_task(app)
    _register_time(app)
    _register_project_user(app)


def _register_project(app: typer.Typer) -> None:
    project = typer.Typer(help="Xero projects.")
    app.add_typer(project, name="project")

    @project.command("list", help="List projects.")
    def _list(
        page: Optional[int] = typer.Option(None, "--page", help="Page number (1-based)."),
        page_size: Optional[int] = typer.Option(None, "--page-size", help="Records per page."),
        states: Optional[str] = typer.Option(None, "--states", help="Filter by state(s), e.g. INPROGRESS or CLOSED."),
        contact: Optional[str] = typer.Option(None, "--contact", help="Filter by contact id (GUID)."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        try:
            items = _projects().list_projects(
                page=page, page_size=page_size, states=states, contact_id=contact)
        except Exception as e:
            typer.echo(f"Error fetching projects: {e}", err=True)
            raise typer.Exit(1)
        _emit_list(items, _PROJECT_COLS, "project", output_json)

    @project.command("get", help="Show a single project.")
    def _get(
        project_id: str = typer.Argument(..., help="Project id (GUID)."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        try:
            item = _projects().get_project(project_id)
        except Exception as e:
            typer.echo(f"Error fetching project {project_id}: {e}", err=True)
            raise typer.Exit(1)
        _emit_record(item, output_json)

    @project.command("create", help="Create a project from a JSON body (contactId, name, ...).")
    def _create(
        data: Optional[str] = typer.Option(None, "--data", help="Project object as JSON (or -f / stdin)."),
        file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
    ):
        body = _read_data(data, file)
        _do_write(
            lambda: _projects().create_project(body),
            "create project", confirm="Create this project?",
            yes=yes, output_json=output_json,
        )

    @project.command("update", help="Update a project (PATCH; e.g. close it). Use --status or --data.")
    def _update(
        project_id: str = typer.Argument(..., help="Project id (GUID) to update."),
        status: Optional[str] = typer.Option(None, "--status", help="New status, e.g. INPROGRESS or CLOSED."),
        data: Optional[str] = typer.Option(None, "--data", help="Partial JSON body (or -f / stdin)."),
        file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
    ):
        body = _read_data(data, file, required=False)
        if status is not None:
            body["status"] = status
        if not body:
            typer.echo("Error: nothing to update; pass --status or --data.", err=True)
            raise typer.Exit(1)
        _do_write(
            lambda: _projects().update_project(project_id, body),
            f"update project {project_id}", confirm=f"Update project {project_id}?",
            yes=yes, output_json=output_json,
        )


def _register_task(app: typer.Typer) -> None:
    task = typer.Typer(help="Xero project tasks (scoped to a project via --project).")
    app.add_typer(task, name="task")

    @task.command("list", help="List a project's tasks.")
    def _list(
        project_id: str = typer.Option(..., "--project", help="Parent project id (GUID)."),
        page: Optional[int] = typer.Option(None, "--page", help="Page number (1-based)."),
        page_size: Optional[int] = typer.Option(None, "--page-size", help="Records per page."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        try:
            items = _projects().list_tasks(project_id, page=page, page_size=page_size)
        except Exception as e:
            typer.echo(f"Error fetching tasks: {e}", err=True)
            raise typer.Exit(1)
        _emit_list(items, _TASK_COLS, "task", output_json)

    @task.command("get", help="Show a single task.")
    def _get(
        task_id: str = typer.Argument(..., help="Task id (GUID)."),
        project_id: str = typer.Option(..., "--project", help="Parent project id (GUID)."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        try:
            item = _projects().get_task(project_id, task_id)
        except Exception as e:
            typer.echo(f"Error fetching task {task_id}: {e}", err=True)
            raise typer.Exit(1)
        _emit_record(item, output_json)

    @task.command("create", help="Create a task on a project from a JSON body.")
    def _create(
        project_id: str = typer.Option(..., "--project", help="Parent project id (GUID)."),
        data: Optional[str] = typer.Option(None, "--data", help="Task object as JSON (or -f / stdin)."),
        file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
    ):
        body = _read_data(data, file)
        _do_write(
            lambda: _projects().create_task(project_id, body),
            f"create task on project {project_id}", confirm="Create this task?",
            yes=yes, output_json=output_json,
        )

    @task.command("update", help="Update a task (PUT; read-merge-write).")
    def _update(
        task_id: str = typer.Argument(..., help="Task id (GUID) to update."),
        project_id: str = typer.Option(..., "--project", help="Parent project id (GUID)."),
        data: Optional[str] = typer.Option(None, "--data", help="Partial JSON overlaying the fetched task."),
        file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON overlay from a file."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
    ):
        papi = _projects()
        _merge_update(
            lambda: papi.get_task(project_id, task_id),
            lambda merged: papi.update_task(project_id, task_id, merged),
            data, file, {}, f"update task {task_id}", yes, output_json,
        )

    @task.command("delete", help="Delete a task by id.")
    def _delete(
        task_id: str = typer.Argument(..., help="Task id (GUID)."),
        project_id: str = typer.Option(..., "--project", help="Parent project id (GUID)."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
    ):
        _do_write(
            lambda: _projects().delete_task(project_id, task_id),
            f"delete task {task_id}", confirm=f"Delete task {task_id}?",
            yes=yes, output_json=output_json,
        )


def _register_time(app: typer.Typer) -> None:
    time_entry = typer.Typer(help="Xero project time entries (scoped to a project via --project).")
    app.add_typer(time_entry, name="time-entry")

    @time_entry.command("list", help="List a project's time entries.")
    def _list(
        project_id: str = typer.Option(..., "--project", help="Parent project id (GUID)."),
        page: Optional[int] = typer.Option(None, "--page", help="Page number (1-based)."),
        page_size: Optional[int] = typer.Option(None, "--page-size", help="Records per page."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        try:
            items = _projects().list_time(project_id, page=page, page_size=page_size)
        except Exception as e:
            typer.echo(f"Error fetching time entries: {e}", err=True)
            raise typer.Exit(1)
        _emit_list(items, _TIME_COLS, "time entry", output_json)

    @time_entry.command("get", help="Show a single time entry.")
    def _get(
        time_id: str = typer.Argument(..., help="Time entry id (GUID)."),
        project_id: str = typer.Option(..., "--project", help="Parent project id (GUID)."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        try:
            item = _projects().get_time(project_id, time_id)
        except Exception as e:
            typer.echo(f"Error fetching time entry {time_id}: {e}", err=True)
            raise typer.Exit(1)
        _emit_record(item, output_json)

    @time_entry.command("create", help="Create a time entry on a project from a JSON body.")
    def _create(
        project_id: str = typer.Option(..., "--project", help="Parent project id (GUID)."),
        data: Optional[str] = typer.Option(None, "--data", help="Time entry object as JSON (or -f / stdin)."),
        file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
    ):
        body = _read_data(data, file)
        _do_write(
            lambda: _projects().create_time(project_id, body),
            f"create time entry on project {project_id}", confirm="Create this time entry?",
            yes=yes, output_json=output_json,
        )

    @time_entry.command("update", help="Update a time entry (PUT; read-merge-write).")
    def _update(
        time_id: str = typer.Argument(..., help="Time entry id (GUID) to update."),
        project_id: str = typer.Option(..., "--project", help="Parent project id (GUID)."),
        data: Optional[str] = typer.Option(None, "--data", help="Partial JSON overlaying the fetched entry."),
        file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON overlay from a file."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
    ):
        papi = _projects()
        _merge_update(
            lambda: papi.get_time(project_id, time_id),
            lambda merged: papi.update_time(project_id, time_id, merged),
            data, file, {}, f"update time entry {time_id}", yes, output_json,
        )

    @time_entry.command("delete", help="Delete a time entry by id.")
    def _delete(
        time_id: str = typer.Argument(..., help="Time entry id (GUID)."),
        project_id: str = typer.Option(..., "--project", help="Parent project id (GUID)."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
    ):
        _do_write(
            lambda: _projects().delete_time(project_id, time_id),
            f"delete time entry {time_id}", confirm=f"Delete time entry {time_id}?",
            yes=yes, output_json=output_json,
        )


def _register_project_user(app: typer.Typer) -> None:
    project_user = typer.Typer(help="Xero project users (read-only).")
    app.add_typer(project_user, name="project-user")

    @project_user.command("list", help="List the project users.")
    def _list(
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        try:
            items = _projects().list_project_users()
        except Exception as e:
            typer.echo(f"Error fetching project users: {e}", err=True)
            raise typer.Exit(1)
        _emit_list(items, _PROJECT_USER_COLS, "project user", output_json)
