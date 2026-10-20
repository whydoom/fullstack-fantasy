"""Email Justin when a Dagster run fails -- the Tuesday refresh or any other job.

A run-failure sensor: Dagster's daemon checks for failed runs and, for each, sends
one email through the same Gmail sender the Monday talking points use
(src/notify/send_email.py), from .env on this machine:

    SMTP_USER=justin@jimoody.com
    SMTP_PASSWORD=<Google app password>
    ALERT_TO=justin@jimoody.com      # optional; defaults to SMTP_USER

The email names the job, when it started, the step that failed and the first lines
of the error, with the run's id for finding it in the Dagster UI. If the email
itself cannot be sent, the sensor logs why instead of failing silently.
"""

from __future__ import annotations

import os

from dagster import DefaultSensorStatus, RunFailureSensorContext, run_failure_sensor

from src.notify.send_email import send


def _failed_step(context: RunFailureSensorContext) -> str:
    """The first step that failed, if Dagster recorded one."""
    try:
        for e in context.get_step_failure_events():
            return e.step_key or ""
    except Exception:          # older runs / runs that failed outside a step
        pass
    return ""


@run_failure_sensor(default_status=DefaultSensorStatus.RUNNING, name="email_on_run_failure",
                    description="Email Justin whenever a run fails (the Tuesday refresh included).")
def email_on_run_failure(context: RunFailureSensorContext):
    run = context.dagster_run
    step = _failed_step(context)
    message = (context.failure_event.message or "").strip()
    lines = [
        f"The Dagster run for {run.job_name} failed.",
        "",
        f"Job:   {run.job_name}",
        f"Run:   {run.run_id}",
        f"Step:  {step or '(not recorded)'}",
        "",
        "Error (first lines):",
        *message.splitlines()[:25],
        "",
        "Open the Dagster UI (dagster dev, then Runs) and search for the run id to see the full log.",
    ]
    to = os.environ.get("ALERT_TO") or os.environ.get("SMTP_USER") or ""
    try:
        send(subject=f"[FFF] {run.job_name} failed{f' at {step}' if step else ''}",
             text="\n".join(lines), to=[a.strip() for a in to.split(",") if a.strip()])
    except SystemExit as e:
        context.log.error(f"Could not email the failure: {e}")
