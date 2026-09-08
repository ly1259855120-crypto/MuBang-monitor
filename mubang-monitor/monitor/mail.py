from email.message import EmailMessage
from email.utils import formatdate
import hashlib
import os
import smtplib
import ssl

def send(key, subject, body):
    host = os.environ['SMTP_HOST']
    user = os.environ['SMTP_USER']
    password = os.environ['SMTP_PASSWORD']
    sender = os.getenv('MAIL_FROM') or user
    recipients = [v.strip() for v in os.environ['MAIL_TO'].split(',') if v.strip()]
    if not recipients:
        raise ValueError('MAIL_TO empty')
    mode = os.getenv('SMTP_SECURITY', 'ssl')
    if mode not in ('ssl', 'starttls'):
        raise ValueError('SMTP_SECURITY must be ssl or starttls')
    port = int(os.getenv('SMTP_PORT') or ('465' if mode == 'ssl' else '587'))
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = ', '.join(recipients)
    msg['Date'] = formatdate(localtime=False)
    msg['Message-ID'] = f'<{hashlib.sha256(key.encode()).hexdigest()}@mubang-monitor.local>'
    msg.set_content(body)
    context = ssl.create_default_context()
    connection = smtplib.SMTP_SSL(host, port, timeout=30, context=context) if mode == 'ssl' else smtplib.SMTP(host, port, timeout=30)
    with connection as smtp:
        if mode == 'starttls':
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
        smtp.login(user, password)
        refused = smtp.send_message(msg, from_addr=sender, to_addrs=recipients)
        if refused:
            raise RuntimeError('SMTP refused one or more recipients')
