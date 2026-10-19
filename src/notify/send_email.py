"""Send one HTML email (with a plain-text part) through an SMTP account.

    python -m src.notify.send_email --subject-file S --html H --text T

Account and recipients come from the environment, never the command line or the
repo: SMTP_USER (the sending address, e.g. justin@jimoody.com), SMTP_PASSWORD (a
Google Workspace app password), SMTP_HOST / SMTP_PORT (default smtp.gmail.com:587,
STARTTLS), MAIL_TO and optional MAIL_CC (comma-separated). Prints only that it
sent and to how many people: the scheduled run's logs are public.
"""

from __future__ import annotations

import argparse
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path


def addresses(name: str) -> list[str]:
    return [a.strip() for a in (os.environ.get(name) or "").split(",") if a.strip()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject-file", type=Path, required=True)
    ap.add_argument("--html", type=Path, required=True)
    ap.add_argument("--text", type=Path, required=True)
    a = ap.parse_args()

    user, password = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
    to, cc = addresses("MAIL_TO"), addresses("MAIL_CC")
    if not (user and password and to):
        raise SystemExit("SMTP_USER, SMTP_PASSWORD and MAIL_TO must be set.")

    msg = EmailMessage()
    msg["Subject"] = a.subject_file.read_text(encoding="utf-8").strip()
    msg["From"] = os.environ.get("MAIL_FROM") or user
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg.set_content(a.text.read_text(encoding="utf-8"))
    msg.add_alternative(a.html.read_text(encoding="utf-8"), subtype="html")

    host, port = os.environ.get("SMTP_HOST") or "smtp.gmail.com", int(os.environ.get("SMTP_PORT") or 587)
    with smtplib.SMTP(host, port, timeout=60) as s:
        s.starttls()
        s.login(user, password)
        s.send_message(msg)
    print(f"sent to {len(to) + len(cc)} recipient(s)", file=sys.stderr)


if __name__ == "__main__":
    main()
