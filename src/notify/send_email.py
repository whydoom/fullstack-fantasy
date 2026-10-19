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

    # Google shows an app password as four groups ("abcd efgh ijkl mnop"); spaces or
    # a stray newline pasted into the secret break the login, so strip them.
    user = (os.environ.get("SMTP_USER") or "").strip()
    password = "".join((os.environ.get("SMTP_PASSWORD") or "").split())
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

    host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
    errors = []
    # STARTTLS on 587, then implicit TLS on 465: Gmail takes either, and a network
    # that drops one usually passes the other.
    for port, tls in ((int(os.environ.get("SMTP_PORT") or 587), "starttls"), (465, "ssl")):
        try:
            if tls == "ssl":
                s = smtplib.SMTP_SSL(host, port, timeout=60)
            else:
                s = smtplib.SMTP(host, port, timeout=60)
                s.ehlo()
                s.starttls()
                s.ehlo()
            with s:
                s.login(user, password)
                s.send_message(msg)
            print(f"sent to {len(to) + len(cc)} recipient(s) via {host}:{port}", file=sys.stderr)
            return
        except smtplib.SMTPAuthenticationError as e:
            # Gmail's code and reason (no credentials in it): 534 = app password
            # needed, 535 = user name and password not accepted
            raise SystemExit(f"SMTP login refused ({e.smtp_code}): "
                             f"{e.smtp_error.decode(errors='replace')[:200]}")
        except (smtplib.SMTPException, OSError) as e:
            errors.append(f"{port}/{tls}: {type(e).__name__}: {e}")
    raise SystemExit("Could not send. " + " | ".join(errors))


if __name__ == "__main__":
    main()
