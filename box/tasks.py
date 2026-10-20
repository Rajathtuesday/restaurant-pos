import logging

from celery import shared_task

logger = logging.getLogger("pos.box")


@shared_task(name="box.tasks.nightly_backup", acks_late=True)
def nightly_backup():
    """The box's nightly backup (only scheduled on a box: core/settings.py)."""
    from box.backup import BackupError, run_backup
    try:
        path = run_backup()
        logger.info("Box backup made: %s", path)
        return str(path)
    except BackupError as e:
        logger.error("Box backup failed: %s", e)
        return None


@shared_task(name="box.tasks.print_queued_job", queue="printing", acks_late=True)
def print_queued_job(job_id):
    """A print job on a box, sent straight to its printer (box/print_queue.py)."""
    from box.print_queue import print_job
    return print_job(job_id)
