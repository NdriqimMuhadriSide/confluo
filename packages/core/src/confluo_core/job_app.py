"""Build the process's Procrastinate app from core and module blueprints."""

import procrastinate

from confluo_core import webhooks  # noqa: F401  (registers process_inbound_event)
from confluo_core.jobs import TaskSet, core_tasks, create_job_app
from confluo_core.modules import ConfluoModule


def build_job_app(
    modules: dict[str, ConfluoModule], extra: dict[str, TaskSet] | None = None
) -> procrastinate.App:
    task_sets = {"core": core_tasks}
    task_sets |= {k: m.tasks for k, m in modules.items() if m.tasks is not None}
    return create_job_app(task_sets | (extra or {}))
