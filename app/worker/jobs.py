from app.domains.exports.service import export_tasks
from app.domains.reminders.service import scan_due_tasks, send_due_reminder
from app.domains.attachments.service import cleanup_attachments

from app.domains.reminders.service import report_queue_depth

ALL_JOBS = [
    send_due_reminder,
    scan_due_tasks,
    export_tasks,
    cleanup_attachments,
    report_queue_depth,
]
