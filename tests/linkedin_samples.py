"""Synthetic, anonymized LinkedIn Job Alert emails for tests.

Addresses and tracking tokens are fake. Tests on real emails, if any, live in the gitignored tests/test_local_*.py.
"""
from email.message import EmailMessage

SENDER = "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>"
RECIPIENT = "user@example.com"


def job_link(job_id: str, slug: str = "") -> str:
    path = f"{slug}-{job_id}" if slug else job_id
    return (
        f"https://www.linkedin.com/comm/jobs/view/{path}/?trackingId=AAAAtoken%3D%3D"
        f"&refId=BBBBtoken&lipi=urn%3Ali%3Apage%3Aemail&midToken=CCCC&trk=eml-email_job_alert&eid=xxx"
    )


def html_card(job_id: str, title: str, company_line: str, labels=("Easy Apply",)) -> str:
    label_rows = "".join(f"<tr><td><span>{label}</span></td></tr>" for label in labels)
    return f"""
    <table role="presentation"><tr><td>
      <table><tr>
        <td><a href="{job_link(job_id)}"><img src="https://media.example.com/logo.png" alt="Company logo"></a></td>
        <td>
          <table>
            <tr><td><a href="{job_link(job_id)}">{title}</a></td></tr>
            <tr><td><p>{company_line}</p></td></tr>
            {label_rows}
          </table>
        </td>
      </tr></table>
    </td></tr></table>
    """


def alert_email(message_id: str, subscription: str, jobs, html: bool = True, plain: bool = True, header: str = "") -> EmailMessage:
    """jobs: list of (job_id, title, 'Company · Location (Format)')."""
    msg = EmailMessage()
    msg["From"] = SENDER
    msg["To"] = RECIPIENT
    msg["Subject"] = jobs[0][1] if jobs else "Job alert"
    msg["Date"] = "Mon, 05 Oct 2026 08:00:00 +0000"
    msg["Message-ID"] = message_id
    header = header or f"Your job alert for {subscription}"
    text_lines = [header, "", "New jobs match your preferences.", ""]
    for job_id, title, company_line in jobs:
        text_lines += [title, company_line, "Easy Apply", f"View job: {job_link(job_id)}", "", "-" * 20, ""]
    text_lines += ["Unsubscribe: https://www.linkedin.com/comm/jobs/alerts?token=DDDD"]
    if plain:
        msg.set_content("\n".join(text_lines))
    if html:
        cards = "".join(html_card(*job) for job in jobs)
        body = f"""<html><head><style>.x{{color:red}}</style></head><body>
        <table><tr><td><h2>{header}</h2></td></tr></table>
        {cards}
        <table><tr><td><a href="https://www.linkedin.com/comm/jobs/search?keywords=x&trk=eml">See all jobs</a></td></tr></table>
        <p><a href="https://www.linkedin.com/comm/jobs/alerts?token=DDDD">Unsubscribe</a></p>
        </body></html>"""
        if plain:
            msg.add_alternative(body, subtype="html")
        else:
            msg.set_content(body, subtype="html")
    return msg


class FakeIMAP:
    """Minimal imaplib.IMAP4 stand-in: serves messages by UID, records flag changes."""

    def __init__(self, messages):
        self.messages = {str(i + 1).encode(): m.as_bytes() for i, m in enumerate(messages)}
        self.stored = []
        self.readonly = None
        self.searches = []

    def __call__(self, host):
        return self

    def login(self, user, password):
        return "OK", [b"logged in"]

    def select(self, mailbox, readonly=False):
        self.readonly = readonly
        return "OK", [str(len(self.messages)).encode()]

    def logout(self):
        return "BYE", []

    def uid(self, command, *args):
        command = command.upper()
        if command == "SEARCH":
            self.searches.append(args[-1])
            return "OK", [b" ".join(self.messages)]
        if command == "FETCH":
            uid, item = args
            raw = self.messages[uid]
            if "HEADER.FIELDS" in item:
                header = raw.split(b"\n\n", 1)[0].split(b"\r\n\r\n", 1)[0]
                lines = [l for l in header.splitlines() if l.lower().startswith(b"message-id:")]
                payload = b"\r\n".join(lines) + b"\r\n\r\n"
            else:
                payload = raw
            return "OK", [(b"%s (UID %s BODY[] {%d}" % (uid, uid, len(payload)), payload), b")"]
        if command == "STORE":
            self.stored.append(args)
            return "OK", []
        raise AssertionError(f"unexpected IMAP command {command}")
