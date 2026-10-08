import os
from datetime import date, timedelta
from functools import wraps
from urllib.parse import urlparse

import certifi
import truststore
from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, session, url_for
from postgrest.exceptions import APIError
from supabase import Client, create_client

load_dotenv()
truststore.inject_into_ssl()
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
supabase_host = urlparse(os.getenv("SUPABASE_URL", "")).hostname
if supabase_host:
    bypass_hosts = ",".join(filter(None, [os.getenv("NO_PROXY", ""), supabase_host]))
    os.environ["NO_PROXY"] = bypass_hosts
    os.environ["no_proxy"] = bypass_hosts
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-only-change-me")


def is_auth_error(error):
    details = error.args[0] if error.args and isinstance(error.args[0], dict) else {}
    message = str(error).lower()
    return str(details.get("code", "")) in ("401", "PGRST301") or any(marker in message for marker in ("jwt", "not authenticated", "invalid token", "unauthorized"))


@app.errorhandler(APIError)
def handle_supabase_api_error(error):
    message = str(error)
    if is_auth_error(error):
        session.clear()
        if request.path.startswith("/api/"):
            return {"success": False, "error": "Authentication required."}, 401
        return redirect(url_for("login"))
    missing_schema = any(marker in message for marker in (
        "does not exist",
        "Could not find the table",
        "schema cache",
    ))
    if missing_schema and session.get("access_token"):
        app.logger.warning("Supabase schema migration required: %s", message)
        if request.path.startswith("/api/"):
            return {"success": False, "error": "Required Supabase migration is not applied."}, 503
        return render_template("setup_error.html"), 503
    raise error


