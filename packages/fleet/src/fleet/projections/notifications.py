"""Changes in worker reports for notification adapters."""
from datetime import datetime


class Notifications:
    def __init__(self):
        self.known = {}
        self.down = {}
        self.first_pass = True

    def update(self, reports):
        events = []
        for report in reports:
            if getattr(report, "error", None):
                if report.host.name not in self.down:
                    self.down[report.host.name] = datetime.now().astimezone().isoformat()
                    events.append(dict(kind='host_down', host=report.host.name, since=self.down[report.host.name], error=report.error))
                continue
            if report.host.name in self.down:
                self.down.pop(report.host.name)
                events.append(dict(kind='host_up', host=report.host.name))
            for job in report.jobs:
                reference = f"{report.host.name}:{job['id']}"
                for step in job['steps']:
                    key = f"{reference}#{step['index']}"
                    if self.known.get(key) != step['status']:
                        if not self.first_pass and step['status'] in ('done', 'failed', 'blocked', 'cancelled', 'running'):
                            events.append(dict(kind='step', reference=reference, step=step, total=len(job['steps'])))
                        self.known[key] = step['status']
                if self.known.get(reference) != job['status']:
                    if not self.first_pass and job['status'] in ('done', 'failed', 'blocked', 'cancelled', 'stalled', 'lost'):
                        events.append(dict(kind='job', reference=reference, job=job))
                    self.known[reference] = job['status']
        self.first_pass = False
        return events
