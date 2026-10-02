from app.domains.exports.service import export_tasks
from app.domains.reminders.service import scan_due_tasks, send_due_reminder

ALL_JOBS = [send_due_reminder, scan_due_tasks, export_tasks]