def supabase_client() -> Client:
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError("Set SUPABASE_URL and SUPABASE_ANON_KEY in .env")
    return create_client(url, key)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("access_token"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def db():
    client = supabase_client()
    client.postgrest.auth(session.get("access_token", os.getenv("SUPABASE_ANON_KEY", "")))
    return client


def current_user():
    return session.get("user", {})


def add_notification(user_id, title, description, category="workspace"):
    """Best-effort notification write; workflow success is not hidden by a notification failure."""
    if not user_id:
        return
    try:
        db().table("notifications").insert({
            "user_id": user_id,
            "title": title,
            "description": description,
            "category": category,
        }).execute()
    except Exception:
        app.logger.exception("Notification write failed")


def execute_automation_rules(event, task=None):
    """Execute supported internal actions for an application event.

    Provider delivery is intentionally outside this function; integrations only
    record connection state until provider credentials and OAuth are configured.
    """
    if not task:
        return
    try:
        rules = db().table("automation_rules").select("*").eq("event", event).eq("enabled", True).execute().data or []
        for rule in rules:
            action = rule.get("action")
            title = task.get("title", "Task")
            if action == "notify_assignee":
                add_notification(task.get("assignee_id") or task.get("created_by"), "Automation notification", f"Automation rule {rule['name']} matched task: {title}.", "automation")
            elif action == "notify_manager":
                managers = db().table("profiles").select("id").in_("role", ["admin", "manager"]).execute().data or []
                for manager in managers:
                    add_notification(manager["id"], "Automation notification", f"Automation rule {rule['name']} matched task: {title}.", "automation")
            elif action == "move_to_review":
                db().table("tasks").update({"status": "review"}).eq("id", task["id"]).execute()
            elif action == "create_task":
                db().table("tasks").insert({
                    "project_id": task.get("project_id"),
                    "created_by": current_user().get("id"),
                    "title": f"Follow-up: {title}",
                    "description": f"Created by automation rule {rule['name']}.",
                    "status": "todo",
                    "priority": task.get("priority", "medium"),
                }).execute()
    except Exception:
        app.logger.exception("Automation execution failed")


def execute_overdue_automations(tasks):
    for task in tasks:
        execute_automation_rules("task_overdue", task)


def dashboard_snapshot():
    """Build the dashboard entirely from current Supabase rows."""
    client = db()
    projects = client.table("projects").select("*").order("created_at", desc=True).execute().data or []
    tasks = client.table("tasks").select("*").order("created_at", desc=True).execute().data or []
    members = client.table("profiles").select("*").order("full_name").execute().data or []
    methodology_metrics = client.table("methodology_metrics").select("*").order("name").execute().data or []
    today = date.today().isoformat()

    project_status = {status: sum(p.get("status") == status for p in projects) for status in ("active", "planning", "completed", "on_hold")}
    task_status = {status: sum(t.get("status") == status for t in tasks) for status in ("todo", "in_progress", "review", "done")}
    overdue = sum((t.get("due_date") or "9999-12-31") < today and t.get("status") != "done" for t in tasks)
    execute_overdue_automations([t for t in tasks if (t.get("due_date") or "9999-12-31") < today and t.get("status") != "done"])

    progress_by_project = {}
    for project in projects:
        project_tasks = [t for t in tasks if t.get("project_id") == project.get("id")]
        completed = sum(t.get("status") == "done" for t in project_tasks)
        progress_by_project[project.get("id")] = round((completed / len(project_tasks)) * 100) if project_tasks else 0

    summary = {
        "total_projects": len(projects),
        "active_projects": project_status["active"],
        "planning_projects": project_status["planning"],
        "completed_projects": project_status["completed"],
        "on_hold_projects": project_status["on_hold"],
        "total_tasks": len(tasks),
        "todo_tasks": task_status["todo"],
        "in_progress_tasks": task_status["in_progress"],
        "review_tasks": task_status["review"],
        "completed_tasks": task_status["done"],
        "open_tasks": len(tasks) - task_status["done"],
        "overdue_tasks": overdue,
        "team_members": len(members),
    }
    return {
        "summary": summary,
        "project_status": project_status,
        "task_status": task_status,
        "projects": projects,
        "tasks": tasks,
        "members": members,
        "methodology_metrics": methodology_metrics,
        "progress_by_project": progress_by_project,
    }


@app.context_processor
def inject_globals():
    return {"current_user": current_user(), "today": date.today().isoformat()}


@app.route("/", methods=["GET"])
def home():
    return redirect(url_for("dashboard" if session.get("access_token") else "login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        session.pop("_flashes", None)
    if request.method == "POST":
        try:
            result = supabase_client().auth.sign_in_with_password({
                "email": request.form["email"].strip(),
                "password": request.form["password"],
            })
            session["access_token"] = result.session.access_token
            session["refresh_token"] = result.session.refresh_token
            session["user"] = {"id": result.user.id, "email": result.user.email}
            return redirect(url_for("dashboard"))
        except Exception as error:
            app.logger.exception("Supabase login failed")
            message = str(error).lower()
            if "email_not_confirmed" in message or "email not confirmed" in message:
                flash("This account is not confirmed in Supabase. Enable Auto confirm user or confirm the email.", "error")
            elif "invalid login credentials" in message:
                flash("Email or password is incorrect. Use a user from Supabase Authentication → Users.", "error")
            elif "certificate_verify_failed" in message or "ssl" in message:
                flash("Secure connection to Supabase failed on this computer. Restart the app after updating Python certificates.", "error")
            else:
                flash(f"Supabase login failed: {str(error)[:240]}", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    try:
        snapshot = dashboard_snapshot()
    except APIError as error:
        if is_auth_error(error):
            session.clear()
            return redirect(url_for("login"))
        app.logger.exception("Supabase dashboard query failed")
        return render_template("setup_error.html"), 503
    except Exception:
        app.logger.exception("Supabase dashboard query failed")
        return render_template("setup_error.html"), 503
    return render_template("dashboard.html", **snapshot)


@app.route("/api/dashboard")
@login_required
def dashboard_api():
    try:
        snapshot = dashboard_snapshot()
    except APIError as error:
        if is_auth_error(error):
            session.clear()
            return {"success": False, "error": "Authentication required."}, 401
        app.logger.exception("Supabase dashboard API query failed")
        return {"success": False, "error": "Unable to load dashboard data."}, 503
    except Exception:
        app.logger.exception("Supabase dashboard API query failed")
        return {"success": False, "error": "Unable to load dashboard data."}, 503
    return {
        "success": True,
        "data": {
            "summary": snapshot["summary"],
            "project_status": snapshot["project_status"],
            "task_status": snapshot["task_status"],
            "methodology_metrics": snapshot["methodology_metrics"],
            "recent_projects": snapshot["projects"][:8],
            "recent_tasks": snapshot["tasks"][:8],
            "progress_by_project": snapshot["progress_by_project"],
        },
    }


def request_payload():
    return request.get_json(silent=True) or request.form


def paged_rows(rows):
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except ValueError:
        page = 1
    try:
        page_size = min(max(int(request.args.get("page_size", 50)), 1), 100)
    except ValueError:
        page_size = 50
    total = len(rows)
    start = (page - 1) * page_size
    return rows[start:start + page_size], {"page": page, "page_size": page_size, "total": total, "pages": (total + page_size - 1) // page_size}


@app.route("/api/projects", methods=["GET", "POST"])
@login_required
def projects_api():
    client = db()
    if request.method == "POST":
        payload = request_payload()
        name = str(payload.get("name", "")).strip()
        if not name:
            return {"success": False, "error": "Project name is required."}, 400
        row = client.table("projects").insert({
            "name": name,
            "description": str(payload.get("description", "")).strip(),
            "status": payload.get("status", "active"),
            "start_date": payload.get("start_date") or date.today().isoformat(),
            "due_date": payload.get("due_date") or None,
            "owner_id": current_user().get("id"),
        }).execute().data or []
        add_notification(current_user().get("id"), "Project created", f"Project {name} was created.", "project")
        return {"success": True, "data": row[0] if row else None}, 201
    rows = client.table("projects").select("*").order("created_at", desc=True).execute().data or []
    query = request.args.get("q", "").strip().lower()
    status = request.args.get("status")
    if query:
        rows = [row for row in rows if query in (row.get("name") or "").lower() or query in (row.get("description") or "").lower()]
    if status:
        rows = [row for row in rows if row.get("status") == status]
    rows, pagination = paged_rows(rows)
    return {"success": True, "data": rows, "pagination": pagination, "total": pagination["total"]}


@app.route("/api/tasks", methods=["GET", "POST"])
@login_required
def tasks_api():
    client = db()
    if request.method == "POST":
        payload = request_payload()
        title = str(payload.get("title", "")).strip()
        if not title:
            return {"success": False, "error": "Task title is required."}, 400
        assignee_id = payload.get("assignee_id") or None
        row = client.table("tasks").insert({
            "title": title,
            "description": str(payload.get("description", "")).strip(),
            "status": payload.get("status", "todo"),
            "priority": payload.get("priority", "medium"),
            "due_date": payload.get("due_date") or None,
            "project_id": payload.get("project_id") or None,
            "assignee_id": assignee_id,
            "created_by": current_user().get("id"),
            "start_date": payload.get("start_date") or None,
            "end_date": payload.get("end_date") or None,
            "estimated_hours": payload.get("estimated_hours") or 0,
            "progress": payload.get("progress") or 0,
            "sprint_id": payload.get("sprint_id") or None,
            "release_id": payload.get("release_id") or None,
        }).execute().data or []
        add_notification(assignee_id or current_user().get("id"), "Task assigned", f"Task {title} was added to your workspace.", "task")
        if row:
            execute_automation_rules("task_created", row[0])
        return {"success": True, "data": row[0] if row else None}, 201
    rows = client.table("tasks").select("*, projects(name), profiles:assignee_id(full_name,email)").order("created_at", desc=True).execute().data or []
    query = request.args.get("q", "").strip().lower()
    if query:
        rows = [row for row in rows if query in (row.get("title") or "").lower() or query in (row.get("description") or "").lower()]
    for field in ("status", "priority", "project_id", "assignee_id"):
        if request.args.get(field):
            rows = [row for row in rows if str(row.get(field)) == request.args[field]]
    rows, pagination = paged_rows(rows)
    return {"success": True, "data": rows, "pagination": pagination, "total": pagination["total"]}


@app.route("/api/issues", methods=["GET", "POST"])
@login_required
def issues_api():
    client = db()
    if request.method == "POST":
        payload = request_payload()
        title = str(payload.get("title", "")).strip()
        if not title:
            return {"success": False, "error": "Issue title is required."}, 400
        assignee_id = payload.get("assignee_id") or None
        issue_data = {
            "title": title,
            "description": str(payload.get("description", "")).strip(),
            "project_id": payload.get("project_id") or None,
            "assignee_id": assignee_id,
            "created_by": current_user().get("id"),
            "issue_type": payload.get("issue_type", "bug"),
            "priority": payload.get("priority", "medium"),
            "severity": payload.get("severity", "medium"),
        }
        if payload.get("release_id"):
            issue_data["release_id"] = payload["release_id"]
        row = client.table("issues").insert(issue_data).execute().data or []
        add_notification(assignee_id or current_user().get("id"), "Issue created", f"Issue {title} was added to the workspace.", "issue")
        return {"success": True, "data": row[0] if row else None}, 201
    query = client.table("issues").select("*, projects(name), profiles:assignee_id(full_name,email)")
    if request.args.get("status"):
        query = query.eq("status", request.args["status"])
    if request.args.get("priority"):
        query = query.eq("priority", request.args["priority"])
    rows = query.order("created_at", desc=True).execute().data or []
    search = request.args.get("q", "").strip().lower()
    if search:
        rows = [row for row in rows if search in (row.get("title") or "").lower() or search in (row.get("description") or "").lower()]
    rows, pagination = paged_rows(rows)
    return {"success": True, "data": rows, "pagination": pagination, "total": pagination["total"]}


@app.route("/api/notifications")
@login_required
def notifications_api():
    rows = db().table("notifications").select("*").eq("user_id", current_user().get("id")).order("created_at", desc=True).execute().data or []
    return {"success": True, "data": rows, "unread": sum(not row.get("read_at") for row in rows)}


@app.route("/api/sprints")
@login_required
def sprints_api():
    rows = db().table("sprints").select("*, projects(name)").order("created_at", desc=True).execute().data or []
    return {"success": True, "data": rows, "total": len(rows)}


@app.route("/api/releases")
@login_required
def releases_api():
    rows = db().table("releases").select("*, projects(name)").order("created_at", desc=True).execute().data or []
    return {"success": True, "data": rows, "total": len(rows)}


@app.route("/api/capacity")
@login_required
def capacity_api():
    client = db()
    members = client.table("profiles").select("id,full_name,email").order("full_name").execute().data or []
    tasks_rows = client.table("tasks").select("assignee_id,estimated_hours,status").execute().data or []
    capacities = client.table("team_capacity").select("*").order("week_start", desc=True).execute().data or []
    latest = {}
    for row in capacities:
        latest.setdefault(row["user_id"], row)
    data = []
    for member in members:
        allocated = sum(float(task.get("estimated_hours") or 0) for task in tasks_rows if task.get("assignee_id") == member["id"] and task.get("status") != "done")
        available = float(latest.get(member["id"], {}).get("capacity_hours") or 0)
        data.append({**member, "capacity_hours": available, "allocated_hours": allocated, "available_hours": max(available - allocated, 0)})
    return {"success": True, "data": data, "total": len(data)}


@app.route("/api/gantt/<project_id>")
@login_required
def gantt_api(project_id):
    client = db()
    tasks_rows = client.table("tasks").select("*, profiles:assignee_id(full_name,email)").eq("project_id", project_id).order("start_date").execute().data or []
    dependencies = client.table("task_dependencies").select("*").eq("project_id", project_id).execute().data or []
    milestones = client.table("project_milestones").select("*").eq("project_id", project_id).order("due_date").execute().data or []
    return {"success": True, "data": {"tasks": tasks_rows, "dependencies": dependencies, "milestones": milestones}}


@app.route("/projects", methods=["GET", "POST"])
@login_required
def projects():
    client = db()
    if request.method == "POST":
        project_name = request.form["name"].strip()
        client.table("projects").insert({
            "name": project_name,
            "description": request.form.get("description", "").strip(),
            "status": request.form.get("status", "active"),
            "start_date": request.form.get("start_date") or date.today().isoformat(),
            "due_date": request.form.get("due_date") or (date.today() + timedelta(days=30)).isoformat(),
            "owner_id": current_user().get("id"),
        }).execute()
        add_notification(current_user().get("id"), "Project created", f"Project {project_name} was created.", "project")
        flash("Project created.", "success")
        return redirect(url_for("projects"))
    items = client.table("projects").select("*").order("created_at", desc=True).execute().data or []
    return render_template("projects.html", projects=items)


@app.route("/projects/<project_id>/delete", methods=["POST"])
@login_required
def delete_project(project_id):
    db().table("projects").delete().eq("id", project_id).execute()
    flash("Project removed.", "success")
    return redirect(url_for("projects"))


@app.route("/projects/<project_id>/edit", methods=["POST"])
@login_required
def edit_project(project_id):
    payload = {
        "name": request.form["name"].strip(),
        "description": request.form.get("description", "").strip(),
        "status": request.form.get("status", "active"),
        "start_date": request.form.get("start_date") or date.today().isoformat(),
        "due_date": request.form.get("due_date") or None,
    }
    db().table("projects").update(payload).eq("id", project_id).execute()
    add_notification(current_user().get("id"), "Project updated", f"Project {payload['name']} was updated.", "project")
    flash("Project updated.", "success")
    return redirect(request.referrer or url_for("projects"))


@app.route("/tasks", methods=["GET", "POST"])
@login_required
def tasks():
    client = db()
    if request.method == "POST":
        assignee_id = request.form.get("assignee_id") or None
        task_title = request.form["title"].strip()
        created = client.table("tasks").insert({
            "title": task_title,
            "description": request.form.get("description", "").strip(),
            "status": request.form.get("status", "todo"),
            "priority": request.form.get("priority", "medium"),
            "due_date": request.form.get("due_date") or None,
            "project_id": request.form.get("project_id") or None,
            "assignee_id": assignee_id,
            "created_by": current_user().get("id"),
        }).execute().data or []
        add_notification(assignee_id or current_user().get("id"), "Task assigned", f"Task {task_title} was added to your workspace.", "task")
        if created:
            execute_automation_rules("task_created", created[0])
        flash("Task added.", "success")
        return redirect(url_for("tasks"))
    task_rows = client.table("tasks").select("*, projects(name), profiles:assignee_id(full_name,email)").order("due_date").execute().data or []
    project_rows = client.table("projects").select("id,name").order("name").execute().data or []
    member_rows = client.table("profiles").select("id,full_name,email").order("full_name").execute().data or []
    return render_template("tasks.html", tasks=task_rows, projects=project_rows, members=member_rows)


@app.route("/tasks/<task_id>/status", methods=["POST"])
@login_required
def update_task_status(task_id):
    client = db()
    task_rows = client.table("tasks").select("*").eq("id", task_id).limit(1).execute().data or []
    client.table("tasks").update({"status": request.form["status"]}).eq("id", task_id).execute()
    add_notification(current_user().get("id"), "Task status updated", f"Task status changed to {request.form['status']}.", "task")
    if task_rows and request.form["status"] == "done":
        task_rows[0]["status"] = "done"
        execute_automation_rules("task_completed", task_rows[0])
    return redirect(request.referrer or url_for("tasks"))


@app.route("/tasks/<task_id>/edit", methods=["POST"])
@login_required
def edit_task(task_id):
    payload = {
        "title": request.form["title"].strip(),
        "description": request.form.get("description", "").strip(),
        "status": request.form.get("status", "todo"),
        "priority": request.form.get("priority", "medium"),
        "due_date": request.form.get("due_date") or None,
        "project_id": request.form.get("project_id") or None,
        "assignee_id": request.form.get("assignee_id") or None,
        "start_date": request.form.get("start_date") or None,
        "end_date": request.form.get("end_date") or None,
        "estimated_hours": request.form.get("estimated_hours") or 0,
        "progress": request.form.get("progress") or 0,
        "sprint_id": request.form.get("sprint_id") or None,
        "release_id": request.form.get("release_id") or None,
    }
    db().table("tasks").update(payload).eq("id", task_id).execute()
    add_notification(current_user().get("id"), "Task updated", f"Task {payload['title']} was updated.", "task")
    flash("Task updated.", "success")
    return redirect(request.referrer or url_for("tasks"))


@app.route("/tasks/<task_id>/delete", methods=["POST"])
@login_required
def delete_task(task_id):
    db().table("tasks").delete().eq("id", task_id).execute()
    flash("Task deleted.", "success")
    return redirect(request.referrer or url_for("tasks"))


@app.route("/timeline")
@login_required
def timeline():
    projects = db().table("projects").select("*").order("start_date").execute().data or []
    dated_projects = []
    for project in projects:
        start_value = project.get("start_date") or date.today().isoformat()
        end_value = project.get("due_date") or start_value
        dated_projects.append((date.fromisoformat(start_value), date.fromisoformat(end_value)))
    if dated_projects:
        range_start = min(item[0] for item in dated_projects)
        range_end = max(item[1] for item in dated_projects)
        range_days = max((range_end - range_start).days + 1, 1)
        for project in projects:
            start_value = project.get("start_date") or range_start.isoformat()
            end_value = project.get("due_date") or start_value
            start_day = (date.fromisoformat(start_value) - range_start).days
            duration = max((date.fromisoformat(end_value) - date.fromisoformat(start_value)).days + 1, 1)
            project["timeline_left"] = round(start_day / range_days * 100, 2)
            project["timeline_width"] = round(min(duration / range_days * 100, 100 - project["timeline_left"]), 2)
    return render_template("timeline.html", projects=projects)


@app.route("/workload")
@login_required
def workload():
    client = db()
    members = client.table("profiles").select("*").order("full_name").execute().data or []
    tasks = client.table("tasks").select("*, profiles:assignee_id(full_name,email)").neq("status", "done").execute().data or []
    counts = {member["id"]: sum(task.get("assignee_id") == member["id"] for task in tasks) for member in members}
    max_count = max(counts.values(), default=0)
    for member in members:
        member["assigned_count"] = counts.get(member["id"], 0)
        member["load_percent"] = round(member["assigned_count"] / max_count * 100) if max_count else 0
    return render_template("workload.html", members=members, tasks=tasks)


@app.route("/automations", methods=["GET", "POST"])
@login_required
def automations():
    client = db()
    if request.method == "POST":
        client.table("automation_rules").insert({
            "name": request.form["name"].strip(),
            "event": request.form["event"],
            "action": request.form["action"],
            "created_by": current_user().get("id"),
        }).execute()
        flash("Automation created.", "success")
        return redirect(url_for("automations"))
    rules = client.table("automation_rules").select("*").order("created_at", desc=True).execute().data or []
    return render_template("automations.html", rules=rules)


@app.route("/automations/<rule_id>/toggle", methods=["POST"])
@login_required
def toggle_automation(rule_id):
    rule = db().table("automation_rules").select("enabled").eq("id", rule_id).single().execute().data
    db().table("automation_rules").update({"enabled": not rule["enabled"]}).eq("id", rule_id).execute()
    return redirect(url_for("automations"))


@app.route("/automations/<rule_id>/delete", methods=["POST"])
@login_required
def delete_automation(rule_id):
    db().table("automation_rules").delete().eq("id", rule_id).execute()
    flash("Automation deleted.", "success")
    return redirect(request.referrer or url_for("automations"))


@app.route("/integrations", methods=["GET", "POST"])
@login_required
def integrations():
    client = db()
    if request.method == "POST":
        client.table("integrations").upsert({
            "provider": request.form["provider"],
            "label": request.form.get("label", request.form["provider"]),
            "connected_by": current_user().get("id"),
            "status": "connected",
        }, on_conflict="provider").execute()
        flash("Integration connected.", "success")
        return redirect(url_for("integrations"))
    connected = client.table("integrations").select("*").order("created_at", desc=True).execute().data or []
    return render_template("integrations.html", connected=connected)


@app.route("/integrations/<integration_id>/disconnect", methods=["POST"])
@login_required
def disconnect_integration(integration_id):
    db().table("integrations").delete().eq("id", integration_id).execute()
    flash("Integration disconnected.", "success")
    return redirect(request.referrer or url_for("integrations"))


@app.route("/issues", methods=["GET", "POST"])
@login_required
def issues():
    client = db()
    if request.method == "POST":
        assignee_id = request.form.get("assignee_id") or None
        issue_title = request.form["title"].strip()
        issue_data = {
            "title": issue_title,
            "description": request.form.get("description", "").strip(),
            "project_id": request.form.get("project_id") or None,
            "assignee_id": assignee_id,
            "created_by": current_user().get("id"),
            "issue_type": request.form.get("issue_type", "bug"),
            "priority": request.form.get("priority", "medium"),
            "severity": request.form.get("severity", "medium"),
        }
        if request.form.get("release_id"):
            issue_data["release_id"] = request.form["release_id"]
        client.table("issues").insert(issue_data).execute()
        add_notification(assignee_id or current_user().get("id"), "Issue created", f"Issue {issue_title} was added to the workspace.", "issue")
        flash("Issue created.", "success")
        return redirect(url_for("issues"))
    rows = client.table("issues").select("*, projects(name), profiles:assignee_id(full_name,email)").order("created_at", desc=True).execute().data or []
    projects = client.table("projects").select("id,name").order("name").execute().data or []
    members = client.table("profiles").select("id,full_name,email").order("full_name").execute().data or []
    return render_template("issues.html", issues=rows, projects=projects, members=members)


@app.route("/issues/<issue_id>/status", methods=["POST"])
@login_required
def update_issue_status(issue_id):
    status = request.form.get("status", "open")
    payload = {"status": status}
    if status in ("resolved", "closed"):
        payload["resolved_at"] = date.today().isoformat()
    else:
        payload["resolved_at"] = None
    db().table("issues").update(payload).eq("id", issue_id).execute()
    add_notification(current_user().get("id"), "Issue status updated", f"Issue status changed to {status}.", "issue")
    return redirect(request.referrer or url_for("issues"))


@app.route("/issues/<issue_id>/delete", methods=["POST"])
@login_required
def delete_issue(issue_id):
    db().table("issues").delete().eq("id", issue_id).execute()
    flash("Issue deleted.", "success")
    return redirect(url_for("issues"))


@app.route("/notifications")
@login_required
def notifications():
    rows = db().table("notifications").select("*").eq("user_id", current_user().get("id")).order("created_at", desc=True).execute().data or []
    return render_template("notifications.html", notifications=rows)


@app.route("/notifications/<notification_id>/read", methods=["POST"])
@login_required
def mark_notification_read(notification_id):
    db().table("notifications").update({"read_at": date.today().isoformat()}).eq("id", notification_id).eq("user_id", current_user().get("id")).execute()
    return redirect(request.referrer or url_for("notifications"))


@app.route("/notifications/read-all", methods=["POST"])
@login_required
def mark_all_notifications_read():
    db().table("notifications").update({"read_at": date.today().isoformat()}).eq("user_id", current_user().get("id")).is_("read_at", "null").execute()
    return redirect(url_for("notifications"))


@app.route("/sprints", methods=["GET", "POST"])
@login_required
def sprints():
    client = db()
    if request.method == "POST":
        sprint_name = request.form["name"].strip()
        client.table("sprints").insert({
            "project_id": request.form["project_id"],
            "name": sprint_name,
            "goal": request.form.get("goal", "").strip(),
            "start_date": request.form.get("start_date") or None,
            "end_date": request.form.get("end_date") or None,
            "created_by": current_user().get("id"),
        }).execute()
        add_notification(current_user().get("id"), "Sprint created", f"Sprint {sprint_name} was created.", "sprint")
        flash("Sprint created.", "success")
        return redirect(url_for("sprints"))
    rows = client.table("sprints").select("*, projects(name)").order("created_at", desc=True).execute().data or []
    projects = client.table("projects").select("id,name").order("name").execute().data or []
    task_rows = client.table("tasks").select("id,sprint_id,status").execute().data or []
    for sprint in rows:
        sprint_tasks = [t for t in task_rows if t.get("sprint_id") == sprint.get("id")]
        sprint["task_count"] = len(sprint_tasks)
        sprint["completed_count"] = sum(t.get("status") == "done" for t in sprint_tasks)
    return render_template("sprints.html", sprints=rows, projects=projects)


@app.route("/sprints/<sprint_id>/<action>", methods=["POST"])
@login_required
def sprint_action(sprint_id, action):
    transitions = {"start": "active", "complete": "completed", "cancel": "cancelled"}
    if action not in transitions:
        return "Invalid sprint action", 400
    next_status = transitions[action]
    db().table("sprints").update({"status": next_status, "updated_at": date.today().isoformat()}).eq("id", sprint_id).execute()
    add_notification(current_user().get("id"), "Sprint updated", f"Sprint status changed to {next_status}.", "sprint")
    return redirect(url_for("sprints"))


@app.route("/sprints/<sprint_id>/edit", methods=["POST"])
@login_required
def edit_sprint(sprint_id):
    payload = {
        "project_id": request.form["project_id"],
        "name": request.form["name"].strip(),
        "goal": request.form.get("goal", "").strip(),
        "start_date": request.form.get("start_date") or None,
        "end_date": request.form.get("end_date") or None,
        "updated_at": date.today().isoformat(),
    }
    db().table("sprints").update(payload).eq("id", sprint_id).execute()
    add_notification(current_user().get("id"), "Sprint updated", f"Sprint {payload['name']} was updated.", "sprint")
    flash("Sprint updated.", "success")
    return redirect(request.referrer or url_for("sprints"))


@app.route("/sprints/<sprint_id>/delete", methods=["POST"])
@login_required
def delete_sprint(sprint_id):
    db().table("sprints").delete().eq("id", sprint_id).execute()
    flash("Sprint deleted.", "success")
    return redirect(request.referrer or url_for("sprints"))


@app.route("/sprints/<sprint_id>/tasks/<task_id>", methods=["POST"])
@login_required
def assign_sprint_task(sprint_id, task_id):
    action = request.form.get("action", "add")
    db().table("tasks").update({"sprint_id": sprint_id if action == "add" else None}).eq("id", task_id).execute()
    add_notification(current_user().get("id"), "Sprint task updated", f"Task was {'added to' if action == 'add' else 'removed from'} the sprint.", "sprint")
    return redirect(request.referrer or url_for("sprints"))


@app.route("/releases", methods=["GET", "POST"])
@login_required
def releases():
    client = db()
    if request.method == "POST":
        release_name = request.form["name"].strip()
        client.table("releases").insert({
            "project_id": request.form.get("project_id") or None,
            "name": release_name,
            "version": request.form["version"].strip(),
            "description": request.form.get("description", "").strip(),
            "release_date": request.form.get("release_date") or None,
            "created_by": current_user().get("id"),
        }).execute()
        add_notification(current_user().get("id"), "Release created", f"Release {release_name} was created.", "release")
        flash("Release created.", "success")
        return redirect(url_for("releases"))
    rows = client.table("releases").select("*, projects(name)").order("created_at", desc=True).execute().data or []
    projects = client.table("projects").select("id,name").order("name").execute().data or []
    return render_template("releases.html", releases=rows, projects=projects)


@app.route("/releases/<release_id>/status", methods=["POST"])
@login_required
def update_release_status(release_id):
    status = request.form.get("status", "unreleased")
    db().table("releases").update({"status": status, "updated_at": date.today().isoformat()}).eq("id", release_id).execute()
    add_notification(current_user().get("id"), "Release status updated", f"Release status changed to {status}.", "release")
    return redirect(url_for("releases"))


@app.route("/releases/<release_id>/edit", methods=["POST"])
@login_required
def edit_release(release_id):
    payload = {
        "project_id": request.form.get("project_id") or None,
        "name": request.form["name"].strip(),
        "version": request.form["version"].strip(),
        "description": request.form.get("description", "").strip(),
        "release_date": request.form.get("release_date") or None,
        "updated_at": date.today().isoformat(),
    }
    db().table("releases").update(payload).eq("id", release_id).execute()
    add_notification(current_user().get("id"), "Release updated", f"Release {payload['name']} was updated.", "release")
    flash("Release updated.", "success")
    return redirect(request.referrer or url_for("releases"))


@app.route("/releases/<release_id>/delete", methods=["POST"])
@login_required
def delete_release(release_id):
    db().table("releases").delete().eq("id", release_id).execute()
    flash("Release deleted.", "success")
    return redirect(request.referrer or url_for("releases"))


@app.route("/releases/<release_id>/tasks/<task_id>", methods=["POST"])
@login_required
def assign_release_task(release_id, task_id):
    action = request.form.get("action", "add")
    db().table("tasks").update({"release_id": release_id if action == "add" else None}).eq("id", task_id).execute()
    add_notification(current_user().get("id"), "Release task updated", f"Task was {'added to' if action == 'add' else 'removed from'} the release.", "release")
    return redirect(request.referrer or url_for("releases"))


@app.route("/releases/<release_id>/issues/<issue_id>", methods=["POST"])
@login_required
def assign_release_issue(release_id, issue_id):
    action = request.form.get("action", "add")
    db().table("issues").update({"release_id": release_id if action == "add" else None}).eq("id", issue_id).execute()
    add_notification(current_user().get("id"), "Release issue updated", f"Issue was {'added to' if action == 'add' else 'removed from'} the release.", "release")
    return redirect(request.referrer or url_for("releases"))


@app.route("/capacity", methods=["GET", "POST"])
@login_required
def capacity():
    client = db()
    if request.method == "POST":
        client.table("team_capacity").upsert({
            "user_id": request.form["user_id"],
            "week_start": request.form["week_start"],
            "capacity_hours": request.form["capacity_hours"],
        }, on_conflict="user_id,week_start").execute()
        flash("Capacity saved.", "success")
        return redirect(url_for("capacity"))
    members = client.table("profiles").select("id,full_name,email").order("full_name").execute().data or []
    tasks_rows = client.table("tasks").select("assignee_id,estimated_hours,status").execute().data or []
    capacities = client.table("team_capacity").select("*").order("week_start", desc=True).execute().data or []
    latest_capacity = {}
    for row in capacities:
        latest_capacity.setdefault(row["user_id"], row)
    total_capacity = sum(float(latest_capacity.get(m["id"], {}).get("capacity_hours", 0)) for m in members)
    member_rows = []
    for member in members:
        allocated = sum(float(t.get("estimated_hours") or 0) for t in tasks_rows if t.get("assignee_id") == member["id"] and t.get("status") != "done")
        capacity_hours = float(latest_capacity.get(member["id"], {}).get("capacity_hours", 0))
        member_rows.append({**member, "capacity_hours": capacity_hours, "allocated_hours": allocated, "available_hours": max(capacity_hours - allocated, 0), "utilization": round(allocated / capacity_hours * 100) if capacity_hours else 0})
    allocated_total = sum(r["allocated_hours"] for r in member_rows)
    return render_template("capacity.html", members=member_rows, total_capacity=total_capacity, allocated_total=allocated_total, available_total=max(total_capacity - allocated_total, 0))


@app.route("/reports")
@login_required
def reports():
    snapshot = dashboard_snapshot()
    return render_template("reports.html", summary=snapshot["summary"], project_status=snapshot["project_status"], task_status=snapshot["task_status"])


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    client = db()
    if request.method == "POST":
        action = request.form.get("action", "workspace")
        if action == "profile":
            client.table("profiles").update({
                "full_name": request.form["full_name"].strip(),
                "avatar_color": request.form.get("avatar_color", "#5eead4"),
            }).eq("id", current_user().get("id")).execute()
            session["user"]["full_name"] = request.form["full_name"].strip()
            flash("Profile saved.", "success")
        elif action == "preferences":
            client.table("user_preferences").upsert({
                "user_id": current_user().get("id"),
                "email_notifications": request.form.get("email_notifications") == "on",
                "in_app_notifications": request.form.get("in_app_notifications") == "on",
                "digest_frequency": request.form.get("digest_frequency", "daily"),
            }, on_conflict="user_id").execute()
            flash("Notification preferences saved.", "success")
        else:
            client.table("workspace_settings").upsert({
                "workspace_key": "default",
                "workspace_name": request.form["workspace_name"].strip(),
                "timezone": request.form.get("timezone", "Asia/Calcutta"),
                "date_format": request.form.get("date_format", "YYYY-MM-DD"),
                "updated_by": current_user().get("id"),
            }, on_conflict="workspace_key").execute()
            flash("Workspace settings saved.", "success")
        return redirect(url_for("settings"))
    settings_rows = client.table("workspace_settings").select("*").eq("workspace_key", "default").limit(1).execute().data or []
    row = settings_rows[0] if settings_rows else {}
    profile_rows = client.table("profiles").select("*").eq("id", current_user().get("id")).limit(1).execute().data or []
    profile = profile_rows[0] if profile_rows else {}
    try:
        preferences_rows = client.table("user_preferences").select("*").eq("user_id", current_user().get("id")).limit(1).execute().data or []
        preferences = preferences_rows[0] if preferences_rows else {}
    except APIError:
        preferences = {}
    members = client.table("profiles").select("id,email,full_name,role").order("full_name").execute().data or []
    try:
        permissions = client.table("role_permissions").select("*").order("role,permission").execute().data or []
    except APIError:
        permissions = []
    return render_template("settings.html", settings=row, profile=profile, preferences=preferences, members=members, permissions=permissions)


@app.route("/settings/password", methods=["POST"])
@login_required
def change_password():
    new_password = request.form.get("new_password", "")
    confirmation = request.form.get("confirm_password", "")
    if len(new_password) < 8 or new_password != confirmation:
        flash("Password must be at least 8 characters and both fields must match.", "error")
        return redirect(url_for("settings"))
    client = supabase_client()
    client.auth.set_session(session["access_token"], session.get("refresh_token", ""))
    client.auth.update_user({"password": new_password})
    flash("Password updated through Supabase Auth.", "success")
    return redirect(url_for("settings"))


@app.route("/settings/users/<user_id>/role", methods=["POST"])
@login_required
def update_member_role(user_id):
    actor = db().table("profiles").select("role").eq("id", current_user().get("id")).limit(1).execute().data or []
    if not actor or actor[0].get("role") != "admin":
        return "Administrator permission required.", 403
    role = request.form.get("role", "member")
    if role not in ("admin", "manager", "member"):
        return "Invalid role.", 400
    db().table("profiles").update({"role": role}).eq("id", user_id).execute()
    flash("Member role updated.", "success")
    return redirect(url_for("settings"))


@app.route("/settings/permissions/<role>/<permission>", methods=["POST"])
@login_required
def update_role_permission(role, permission):
    actor = db().table("profiles").select("role").eq("id", current_user().get("id")).limit(1).execute().data or []
    if not actor or actor[0].get("role") != "admin":
        return "Administrator permission required.", 403
    if role not in ("admin", "manager", "member"):
        return "Invalid role.", 400
    db().table("role_permissions").update({"enabled": request.form.get("enabled") == "on", "updated_at": date.today().isoformat()}).eq("role", role).eq("permission", permission).execute()
    flash("Permission updated.", "success")
    return redirect(url_for("settings"))


@app.route("/gantt")
@login_required
def gantt():
    client = db()
    projects = client.table("projects").select("id,name").order("name").execute().data or []
    project_id = request.args.get("project_id") or (projects[0]["id"] if projects else None)
    tasks_rows = client.table("tasks").select("*, profiles:assignee_id(full_name,email)").eq("project_id", project_id).order("start_date").execute().data if project_id else []
    dependencies = client.table("task_dependencies").select("*").eq("project_id", project_id).execute().data if project_id else []
    tasks_rows = tasks_rows or []
    milestones = client.table("project_milestones").select("*").eq("project_id", project_id).order("due_date").execute().data if project_id else []
    dated_tasks = []
    for task in tasks_rows:
        start_value = task.get("start_date") or task.get("due_date")
        end_value = task.get("end_date") or task.get("due_date") or start_value
        if start_value:
            dated_tasks.append((date.fromisoformat(start_value), date.fromisoformat(end_value or start_value)))
    if dated_tasks:
        range_start = min(item[0] for item in dated_tasks)
        range_end = max(item[1] for item in dated_tasks)
        range_days = max((range_end - range_start).days + 1, 1)
        for task in tasks_rows:
            start_value = task.get("start_date") or task.get("due_date")
            end_value = task.get("end_date") or task.get("due_date") or start_value
            if start_value:
                start_day = (date.fromisoformat(start_value) - range_start).days
                duration = max((date.fromisoformat(end_value or start_value) - date.fromisoformat(start_value)).days + 1, 1)
                task["gantt_left"] = round(start_day / range_days * 100, 2)
                task["gantt_width"] = round(min(duration / range_days * 100, 100 - task["gantt_left"]), 2)
            else:
                task["gantt_left"], task["gantt_width"] = 0, 0
    return render_template("gantt.html", projects=projects, project_id=project_id, tasks=tasks_rows, dependencies=dependencies or [], milestones=milestones or [])


@app.route("/gantt/milestones", methods=["POST"])
@login_required
def create_milestone():
    project_id = request.form["project_id"]
    name = request.form["name"].strip()
    if not name:
        flash("Milestone name is required.", "error")
        return redirect(url_for("gantt", project_id=project_id))
    db().table("project_milestones").insert({
        "project_id": project_id,
        "name": name,
        "status": request.form.get("status", "planned"),
        "due_date": request.form.get("due_date") or None,
    }).execute()
    add_notification(current_user().get("id"), "Milestone created", f"Milestone {name} was created.", "gantt")
    flash("Milestone created.", "success")
    return redirect(url_for("gantt", project_id=project_id))


@app.route("/gantt/milestones/<milestone_id>/delete", methods=["POST"])
@login_required
def delete_milestone(milestone_id):
    db().table("project_milestones").delete().eq("id", milestone_id).execute()
    flash("Milestone deleted.", "success")
    return redirect(request.referrer or url_for("gantt"))


@app.route("/gantt/dependencies", methods=["POST"])
@login_required
def create_dependency():
    predecessor = request.form["predecessor_task_id"]
    successor = request.form["successor_task_id"]
    project_id = request.form["project_id"]
    if predecessor == successor:
        flash("A task cannot depend on itself.", "error")
        return redirect(url_for("gantt", project_id=project_id))
    client = db()
    existing = client.table("task_dependencies").select("predecessor_task_id,successor_task_id").eq("project_id", project_id).execute().data or []
    edges = {(row["predecessor_task_id"], row["successor_task_id"]) for row in existing}
    if (predecessor, successor) in edges:
        flash("That dependency already exists.", "error")
        return redirect(url_for("gantt", project_id=project_id))
    edges.add((predecessor, successor))
    graph = {}
    for left, right in edges:
        graph.setdefault(left, []).append(right)
    visiting, visited = set(), set()
    def has_cycle(node):
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(has_cycle(child) for child in graph.get(node, [])):
            return True
        visiting.remove(node)
        visited.add(node)
        return False
    if any(has_cycle(node) for node in graph):
        flash("That dependency would create a circular workflow.", "error")
        return redirect(url_for("gantt", project_id=project_id))
    client.table("task_dependencies").insert({"project_id": project_id, "predecessor_task_id": predecessor, "successor_task_id": successor, "dependency_type": request.form.get("dependency_type", "FS")}).execute()
    flash("Dependency created.", "success")
    return redirect(url_for("gantt", project_id=project_id))


@app.route("/gantt/dependencies/<dependency_id>/delete", methods=["POST"])
@login_required
def delete_dependency(dependency_id):
    db().table("task_dependencies").delete().eq("id", dependency_id).execute()
    return redirect(request.referrer or url_for("gantt"))


@app.route("/legacy/<module>")
@login_required
def legacy_module(module):
    try:
        client = db()
        module_row = client.table("pmt_modules").select("*").eq("slug", module).single().execute().data
        item_rows = client.table("pmt_module_items").select("*").eq("module_id", module_row["id"]).order("position").execute().data or []
    except Exception:
        app.logger.exception("Supabase module query failed")
        return render_template("setup_error.html"), 503
    return render_template("legacy_module.html", title=module_row["title"], description=module_row["description"], delivery_mode=module_row["delivery_mode"], items=item_rows, module=module)


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")

