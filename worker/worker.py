"""GigBid worker (arq on Redis). Run:  arq worker.worker.WorkerSettings   (or: python -m arq worker.WorkerSettings)

It does everything that used to run inside web requests:
  run_job          analyse one job through the bid graph, up to the approval pause
  resume_job       apply an approve / edit / reject decision (submit happens here, exactly once)
  run_conversation run the conversation graph for a client message
  scout_user       run a user's saved searches, register fresh jobs, queue one run_job per job
  scout_all        cron: scouting for every user every `scout.interval_minutes` (15 min mock, 60 min live)

Nothing secret travels through Redis: jobs carry user id, job id and a trace id. The worker loads each user's
profile and encrypted marketplace token from the database itself (same code as the API: apps/api/bootstrap.py).
All state is in Postgres, so killing the worker mid-run is safe: the next attempt continues from the checkpoint.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_here = Path(__file__).resolve()
for _root in _here.parents[:3]:
    for _cand in (_root / "apps" / "api", _root):
        if _cand.is_dir() and str(_cand) not in sys.path and ((_cand / "bootstrap.py").exists() or (_cand / "agent").is_dir()):
            sys.path.insert(0, str(_cand))

from arq import cron  # noqa: E402
from arq.connections import RedisSettings  # noqa: E402
from arq.worker import func  # noqa: E402

import yaml  # noqa: E402

from bootstrap import build_runtime, resolve_config_path  # noqa: E402  (loads .env, sets sys.path)
import tasks  # noqa: E402

with open(resolve_config_path()) as _fh:
    SCOUT_EVERY = tasks.scout_interval_minutes(yaml.safe_load(_fh))      # config.yaml -> scout.interval_minutes


async def startup(ctx) -> None:
    ctx["rt"] = await asyncio.to_thread(build_runtime)
    ctx["loop"] = asyncio.get_running_loop()
    print(f"[GigBid worker] ready (durable={ctx['rt'].durable})")


async def shutdown(ctx) -> None:
    rt = ctx.get("rt")
    if rt is not None:
        await asyncio.to_thread(rt.close)


def _spawner(ctx):
    """Lets the synchronous task code queue follow-up jobs on the worker's own Redis connection."""
    def spawn(name, *args, job_id=None):
        fut = asyncio.run_coroutine_threadsafe(ctx["redis"].enqueue_job(name, *args, _job_id=job_id), ctx["loop"])
        return fut.result(10) is not None
    return spawn


def _make(name: str):
    async def job(ctx, *args):
        return await asyncio.to_thread(tasks.run_task, ctx["rt"], name, args, _spawner(ctx))
    job.__name__ = name
    return func(job, name=name, keep_result=0, max_tries=3)     # keep_result=0: the same id may be queued again later


class WorkerSettings:
    functions = [_make(n) for n in tasks.TASK_NAMES]
    cron_jobs = [cron(_make("scout_all").coroutine, name="scout_all", unique=True,
                      minute=set(range(0, 60, SCOUT_EVERY)))]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://localhost:6379"))
    max_jobs = int(os.getenv("WORKER_CONCURRENCY", 4))
    job_timeout = int(os.getenv("WORKER_JOB_TIMEOUT", 900))
    keep_result = 0
