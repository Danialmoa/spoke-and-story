import os

# app.main loads .env; without this a real SENTRY_DSN there would send test traffic to Sentry.
os.environ["SENTRY_DSN"] = ""
