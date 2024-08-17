import logging
from pathlib import Path

from celery import chain, chord, group, shared_task

from orca import _config
from orca.model import Corpus, create_tables, get_redis_client, with_session
from orca.tasks.celery import celery  # noqa: F401 -- need this here for export
from orca.tasks.exporters import create_megadoc, upload_megadoc
from orca.tasks.loaders import index_documents, load_documents
from orca.tasks.searchers import run_search

log = logging.getLogger(__name__)
r = get_redis_client()


def reset_db():
    """Reset database, create metadata.

    This needs to be run at least once before anything else happens.
    """
    log.info(f"Flushing Redis database: {_config.REDIS_SOCKET}")
    r.flushdb(asynchronous=True)
    log.info(f"Deleting database file: {_config.DATABASE_PATH}")
    _config.DATABASE_PATH.unlink()
    log.info("Creating database and setting up table metadata")
    create_tables()
    log.info("Reset complete")


@shared_task
def reset_lock(request, exc, traceback):
    """Reset loading flag in Redis."""
    r.hset("orca:flags", "loading", int(False))


def start_load(path):
    """Load documents from path into the database.

    Each document needs to be in an album subdirectory and named according to
    the schema laid out in `Image.create_from_file()`.

    We start a Redis chord with one `load_documents()` task per subdirectory,
    then finish by creating a `Corpus` snapshot and building a Whoosh index
    with `index_documents()`.
    """

    if r.hget("orca:flags", "loading") == b"1":
        raise RuntimeError("Tried loading documents but process already running")

    if not (path := Path(path)).is_dir():
        raise IOError(f"Bad path: {path}")

    if not (subdirs := [p for p in path.iterdir() if p.is_dir()]):
        raise IOError(f"No albums in path: {path}")

    r.hset("orca:flags", "loading", int(True))
    return chord([load_documents.s(str(p)).on_error(reset_lock.s()) for p in subdirs])(
        chain(index_documents.s().on_error(reset_lock.s()), reset_lock.s())
    )


@with_session
def get_overview(session=None):
    return {
        "api_version": _config.APP_VERSION,
        "corpus": Corpus.get_latest(session=session).as_dict(),
    }


def start_search(search_str):
    """ """
    megadoc_tasks = group(
        chain(create_megadoc.s(filetype), upload_megadoc.s())
        for filetype in _config.MEGADOC_FILETYPES
    )
    return chain(run_search.s(search_str), megadoc_tasks).apply_async()
