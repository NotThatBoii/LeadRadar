import logging

from pydantic import ValidationError

from leadradar.collectors.http import CollectionError, HttpCollector
from leadradar.models import RunReport
from leadradar.processing.normalize import normalize
from leadradar.sources import Source
from leadradar.storage.database import Database

log = logging.getLogger(__name__)


def collect(
    source: Source, database: Database, limit: int, http: HttpCollector | None = None
) -> RunReport:
    if not 1 <= limit <= 500:
        raise ValueError("Collection limit must be between 1 and 500")
    report = RunReport(source=source.name)
    database.start_run(report)
    try:
        for raw in source.collect(limit):
            if report.processed >= limit:
                report.warn("Adapter exceeded limit; remaining records ignored")
                break
            report.processed += 1
            try:
                business = normalize(raw)
                outcome = database.upsert(business)
                setattr(report, outcome, getattr(report, outcome) + 1)
                for warning in business.warnings:
                    report.warn(warning)
            except (ValidationError, ValueError) as exc:
                report.failed += 1
                if len(report.errors) < 100:
                    # Do not serialize record contents into logs/error messages.
                    report.errors.append(
                        f"Record {report.processed}: {type(exc).__name__}; invalid data"
                    )
        report.status = "partial" if report.failed else "success"
    except KeyboardInterrupt:
        report.status = "interrupted"
        report.warn("Interrupted; previously committed records retained")
    except CollectionError as exc:
        report.status = "partial" if report.processed else "failed"
        report.errors.append(str(exc))
    except Exception as exc:
        report.status = "failed"
        report.errors.append(
            f"Unexpected {type(exc).__name__}; inspect source configuration/database"
        )
        log.error("collection_error", extra={"details": {"type": type(exc).__name__}})
    finally:
        if http:
            report.requests = http.requests
            for warning in http.warnings:
                report.warn(warning)
        database.finish_run(report)
    log.info(
        "collection_finished",
        extra={
            "details": {"run_id": report.id, "status": report.status, "processed": report.processed}
        },
    )
    return report
